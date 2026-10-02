"""Download evidence: exposed counts, actual file properties, and comparisons."""
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit


@dataclass
class DownloadResult:
    notes: list = field(default_factory=list)
    expected_count: int | None = None
    variants: dict = field(default_factory=dict)
    live_pairs: list = field(default_factory=list)
    missing_motion: int = 0


def reference(url):
    parsed = urlsplit(url)
    value = (parsed.hostname or "") + parsed.path
    if parsed.path == "/watch" and (parsed.hostname or "").endswith("youtube.com"):
        value += "?v=" + parse_qs(parsed.query).get("v", [""])[0]
    return value


def inspect_files(files, variants=None):
    tool = shutil.which("ffprobe")
    records = []
    for path in files:
        checksum = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                checksum.update(chunk)
        record = {"file": path.name, "bytes": path.stat().st_size, "sha256": checksum.hexdigest(),
                  "kind": "video" if path.suffix.lower() in {".mp4", ".mov", ".m4v"} else "image",
                  "source": (variants or {}).get(path.name, "engine_selected"),
                  "width": None, "height": None, "codec": None, "audio": None,
                  "duration": None, "bit_rate": None, "frame_rate": None, "probe_ok": None}
        if tool:
            process = None
            try:
                process = subprocess.Popen([tool, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                output, _ = process.communicate(timeout=20)
                data = json.loads(output) if process.returncode == 0 else {}
                streams = data.get("streams", [])
                video = next((s for s in streams if s.get("codec_type") == "video"), {})
                record.update(width=video.get("width"), height=video.get("height"), codec=video.get("codec_name"),
                              audio=any(s.get("codec_type") == "audio" for s in streams),
                              duration=data.get("format", {}).get("duration"),
                              bit_rate=data.get("format", {}).get("bit_rate"),
                              frame_rate=video.get("avg_frame_rate"),
                              probe_ok=process.returncode == 0 and bool(video.get("width") and video.get("height")))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()
                record["probe_ok"] = False
        records.append(record)
    return records


def quality_summary(records):
    descriptions = []
    for record in records:
        parts = [f"{record['width']}×{record['height']}" if record.get('width') and record.get('height') else "Dimensions unavailable"]
        if record.get('codec'):
            parts.append(record['codec'])
        if record.get('kind') == 'video':
            try:
                numerator, denominator = map(int, record.get('frame_rate', '').split('/'))
                if denominator and numerator:
                    parts.append(f"{numerator / denominator:g} fps")
            except (AttributeError, ValueError):
                pass
            try:
                if float(record.get('bit_rate')) > 0:
                    parts.append(f"{float(record['bit_rate']) / 1_000_000:.2f} Mbps")
            except (TypeError, ValueError):
                pass
            if record.get('audio') is not None:
                parts.append('audio' if record['audio'] else 'no audio')
        descriptions.append(' · '.join(parts))
    counts = Counter(descriptions)
    return "; ".join(description + (f" ({count} files)" if count > 1 else "") for description, count in counts.items()) or "No media"


def gallery_evidence(directory, domain, remove=True):
    """Read only known extractor fields; never copy engine URLs or account data."""
    metadata = {}
    for path in directory.glob("*.json"):
        try:
            item = json.loads(path.read_text())
        except (ValueError, OSError):
            raise RuntimeError("The downloader returned unreadable gallery metadata; files have been kept.") from None
        if not isinstance(item, dict):
            raise RuntimeError("The downloader returned unreadable gallery metadata; files have been kept.")
        metadata[path.name[:-5]] = item
        if not (directory / path.name[:-5]).is_file():
            raise RuntimeError("The downloader is missing a gallery file described by its metadata; files have been kept.")
    evidence = DownloadResult()
    if domain in {"x.com", "instagram.com", "pinterest.com", "weibo.com"} and metadata:
        groups = {}
        for name, item in metadata.items():
            post = item.get('status', {}) if domain == "weibo.com" else item
            identity = post.get({"x.com": "tweet_id", "instagram.com": "post_id"}.get(domain, "id"))
            count, number = post.get('count'), item.get('num')
            if identity is not None and type(count) is int and type(number) is int:
                group = groups.setdefault(str(identity), {'count': count, 'items': {}})
                if count != group['count']:
                    raise RuntimeError("The downloader returned inconsistent gallery counts; files have been kept.")
                group['items'][number] = (name, item)
        if groups and sum(len(g['items']) for g in groups.values()) == len(metadata):
            for group in groups.values():
                if set(group['items']) != set(range(1, group['count'] + 1)):
                    raise RuntimeError("The gallery is incomplete according to its source count; files have been kept.")
                if domain == "weibo.com":
                    for number, (name, item) in sorted(group['items'].items()):
                        if item.get('type') == 'livephoto' and Path(name).suffix.lower() not in {'.mp4', '.mov', '.m4v'}:
                            motion = group['items'].get(number + 1)
                            if not motion or motion[1].get('type') != 'livephoto' or Path(motion[0]).suffix.lower() not in {'.mp4', '.mov', '.m4v'}:
                                raise RuntimeError("A Weibo Live Photo is missing its motion track; files have been kept.")
                            evidence.live_pairs.append((name, motion[0]))
            evidence.expected_count = sum(group['count'] for group in groups.values())
    if domain == "bilibili.com" and metadata:
        counts = {m.get("count") for m in metadata.values() if type(m.get("count")) is int}
        if len(counts) == 1:
            count = counts.pop()
            images = {m.get("num") for name, m in metadata.items() if Path(name).suffix.lower() not in {".mp4", ".mov", ".m4v"}}
            if images != set(range(1, count + 1)):
                raise RuntimeError(f"The Bilibili gallery is incomplete: {len(images)} / {count} pictures. Files have been kept.")
            pairs = {}
            for name, item in metadata.items():
                if item.get("live_url"):
                    pair = pairs.setdefault(item.get("num"), {})
                    pair["video" if item.get("suffix") == "l" else "image"] = name
            for _, pair in sorted(pairs.items()):
                if set(pair) != {"image", "video"}:
                    raise RuntimeError("A Bilibili Live Photo is missing its picture or motion track; files have been kept.")
                evidence.live_pairs.append((pair["image"], pair["video"]))
            evidence.expected_count = count + len(pairs)
    if remove:
        for path in directory.glob("*.json"):
            path.unlink()
    return evidence


def compare_reports(first, second):
    if not isinstance(first, dict) or not isinstance(second, dict) or first.get("schema") != 1 or second.get("schema") != 1:
        raise ValueError("Use two linkget --report JSON files.")
    a, b = first.get("items", []), second.get("items", [])
    if not isinstance(a, list) or not isinstance(b, list) or not all(isinstance(row, dict) for row in a + b):
        raise ValueError("Use two linkget --report JSON files.")
    if len(a) != len(b) or [r.get("reference") for r in a] != [r.get("reference") for r in b]:
        raise ValueError("Compare the same links in the same order.")
    rows = []
    for left, right in zip(a, b):
        if left.get("status") != "completed" or right.get("status") != "completed":
            rows.append({"result": "One or both downloads failed; no quality conclusion"})
            continue
        lf, rf = left["files"], right["files"]
        same = sorted(r["sha256"] for r in lf) == sorted(r["sha256"] for r in rf)
        rows.append({"result": "Identical media bytes" if same else "Different media; visual comparison needed",
                     "first": quality_summary(lf), "second": quality_summary(rf),
                     "first_count": len(lf), "second_count": len(rf),
                     "same_bytes": same})
    return rows


def write_report(path, version, items, code):
    # Explicit opt-in only. Do not overwrite an existing comparison reference.
    with Path(path).expanduser().open("x") as output:
        json.dump({"schema": 1, "version": version, "exit_code": code, "items": items,
                   "highest_quality": "not_verified", "watermark": "not_verified"}, output, ensure_ascii=False, indent=2)
        output.write("\n")
