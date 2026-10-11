"""Prepare explicit source pairs without changing their downloaded originals."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

if __package__:
    from .photos import digest
else:
    from photos import digest


def prepare_pairs(files, pairs, directory):
    if not pairs:
        return []
    if sys.platform != "darwin":
        raise RuntimeError("Native Live Photo pairing requires macOS. Omit --live-photos to save the separate originals.")
    exiftool = shutil.which("exiftool")
    if not exiftool:
        raise RuntimeError("Native Live Photo pairing needs exiftool. Run brew install exiftool, then retry.")
    directory.mkdir()
    helper = directory / ".livephoto-helper"
    commands = [["/usr/bin/xcrun", "swiftc", str(Path(__file__).with_name("livephoto.swift")), "-o", str(helper)]]
    sources = {path.name: path for path in files}
    outputs = []
    for image_name, video_name in pairs:
        image, video = sources[image_name], sources[video_name]
        checksum = hashlib.sha256((digest(image) + digest(video)).encode()).hexdigest()
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "linkget:" + checksum)).upper()
        photo = directory / ("linkget-live-" + checksum + image.suffix.lower())
        movie = photo.with_suffix(".mov")
        if photo.exists() and movie.exists():
            outputs.append((photo, movie))
            continue
        template = directory / ".metadata.jpg"
        # The image is copied first; ExifTool edits only the copy's maker notes.
        shutil.copyfile(image, photo)
        if commands:
            run(commands.pop())
        run([str(helper), "template", str(image), str(template), identifier])
        run([exiftool, "-overwrite_original", "-TagsFromFile", str(template), "-MakerNotes", str(photo)])
        run([str(helper), "movie", str(video), str(movie), identifier])
        # AVAssetWriter stamps the packaging time into container headers. These
        # are not capture dates; normalize them so a repeated export deduplicates.
        dates = ("QuickTime:CreateDate", "QuickTime:ModifyDate", "TrackCreateDate",
                 "TrackModifyDate", "MediaCreateDate", "MediaModifyDate")
        run([exiftool, "-overwrite_original", *[f"-{key}=0000:00:00 00:00:00" for key in dates], str(movie)])
        run([str(helper), "validate", str(photo), str(movie)])
        outputs.append((photo, movie))
    return outputs


def run(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Live Photo packaging could not finish. Original downloads have been kept.") from None
    if result.returncode:
        raise RuntimeError("Live Photo packaging failed. Original downloads have been kept.\n" + result.stderr.strip())


def save_pairs(pairs, folder):
    """A filename collision selects one suffix for both components."""
    folder.mkdir(parents=True, exist_ok=True)
    saved, skipped = [], 0
    for image, video in pairs:
        suffix = 0
        while True:
            stem = image.stem + (f"-{suffix}" if suffix else "")
            targets = [folder / (stem + image.suffix), folder / (stem + video.suffix)]
            if all(p.is_file() for p in targets) and all(digest(a, fresh=True) == digest(b, fresh=True) for a, b in zip((image, video), targets)):
                skipped += 2
                break
            if any(p.exists() for p in targets):
                suffix += 1
                continue
            created = []
            try:
                for source, target in zip((image, video), targets):
                    with target.open("xb") as output:
                        created.append(target)
                        with source.open("rb") as input_file:
                            shutil.copyfileobj(input_file, output)
                    if digest(source, fresh=True) != digest(target, fresh=True):
                        raise RuntimeError("Saved Live Photo verification failed; temporary files have been kept.")
                saved.extend((image, video))
                break
            except BaseException:
                for target in created:
                    target.unlink(missing_ok=True)
                raise
    return saved, skipped


def retain_pairs(pairs, root):
    """Keep stable pair basenames for Photos, including referenced libraries."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    retained = []
    for pair in pairs:
        for source in pair:
            target = root / source.name
            if target.exists():
                if digest(target) != digest(source):
                    raise RuntimeError("A retained Live Photo component failed its content check. Downloads have been kept.")
            else:
                created = False
                try:
                    with target.open("xb") as output:
                        created = True
                        with source.open("rb") as input_file:
                            shutil.copyfileobj(input_file, output)
                    target.chmod(0o600)
                except BaseException:
                    if created:
                        target.unlink(missing_ok=True)
                    raise
            retained.append(target)
    return retained
