"""Prepare explicit source pairs without changing their downloaded originals."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

if __package__:
    from .hashing import digest
    from . import ownership
else:
    from hashing import digest
    import ownership


def _transfers():
    if __package__:
        from . import transfers
    else:
        import transfers
    return transfers


def _proof(files, path):
    if isinstance(files, ownership.FileList):
        return files.proof(path)
    return _transfers().proof_for(path)


def _verify(proofs):
    for proof in proofs:
        proof.verify()


def _remove_created(proofs):
    for proof in reversed(proofs):
        try:
            proof.remove()
        except (OSError, ValueError, RuntimeError):
            pass  # A replacement or uncertain file is kept, never re-authorized.


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
    outputs, produced, inputs = [], {}, []
    for image_name, video_name in pairs:
        image, video = sources[image_name], sources[video_name]
        image_proof, video_proof = _proof(files, image), _proof(files, video)
        inputs.extend((image_proof, video_proof))
        checksum = hashlib.sha256((image_proof.sha256 + video_proof.sha256).encode()).hexdigest()
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "linkget:" + checksum)).upper()
        photo = directory / ("linkget-live-" + checksum + image.suffix.lower())
        movie = photo.with_suffix(".mov")
        key = (photo, movie)
        if key in produced:
            pair = produced[key]
            _verify(pair.proofs.values())
            outputs.append(pair)
            continue
        if any(path.exists() or path.is_symlink() for path in key):
            raise RuntimeError("Unverified Live Photo output already exists; original downloads have been kept.")
        template = directory / ".metadata.jpg"
        # The input copy has writer evidence. External metadata tools legitimately
        # transform it below; their final outputs carry observed, non-owning
        # proofs and are retained rather than granted automatic cleanup rights.
        ownership.copy(image_proof, photo, copier=shutil.copyfileobj)
        if commands:
            run(commands.pop())
        image_proof.verify()
        run([str(helper), "template", str(image_proof.path), str(template), identifier])
        run([exiftool, "-overwrite_original", "-TagsFromFile", str(template), "-MakerNotes", str(photo)])
        video_proof.verify()
        run([str(helper), "movie", str(video_proof.path), str(movie), identifier])
        # Packaging time is not capture time. Normalization preserves repeat
        # export deduplication without claiming original camera key-frame timing.
        dates = ("QuickTime:CreateDate", "QuickTime:ModifyDate", "TrackCreateDate",
                 "TrackModifyDate", "MediaCreateDate", "MediaModifyDate")
        run([exiftool, "-overwrite_original", *[f"-{key}=0000:00:00 00:00:00" for key in dates], str(movie)])
        _verify((image_proof, video_proof))
        proofs = [ownership.FileProof.observe(photo), ownership.FileProof.observe(movie)]
        run([str(helper), "validate", str(photo), str(movie)])
        _verify(proofs)
        pair = ownership.FileList((photo, movie), proofs)
        produced[key] = pair
        outputs.append(pair)
    _verify(inputs)
    return outputs


def run(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Live Photo packaging could not finish. Original downloads have been kept.") from None
    if result.returncode:
        raise RuntimeError("Live Photo packaging failed. Original downloads have been kept.\n" + result.stderr.strip())


def save_pairs(pairs, folder):
    """A collision selects one suffix; every saved or duplicate output has proof."""
    folder.mkdir(parents=True, exist_ok=True)
    saved, skipped, inputs, outputs, created = [], 0, [], [], []
    try:
        for pair in pairs:
            created = []
            image, video = pair
            sources = [_proof(pair, image), _proof(pair, video)]
            inputs.extend(sources)
            suffix = 0
            while True:
                stem = image.stem + (f"-{suffix}" if suffix else "")
                targets = [folder / (stem + image.suffix), folder / (stem + video.suffix)]
                if all(path.is_file() and not path.is_symlink() for path in targets):
                    # Observe each existing target once. A stable hash mismatch
                    # is a collision; a changed inode/hash is an error, not a
                    # reason to re-observe and silently adopt a replacement.
                    observed = [ownership.FileProof.observe(path) for path in targets]
                    if all(proof.sha256 == source.sha256 for proof, source in zip(observed, sources)):
                        outputs.extend(observed)
                        skipped += 2
                        break
                if any(path.exists() or path.is_symlink() for path in targets):
                    suffix += 1
                    continue
                for source, target in zip(sources, targets):
                    proof = ownership.copy(source, target, copier=shutil.copyfileobj)
                    created.append(proof)
                    outputs.append(proof)
                saved.extend((image, video))
                created = []  # Keep complete earlier pairs if a later pair fails.
                break
        _verify(inputs)
        _verify(outputs)
        _transfers().record_outputs(outputs)
        _transfers().verify_outputs()
        return saved, skipped
    except BaseException:
        _remove_created(created)
        raise


def retain_pairs(pairs, root):
    """Carry proof for each retained component; observed copies are never owned."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    retained, proofs, inputs = [], [], []
    for pair in pairs:
        for source in pair:
            source_proof = _proof(pair, source)
            inputs.append(source_proof)
            target = root / source.name
            if target.exists() or target.is_symlink():
                proof = ownership.FileProof.observe(target, expected_hash=source_proof.sha256)
            else:
                # A completed retention copy is kept if another component fails;
                # ownership.copy handles only its own uncertain partial output.
                proof = ownership.copy(source_proof, target, copier=shutil.copyfileobj)
            retained.append(target)
            proofs.append(proof)
    _verify(inputs)
    _verify(proofs)
    return ownership.FileList(retained, proofs)
