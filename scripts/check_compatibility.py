"""Offline engine-boundary checks. No website requests, cookies or Photos access."""
import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from importlib import metadata, util
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from linkget import engines, media
from linkget.version import VERSION

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/compatibility/cases.json"
ENGINES = {"gallery-dl": ["--config-ignore", "--version"],
           "yt-dlp": ["--ignore-config", "--version"],
           "ffmpeg": ["-version"], "ffprobe": ["-version"]}
EXTRACTOR_MODULE = "compat_instagram_fixture"
REQUIRED_EXTRACTOR_TESTS = frozenset(
    EXTRACTOR_MODULE + ".InstagramExtractorTests." + name for name in (
        "test_missing_downloaded_photo_or_video_is_still_rejected",
        "test_mixed_gallery_music_keeps_visual_order_and_count",
        "test_single_reel_music_does_not_require_an_extra_file",
        "test_source_media_silently_skipped_by_upstream_is_rejected",
        "test_story_music_with_a_shared_number_is_excluded",
    )
)


class CoverageResult(unittest.TextTestResult):
    """Record actual executions/successes, not merely the loader's test count."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed_ids = []
        self.successful_ids = []

    def startTest(self, test):
        self.executed_ids.append(test.id())
        super().startTest(test)

    def addSuccess(self, test):
        self.successful_ids.append(test.id())
        super().addSuccess(test)


def collected_ids(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from collected_ids(case)
        else:
            yield case.id()


def safe_version(value):
    """Keep version labels, never emit arbitrary subprocess output."""
    match = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,79}", value or "")
    return match[0] if match else None


def environment(probe_engines=False):
    result = {"python": platform.python_version(), "system": platform.system(),
              "linkget": VERSION, "engine_probe_requested": probe_engines, "engines": {}}
    for name, options in ENGINES.items():
        executable = shutil.which(name)
        try:
            installed = safe_version(metadata.version(name))
        except metadata.PackageNotFoundError:
            installed = None
        row = {"executable_available": executable is not None, "python_package_version": installed,
               "executable_version": None, "probe": "not_requested" if executable else "missing"}
        if probe_engines and executable:
            try:
                process = subprocess.run([executable, *options], capture_output=True, text=True,
                                         timeout=8, stdin=subprocess.DEVNULL)
                # gallery-dl/yt-dlp emit the version alone; FFmpeg prefixes it.
                first = process.stdout.splitlines()[0] if process.stdout else ""
                candidate = first.split()[2] if name in {"ffmpeg", "ffprobe"} and len(first.split()) > 2 else first
                row["executable_version"] = safe_version(candidate)
                row["probe"] = "passed" if process.returncode == 0 and row["executable_version"] else "unrecognized_or_failed"
            except (OSError, subprocess.TimeoutExpired):
                row["probe"] = "unavailable_or_timeout"
        result["engines"][name] = row
    return result


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", value):
        raise ValueError("Invalid synthetic fixture identifier")
    return value


def fixture_checks(path=FIXTURES):
    document = json.loads(Path(path).read_text())
    if not isinstance(document, dict) or document.get("schema") != 1:
        raise ValueError("Unsupported synthetic fixture schema")
    results = []
    for case in document["routes"]:
        identifier = _identifier(case["id"])
        passed = False
        try:
            _, engine, domain = engines.route(case["url"])
            passed = (engine, domain) == (case["engine"], case["domain"])
            if engine in {"gallery-dl", "yt-dlp"}:
                tools = {name: name for name in (*ENGINES, "deno")}
                command = engines.command_for(SimpleNamespace(browser="none", profile=None),
                                              case["url"], engine, domain, tools=tools, media_dir=Path("synthetic-media"))
                passed = passed and command[0] == engine and command[-1] == case["url"] and "--cookies" not in command
        except (KeyError, TypeError, ValueError):
            pass
        results.append({"id": identifier, "status": "passed" if passed else "failed",
                        "layer": "linkget_route_or_command_contract"})
    for case in document["galleries"]:
        identifier = _identifier(case["id"])
        passed = False
        with tempfile.TemporaryDirectory(prefix="linkget-compat-") as temporary:
            directory = Path(temporary)
            for file in case["files"]:
                name = file["name"]
                if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]+\.(?:jpg|mov|mp4)", name):
                    raise ValueError("Invalid synthetic fixture filename")
                (directory / name).write_bytes(b"synthetic format-not-validated fixture")
                (directory / (name + ".json")).write_text(json.dumps(file["metadata"]))
            try:
                evidence = media.gallery_evidence(directory, case["domain"], remove=False)
                passed = (case["outcome"] == "accept" and evidence.expected_count == case["expected_count"]
                          and [list(pair) for pair in evidence.live_pairs] == case["live_pairs"])
            except RuntimeError as error:
                passed = case["outcome"] == "reject" and case["error_contains"] in str(error)
        results.append({"id": identifier, "status": "passed" if passed else "failed",
                        "layer": "linkget_metadata_contract"})
    return results


def extractor_checks():
    """Reuse the existing synthetic gallery-dl extension suite, not its network routes."""
    spec = util.spec_from_file_location(EXTRACTOR_MODULE, ROOT / "tests/test_instagram.py")
    module = util.module_from_spec(spec)
    output = io.StringIO()
    # Upstream initialization may change. Enforce the offline boundary even
    # if a future extractor starts resolving or requesting before its fake posts.
    denied = RuntimeError("Offline extractor checks prohibit network and external processes")
    with redirect_stdout(output), redirect_stderr(output), \
            patch("socket.getaddrinfo", side_effect=denied), \
            patch("socket.socket.connect", side_effect=denied), \
            patch("socket.socket.connect_ex", side_effect=denied), \
            patch("subprocess.Popen", side_effect=denied):
        spec.loader.exec_module(module)
        suite = unittest.defaultTestLoader.loadTestsFromModule(module)
        collected = list(collected_ids(suite))
        result = unittest.TextTestRunner(stream=output, resultclass=CoverageResult).run(suite)
    required = REQUIRED_EXTRACTOR_TESTS
    counts = Counter(collected)
    missing = sorted(required - set(collected))
    unexecuted = sorted(required - set(result.executed_ids))
    duplicate = sorted(identifier for identifier, count in counts.items() if identifier in required and count != 1)
    skipped = sorted(case.id() for case, _ in result.skipped if case.id() in required)
    successful = sorted(required & set(result.successful_ids))
    drift = bool(missing or unexecuted or duplicate)
    package_available = util.find_spec("gallery_dl") is not None
    # Discovery drift is not an absent optional dependency: report the exact
    # coverage gap before considering the known extractor-import skip.
    skip_reason = None
    if result.skipped and not drift:
        skip_reason = ("gallery_dl_python_package_unavailable" if not package_available else
                       "extractor_import_failed" if module.LinkgetInstagramPostExtractor is None else "extractor_cases_skipped")
    unsuccessful_required = required - set(successful) - set(skipped)
    return {"status": "failed" if drift or unsuccessful_required or not result.wasSuccessful() or skip_reason == "extractor_import_failed"
                      else "partial" if result.skipped else "passed",
            "tests_run": result.testsRun, "passed": len(result.successful_ids),
            "skipped": len(result.skipped),
            "skip_reason": skip_reason,
            "coverage": {"contract": 1, "required_test_ids": sorted(required),
                         "collected_test_ids": sorted(collected), "executed_test_ids": sorted(result.executed_ids),
                         "successful_required_test_ids": successful, "skipped_required_test_ids": skipped,
                         "missing_required_test_ids": missing, "unexecuted_required_test_ids": unexecuted,
                         "duplicate_required_test_ids": duplicate,
                         "problem": "coverage_drift" if drift else "required_tests_not_successful" if unsuccessful_required else None},
            "failed_test_ids": [case.id() for case, _ in result.failures + result.errors],
            "layer": "gallery_dl_or_linkget_extension", "reused_suite": "tests/test_instagram.py"}


def check(probe_engines=False):
    cases = fixture_checks()
    extractor = extractor_checks()
    return {"schema": 1, "mode": "offline", "environment": environment(probe_engines),
            "fixtures": cases, "extractor": extractor,
            "status": "failed" if any(case["status"] == "failed" for case in cases) or extractor["status"] == "failed"
                      else "partial" if extractor["status"] == "partial" else "passed",
            "platform": {"status": "not_checked", "reason": "No website requests are implemented by this command."},
            "limits": ["Synthetic bytes do not validate media decoding, maximum quality or source availability.",
                       "Command construction does not prove an installed engine accepts its options.",
                       "Offline extractor drift cannot by itself distinguish upstream changes from a linkget extension defect.",
                       "Photos import and playback are not checked."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-engines", action="store_true",
                        help="also run already-installed trusted engine version commands; no requests or installation")
    parser.add_argument("--require-extractor", action="store_true",
                        help="require every named extractor contract case to be collected and pass without skips")
    parser.add_argument("--output", type=Path, help="write a new JSON report; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        report = check(args.probe_engines)
        text = json.dumps(report, indent=2) + "\n"
        if args.output:
            descriptor = os.open(args.output.expanduser(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(text)
        sys.stdout.write(text)
        return 1 if report["status"] == "failed" or args.require_extractor and report["extractor"]["status"] != "passed" else 0
    except (OSError, ValueError, KeyError, TypeError):
        # Do not echo arbitrary file contents, engine stderr, source URLs or paths.
        sys.stderr.write("Compatibility check could not finish; check the synthetic fixture schema and choose a new report path.\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
