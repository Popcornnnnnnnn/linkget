"""Non-overwriting folder saves and verified Photos imports."""
import filecmp
from pathlib import Path
import re
import shutil
import subprocess
if __package__:
    from .photos import originals_directory
    from .live import retain_pairs
    from . import jobs
else:
    from photos import originals_directory
    from live import retain_pairs
    import jobs


def save_folder(files, folder):
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    skipped = 0
    for source in files:
        target = folder / source.name
        suffix = 1
        while True:
            if target.is_file() and filecmp.cmp(source, target, shallow=False):
                skipped += 1
                break
            created = False
            try:
                with target.open("xb") as output:
                    created = True
                    with source.open("rb") as input_file:
                        shutil.copyfileobj(input_file, output)
                saved.append(source)
                break
            except FileExistsError:
                target = folder / f"{source.stem}-{suffix}{source.suffix}"
                suffix += 1
            except BaseException:
                if created:
                    target.unlink(missing_ok=True)
                raise
    return saved, skipped



def import_photos(files, date_now, live_pairs, retain_originals, verify_library_copies):
    retained = retain_originals(files)
    staged = set(retained)
    if live_pairs:
        paired = retain_pairs(list(live_pairs.values()), originals_directory())
        staged.update(paired)
        replacements = dict(zip((name for pair in live_pairs for name in pair), paired))
        retained = [replacements.get(source.name, path) for source, path in zip(files, retained)]
    unique = list(dict.fromkeys(retained))
    source_indexes = [retained.index(path) for path in unique]
    receipt = ({"files": jobs.snapshot(unique), "staged": jobs.snapshot(sorted(staged)), "identifiers": []}
               if jobs.active() else {"identifiers": []})
    jobs.record(receipt=receipt)
    importer = Path(__file__).with_name("import.applescript")
    try:
        result = subprocess.run(["/usr/bin/osascript", str(importer),
                                 "--date-now" if date_now else "--keep-date",
                                 *map(str, unique)], text=True, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Photos import did not finish within 120 seconds. Original files have been kept.\n"
                           + "Originals are safe in: " + str(originals_directory())) from None
    if result.returncode:
        raise RuntimeError("Photos could not import the media. Allow terminal access in System Settings > Privacy & Security > Automation, or use --folder for unsupported formats.\n"
                           + "Originals are safe in: " + str(originals_directory()) + "\n" + result.stderr.strip())
    response, _, identifier_text = result.stdout.strip().partition("\n")
    if not re.fullmatch(r"\d+:\d+(?::\d+)*", response):
        raise RuntimeError("Photos returned an invalid import result. Original files have been kept.")
    saved, skipped, *indexes = map(int, response.split(":"))
    if (saved + skipped != len(unique) or saved != len(indexes)
            or len(set(indexes)) != len(indexes)
            or any(index < 1 or index > len(unique) for index in indexes)):
        raise RuntimeError("Photos returned an unexpected item count. Original files have been kept.")
    receipt["identifiers"] = identifier_text.split("\t") if identifier_text else []
    jobs.record(receipt=receipt)
    try:
        verify_library_copies(unique, identifier_text.split("\t") if identifier_text else [])
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(str(error) + "\nOriginals are safe in: " + str(originals_directory())) from error
    for path in staged:
        path.unlink(missing_ok=True)
    return [files[source_indexes[index - 1]] for index in indexes], skipped + len(files) - len(unique)
