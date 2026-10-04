"""Stage Photos imports and verify managed copies before removing source files."""

import hashlib
import base64
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile

if __package__:
    from . import accounts
else:
    import accounts


def originals_directory():
    return accounts.directory().parent / "originals"


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def retain_originals(files):
    """Keep stable import sources until managed library copies are verified."""
    root = originals_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    retained = []
    for source in files:
        checksum = digest(source)
        target = root / ("linkget-" + checksum + source.suffix.lower())
        if target.exists():
            if digest(target) != checksum:
                raise RuntimeError(f"An original failed its content check: {target}. Downloaded files have been kept.")
        else:
            with tempfile.NamedTemporaryFile(dir=root, prefix=".copy-", delete=False) as output:
                temporary = Path(output.name)
                try:
                    with source.open("rb") as input_file:
                        shutil.copyfileobj(input_file, output)
                    output.close()
                    if digest(temporary) != checksum:
                        raise RuntimeError("Original copy verification failed; downloaded files have been kept.")
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
        retained.append(target)
    return retained


def library_directory():
    # Resolve Photos' last-used library without launching an app, showing a
    # chooser, assuming ~/Pictures, or reading the library database. Query
    # preferences through defaults: directly opening Photos' container plist
    # can be denied when linkget runs from Terminal.
    preferences = subprocess.run(["/usr/bin/defaults", "export", "com.apple.Photos", "-"],
                                 text=True, capture_output=True, timeout=10)
    if preferences.returncode:
        raise RuntimeError("Could not read the Photos library preference: " + preferences.stderr.strip())
    try:
        bookmark = plistlib.loads(preferences.stdout.encode()).get("IPXDefaultLibraryURLBookmark")
    except (ValueError, plistlib.InvalidFileException) as error:
        raise RuntimeError("Photos returned an unreadable library preference.") from error
    if not isinstance(bookmark, bytes):
        raise RuntimeError("Photos has not recorded its library location.")
    script = '''ObjC.import("Foundation");
function run(argv) {
var data = $.NSData.alloc.initWithBase64EncodedStringOptions(argv[0], 0);
var stale = Ref(); var error = Ref();
var url = $.NSURL.URLByResolvingBookmarkDataOptionsRelativeToURLBookmarkDataIsStaleError(
    data, $.NSURLBookmarkResolutionWithoutUI, $(), stale, error);
if (!url.path.js) throw Error("Cannot resolve Photos library");
return url.path.js;
}'''
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script,
                             base64.b64encode(bookmark).decode("ascii")],
                            text=True, capture_output=True, timeout=10)
    path = result.stdout.strip()
    if result.returncode or not path:
        raise RuntimeError("Could not resolve the Photos library location: " + result.stderr.strip())
    if not Path(path).is_dir():
        raise RuntimeError("The Photos library is unavailable or cannot be accessed: " + path)
    return Path(path)


def verify_library_copies(files, identifiers):
    """Require identical local originals belonging to the returned Photos items.

    An import result or an export alone can also come from a referenced file.
    Check the managed originals themselves; never remove files on uncertainty.
    """
    if len(files) != len(identifiers):
        raise RuntimeError("Photos did not identify every saved item.")
    root = library_directory().resolve() / "originals"
    for source, identifier in zip(files, identifiers):
        asset = identifier.split("/")[0]
        if not re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", asset):
            raise RuntimeError("Photos returned an invalid saved item identifier.")
        asset = asset.upper()
        checksum = digest(source)
        candidates = (root / asset[0]).glob(asset + "*")
        if not any(not target.is_symlink() and target.is_file()
                   and target.stat().st_size == source.stat().st_size
                   and digest(target) == checksum for target in candidates):
            raise RuntimeError("Photos has not saved a verified copy of " + source.name
                               + ". Enable Photos > Settings > General > Copy items to the Photos library;"
                               + " use File > Consolidate for referenced items, then retry.")
