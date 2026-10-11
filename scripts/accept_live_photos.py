"""Opt-in offline/isolated-library Live Photo acceptance. Never opens Photos."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from linkget import cli, jobs, live, photos


def run(command):
    subprocess.run(command, check=True, capture_output=True, timeout=120)


def stream_hashes(path):
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a?",
                             "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-"],
                            check=True, capture_output=True, text=True, timeout=30)
    return result.stdout.splitlines()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new local evidence directory")
    parser.add_argument("--photos", action="store_true", help="import only into the already-open named isolated library")
    parser.add_argument("--library", type=Path, help="exact path to an isolated test library; required with --photos")
    args = parser.parse_args()
    if args.photos and not args.library:
        parser.error("--photos requires --library PATH to an isolated test library.")
    root = args.output.expanduser().resolve()
    if root.exists():
        parser.error("Use a new evidence directory; existing files are never overwritten.")
    if args.photos:
        expected = args.library.expanduser().resolve()
        if photos.library_directory().resolve() != expected:
            parser.error("The active Photos library is not the requested isolated test library.")
    if not shutil.which("ffmpeg"):
        parser.error("ffmpeg is required to generate the local motion sample.")
    root.mkdir(parents=True, mode=0o700)
    os.environ["LINKGET_HOME"] = str(root / "settings")
    image, motion = root / "still.jpg", root / "motion.mp4"
    run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=660:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(motion)])
    run(["ffmpeg", "-v", "error", "-i", str(motion), "-frames:v", "1", str(image)])
    originals = {path.name: photos.digest(path) for path in (image, motion)}
    first = live.prepare_pairs([image, motion], [(image.name, motion.name)], root / "first")
    second = live.prepare_pairs([image, motion], [(image.name, motion.name)], root / "second")
    if stream_hashes(motion) != stream_hashes(first[0][1]):
        raise RuntimeError("The encoded motion/audio streams changed during packaging.")
    if any(photos.digest(a) != photos.digest(b) for a, b in zip(first[0], second[0])):
        raise RuntimeError("Repeated packaging was not byte-identical.")
    if any(photos.digest(path) != originals[path.name] for path in (image, motion)):
        raise RuntimeError("A source file changed during packaging.")
    result = {"offline_recognition": "passed", "encoded_motion_audio": "identical",
              "repeat_packaging": "identical", "raw_sources": "unchanged",
              "photos_import": "not_run", "photos_duplicate_import": "not_run",
              "photos_playback": "pending_manual_acceptance", "multi_pair_source_gallery": "not_tested"}
    if args.photos:
        # The importer itself requires one Photos asset for the two pair resources.
        options = argparse.Namespace(folder=None, date_now=False, live_photos=True, browser="none", profile=None, jobs=None)
        task = jobs.Journal.create(["local-generated-live-photo"], options)
        with task.claim(), jobs.item(task, 1):
            if photos.library_directory().resolve() != expected:
                raise RuntimeError("The active Photos library changed; import was stopped.")
            source_pairs = {(image.name, motion.name): first[0]}
            imported, skipped = cli.import_photos([image, motion], False, source_pairs)
            if len(imported) != 2 or skipped:
                raise RuntimeError("The first import did not save the two resources as one new asset.")
            if list(photos.originals_directory().iterdir()):
                raise RuntimeError("Staged originals were not removed after verification.")
            imported, skipped = cli.import_photos([image, motion], False, source_pairs)
            if imported or skipped != 2:
                raise RuntimeError("The second import did not reuse the existing paired asset.")
            if list(photos.originals_directory().iterdir()):
                raise RuntimeError("Duplicate import left unverified staged originals.")
            jobs.record(status="completed")
        result.update(photos_import="passed_one_asset", photos_duplicate_import="passed_no_additional_asset",
                      managed_originals="byte_verified", staged_cleanup="passed", library=str(expected))
    report = root / "acceptance.json"
    report.write_text(json.dumps(result, indent=2) + "\n")
    report.chmod(0o600)
    print(json.dumps(result, indent=2))
    if args.photos:
        print("In the named test library, confirm the LIVE badge and moving pattern with audio. The sample is kept for review.")


if __name__ == "__main__":
    main()
