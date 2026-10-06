"""Check destination prerequisites before network or browser activity."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

if __package__:
    from .photos import originals_directory
else:
    from photos import originals_directory


def writable_directory(path):
    path = Path(path).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise ValueError("Not a folder: " + str(path))
    ancestor = path
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise ValueError("Not a folder: " + str(ancestor))
    try:
        with tempfile.TemporaryFile(dir=ancestor) as probe:
            probe.write(b"linkget")
            probe.flush()
    except OSError as error:
        raise RuntimeError(f"Cannot write to the destination: {path}. {error.strerror or error}") from error
    return path


def destination(args, find_tool):
    if args.folder is not None:
        writable_directory(args.folder)
    else:
        if sys.platform != "darwin":
            raise ValueError("Photos is available on macOS only. Use --folder to choose a destination.")
        writable_directory(originals_directory())
    if args.live_photos:
        if sys.platform != "darwin":
            raise ValueError("Native Live Photo pairing requires macOS.")
        if not find_tool("exiftool"):
            raise RuntimeError("Live Photo pairing needs exiftool. Run brew install exiftool.")
        # Do not run xcrun here: it can show an installation dialog unattended.
        selected = os.environ.get("DEVELOPER_DIR")
        if not selected:
            try:
                result = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, timeout=10)
                selected = result.stdout.strip() if result.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                selected = None
        if not selected or not any(path.is_file() for path in (
                Path(selected) / "usr/bin/swiftc", Path(selected) / "Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc")):
            raise RuntimeError("Live Photo pairing needs Apple's developer tools. Run xcode-select --install.")
