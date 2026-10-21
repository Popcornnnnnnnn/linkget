"""Non-overwriting folder saves and verified Photos imports."""
from pathlib import Path
import re
import os
import shutil
import subprocess
if __package__:
    from .photos import originals_directory
    from .hashing import digest_session
    from .live import retain_pairs
    from . import jobs, transfers, staging
else:
    from photos import originals_directory
    from hashing import digest_session
    from live import retain_pairs
    import jobs, transfers, staging


def save_folder(files, folder):
    transfers.verify(files)
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    skipped = 0
    checked_outputs = []
    for source in files:
        target = folder / source.name
        suffix = 1
        while True:
            if target.is_file() and jobs.digest(source, fresh=True) == jobs.digest(target, fresh=True):
                skipped += 1
                break
            created = False
            created_identity = None
            try:
                with target.open("xb") as output:
                    created = True
                    try:
                        with transfers.open_verified(source) as input_file:
                            shutil.copyfileobj(input_file, output)
                    finally:
                        output.flush()
                        info = os.fstat(output.fileno())
                        created_identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
                checksum = jobs.digest(source, fresh=True)
                if not transfers.matches(target, created_identity, checksum):
                    transfers.protect([source.parent.parent])
                    raise RuntimeError("Saved file verification failed; temporary files have been kept.")
                checked_outputs.append((source, target, created_identity, checksum))
                saved.append(source)
                break
            except FileExistsError:
                target = folder / f"{source.stem}-{suffix}{source.suffix}"
                suffix += 1
            except BaseException:
                if created and created_identity is not None:
                    try:
                        if transfers.file_identity(target) == created_identity:
                            staging.remove_owned(target, jobs.digest(target, fresh=True), created_identity)
                    except (OSError, ValueError, RuntimeError):
                        pass  # Retain any external replacement or uncertain copy.
                raise
    transfers.verify(files)
    for source, target, expected, checksum in checked_outputs:
        if not transfers.matches(target, expected, checksum):
            transfers.protect([source.parent.parent])
            raise RuntimeError('A saved file was replaced; temporary originals have been kept.')
    return saved, skipped



def import_photos(files, date_now, live_pairs, retain_originals, verify_library_copies):
    with digest_session():
        return _import_photos(files, date_now, live_pairs, retain_originals, verify_library_copies)


def _import_photos(files, date_now, live_pairs, retain_originals, verify_library_copies):
    transfers.verify(files)
    retained = retain_originals(files)
    transfers.verify(files)
    for source, copy in zip(files, retained):
        row = transfers.record_for(source)
        if row and jobs.digest(copy, fresh=True) != row['sha256']:
            transfers.protect([row['stage']])
            raise RuntimeError('A retained original changed; Photos import was not started.')
    staged = set(retained)
    if live_pairs:
        paired = retain_pairs(list(live_pairs.values()), originals_directory())
        staged.update(paired)
        replacements = dict(zip((name for pair in live_pairs for name in pair), paired))
        retained = [replacements.get(source.name, path) for source, path in zip(files, retained)]
    staged_records = [(path, transfers.file_identity(path), jobs.digest(path, fresh=True)) for path in staged]
    for path, expected, checksum in staged_records:
        if not transfers.matches(path, expected, checksum):
            raise RuntimeError('A retained original changed; Photos import was not started.')
    unique = list(dict.fromkeys(retained))
    first_indexes = {}
    for index, path in enumerate(retained):
        first_indexes.setdefault(path, index)
    source_indexes = [first_indexes[path] for path in unique]
    receipt = ({"files": jobs.snapshot(unique), "staged": jobs.snapshot(sorted(staged)), "identifiers": []}
               if jobs.active() else {"identifiers": []})
    jobs.record(receipt=receipt)
    importer = Path(__file__).with_name("import.applescript")
    transfers.verify(files)
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
    for path, expected, checksum in staged_records:
        staging.remove_owned(path, checksum, expected)
    return [files[source_indexes[index - 1]] for index in indexes], skipped + len(files) - len(unique)
