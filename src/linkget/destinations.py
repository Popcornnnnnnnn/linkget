"""Non-overwriting folder saves and verified Photos imports."""
from pathlib import Path
import re
import shutil
import subprocess
if __package__:
    from .photos import originals_directory
    from .hashing import digest_session
    from .live import retain_pairs
    from . import jobs, transfers, staging, ownership
else:
    from photos import originals_directory
    from hashing import digest_session
    from live import retain_pairs
    import jobs, transfers, staging, ownership


def save_folder(files, folder):
    sources = transfers.proofs_for(files)
    folder.mkdir(parents=True, exist_ok=True)
    saved, outputs = [], []
    skipped = 0
    try:
        for original, source in zip(files, sources):
            target = folder / source.path.name
            suffix = 1
            while True:
                if target.is_file():
                    existing = ownership.FileProof.observe(target)
                    if existing.sha256 == source.sha256:
                        existing.verify()
                        outputs.append(existing)  # Duplicate evidence is also durable.
                        skipped += 1
                        break
                try:
                    proof = ownership.copy(source, target, copier=shutil.copyfileobj)
                    outputs.append(proof)
                    saved.append(original)
                    break
                except FileExistsError:
                    target = folder / f"{source.path.stem}-{suffix}{source.path.suffix}"
                    suffix += 1
        for source in sources:
            source.verify()
        transfers.record_outputs(outputs)
    except BaseException:
        transfers.protect([source.path.parent.parent for source in sources])
        raise
    return saved, skipped



def import_photos(files, date_now, live_pairs, retain_originals, verify_library_copies):
    with digest_session():
        return _import_photos(files, date_now, live_pairs, retain_originals, verify_library_copies)


def _import_photos(files, date_now, live_pairs, retain_originals, verify_library_copies):
    sources = transfers.proofs_for(files)
    retained = retain_originals(files)
    if len(retained) != len(sources):
        raise RuntimeError('The original producer returned an incomplete set; import was not started.')
    proofs = {}
    def produced(container, path, expected):
        # New producers carry their writer proof. Legacy/injected callbacks may
        # only establish a read proof once, BEFORE reading their returned path.
        proof = (container.proof(path, expected) if isinstance(container, ownership.FileList)
                 else ownership.FileProof.observe(path, expected))
        previous = proofs.get(proof.path)
        if previous is not None and previous != proof:
            raise RuntimeError('The original producer changed its evidence; import was not started.')
        proofs[proof.path] = proof
        return proof
    for source, path in zip(sources, retained):
        source.verify()
        produced(retained, path, source.sha256)
    pair_inputs = []
    if live_pairs:
        for pair in live_pairs.values():
            pair_inputs.extend(transfers.proofs_for(pair))
        paired = retain_pairs(list(live_pairs.values()), originals_directory())
        source_pairs = [path for pair in live_pairs.values() for path in pair]
        if len(paired) != len(source_pairs):
            raise RuntimeError('The pair producer returned an incomplete set; import was not started.')
        for source, path in zip(pair_inputs, paired):
            source.verify()
            produced(paired, path, source.sha256)
        replacements = dict(zip((name for pair in live_pairs for name in pair), paired))
        retained = [replacements.get(source.name, path) for source, path in zip(files, retained)]
    unique = list(dict.fromkeys(retained))
    first_indexes = {}
    for index, path in enumerate(retained):
        first_indexes.setdefault(path, index)
    source_indexes = [first_indexes[path] for path in unique]
    unique_proofs = [proofs[ownership.canonical(path)] for path in unique]
    staged_proofs = [proof for proof in proofs.values() if proof.created]
    receipt = ({"files": [proof.record() for proof in unique_proofs],
                "staged": [proof.record() for proof in staged_proofs], "identifiers": []}
               if jobs.active() else {"identifiers": []})
    jobs.record(receipt=receipt)
    importer = Path(__file__).with_name("import.applescript")
    for proof in (*sources, *pair_inputs, *proofs.values()):
        proof.verify(fresh=True)
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
        managed = verify_library_copies(ownership.FileList(unique, unique_proofs), identifier_text.split("\t") if identifier_text else [])
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(str(error) + "\nOriginals are safe in: " + str(originals_directory())) from error
    # Verification consumes the same evidence; it never creates new authority.
    for proof in (*sources, *pair_inputs, *proofs.values()):
        proof.verify(fresh=True)
    if isinstance(managed, ownership.FileList):
        transfers.record_outputs(managed.proofs.values())
        for proof in staged_proofs:
            transfers.verify_outputs()
            for target in managed.proofs.values():
                target.verify(fresh=True)
            proof.remove()
    # Legacy verification callbacks returning no evidence authorize no deletion.
    return [files[source_indexes[index - 1]] for index in indexes], skipped + len(files) - len(unique)
