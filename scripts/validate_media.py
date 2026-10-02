"""Opt-in real media checks. Background only; never opens browsers or Photos.

Run with PYTHONPATH=src. Results and downloads belong in an ignored local folder.
Saved-session comparisons read existing snapshots without refreshing accounts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

from linkget import accounts, cli


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def child(case, directory, cookie):
    # Keep CLI failure evidence inside this case, never in Photos or user settings.
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'temporary').mkdir()
    tempfile.tempdir = str(directory / 'temporary')
    original_command = cli.command_for
    def command(*args, **kwargs):
        if cookie:
            kwargs['cookie_path'] = cookie
            args = args[:6]  # Override the CLI's positional cookie argument.
        value = original_command(*args, **kwargs)
        if 'gallery-dl' in Path(value[0]).name:
            return value[:-1] + ['--retries', '0', '-o', 'extractor.timeout=10'] + value[-1:]
        return value[:-1] + ['--socket-timeout', '10', '--retries', '0', '--extractor-retries', '0', '--fragment-retries', '0', '--max-filesize', '100M'] + value[-1:]
    cli.command_for = command
    if cookie:
        for name in ('download_douyin', 'download_xiaohongshu', 'download_kuaishou', 'download_pixiv'):
            original = getattr(cli, name)
            def download(url, folder, cookie_path=None, original=original):
                return original(url, folder, cookie)
            setattr(cli, name, download)
    original_prepare = cli.prepare_video
    def prepare(command, info_path, log, domain):
        try:
            return original_prepare(command, info_path, log, domain)
        finally:
            if info_path.exists():
                info = json.loads(info_path.read_text())
                keys = ('format_id', 'width', 'height', 'fps', 'vcodec', 'acodec', 'tbr', 'vbr', 'ext', 'dynamic_range')
                write_json(directory / 'source-formats.json', {
                    'formats': [{k: f.get(k) for k in keys} for f in info.get('formats', [])],
                    'scope': 'Formats exposed to this request; not proof of the platform or upload ceiling.',
                })
    cli.prepare_video = prepare
    return cli.main([case['url'], '--browser', 'none', '--folder', str(directory / 'media')])


def inspect_media(path, directory):
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, timeout=20)
    data = json.loads(probe.stdout) if probe.returncode == 0 else {}
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), {})
    is_video = path.suffix.lower() in cli.VIDEO
    item = {
        'file': str(path.relative_to(directory)), 'bytes': path.stat().st_size,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'probe_ok': probe.returncode == 0,
        'kind': 'video' if is_video else 'image', 'width': video.get('width'), 'height': video.get('height'),
        'codec': video.get('codec_name'), 'fps': video.get('avg_frame_rate'),
        'duration': data.get('format', {}).get('duration'), 'bit_rate': data.get('format', {}).get('bit_rate'),
        'audio': any(s.get('codec_type') == 'audio' for s in streams), 'previews': [],
        'watermark': 'pending_visual_review',
    }
    preview = directory / 'previews'
    preview.mkdir(exist_ok=True)
    points = [0] if not is_video else [0.05, 0.25, 0.5, 0.75, 0.95]
    duration = float(item.get('duration') or 0)
    for index, fraction in enumerate(points):
        output = preview / (path.stem + f'_{index:02d}.jpg')
        cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y']
        if is_video:
            cmd += ['-ss', str(duration * fraction)]
        cmd += ['-i', str(path), '-frames:v', '1', '-vf', 'scale=720:720:force_original_aspect_ratio=decrease', str(output)]
        result = subprocess.run(cmd, capture_output=True, timeout=20)
        if result.returncode == 0 and output.exists():
            item['previews'].append({'file': str(output.relative_to(directory)), 'seconds': round(duration * fraction, 2)})
    if len(item['previews']) == 5:
        output = preview / (path.stem + '_strip.jpg')
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-framerate', '1', '-i', str(preview / (path.stem + '_%02d.jpg')), '-vf', 'scale=480:480:force_original_aspect_ratio=decrease,pad=480:480:(ow-iw)/2:(oh-ih)/2,tile=5x1', '-frames:v', '1', str(output)], capture_output=True, timeout=20)
        if result.returncode == 0:
            item['strip'] = str(output.relative_to(directory))
    return item


def run_mode(case, root, mode, snapshot, timeout):
    directory = root / case['id'] / mode
    directory.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LINKGET_HOME=str(directory / 'isolated-settings'), PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    args = [sys.executable, str(Path(__file__).resolve()), '--child', '--case', json.dumps(case), '--output', str(directory)]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='linkget-validation-cookies-') as temporary:
        if snapshot:
            copied = Path(temporary) / 'cookies.txt'
            shutil.copy2(snapshot, copied)
            copied.chmod(0o600)
            args += ['--cookie', str(copied)]
        with (directory / 'download.log').open('w') as log:
            process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
                state = 'downloaded' if code == 0 else 'failed'
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                code, state = None, 'timeout'
    files = [inspect_media(p, directory) for p in sorted((directory / 'media').glob('*')) if p.is_file() and p.suffix.lower() in cli.MEDIA]
    expected = case.get('expected_count')
    result = {'mode': mode, 'status': state, 'returncode': code, 'seconds': round(time.monotonic()-started, 1), 'files': files,
              'count_check': (len(files) == expected) if expected is not None else None,
              'quality': 'not_proven', 'watermark': 'pending_visual_review' if files else 'not_assessable',
              'log_tail': (directory / 'download.log').read_text(errors='replace').splitlines()[-8:]}
    formats = directory / 'source-formats.json'
    if formats.exists():
        result['source_formats'] = json.loads(formats.read_text())
        source = [f for f in result['source_formats']['formats'] if f.get('width') and f.get('height')]
        actual = [f for f in files if f['kind'] == 'video' and f.get('width') and f.get('height')]
        if source and actual:
            ceiling = max(f['width']*f['height'] for f in source)
            result['matches_exposed_resolution_ceiling'] = all(f['width']*f['height'] >= ceiling for f in actual)
    write_json(directory / 'result.json', result)
    return result


def run_channel(cases, root, compare_saved, timeout):
    rows = []
    for case in cases:
        row = dict(case, results=[])
        if case.get('coverage') != 'active':
            row['status'] = case['coverage']
        else:
            row['results'].append(run_mode(case, root, 'public', None, timeout))
            domain = accounts.SITES[case['site']]
            snapshot = accounts.path_for(domain)
            if compare_saved and snapshot.exists():
                row['results'].append(run_mode(case, root, 'saved', snapshot, timeout))
            elif compare_saved:
                row['saved_comparison'] = 'No saved session; browser was not read.'
            if len(row['results']) == 2:
                a, b = [r['files'] for r in row['results']]
                row['same_media_bytes'] = bool(a and b) and sorted(f['sha256'] for f in a) == sorted(f['sha256'] for f in b)
                row['resolution_comparison'] = {
                    r['mode']: sorted((f['width'] or 0, f['height'] or 0) for f in r['files']) for r in row['results']}
        write_json(root / (case['id'] + '.json'), row)
        print(case['id'], [(r['mode'], r['status'], len(r['files'])) for r in row['results']] or row['status'], flush=True)
        rows.append(row)
    return rows


def report(rows, root):
    write_json(root / 'results.json', rows)
    active = [r for r in rows if r.get('coverage') == 'active']
    successes = sum(any(r['mode'] == 'public' and r['status'] == 'downloaded' and r['files'] and all(f['probe_ok'] for f in r['files']) and r['count_check'] is not False for r in row['results']) for row in active)
    lines = ['# 实际媒体验证', '', f'{len(set(r["site"] for r in rows))} 个渠道，{len(active)} 个真实帖子，匿名成功 {successes} 个，另有 {len(rows)-len(active)} 个不适用/未实现边界。', '', '匿名成功、达到请求暴露的分辨率上限、达到平台最高质量、无平台水印分别判定。未获得对照依据的一律未证实。视频仅采样 5 帧；原文件保留供完整检查。保存登录对照强制使用已有快照，不刷新浏览器、不修改账户。重复运行产生的初始测试器参数错误已由修正后结果替换，不计作渠道失败。', '', '| 样本 | 渠道 / 类型 | 匿名 | 保存登录对照 | 尺寸对照 | 水印 / 判定 |', '|---|---|---|---|---|---|']
    cards = []
    for row in rows:
        results = {r['mode']: r for r in row['results']}
        label = row.get('status') or '未运行'
        sizes = []
        body = []
        for mode, r in results.items():
            dims = ', '.join(f"{f['width']}×{f['height']}" for f in r['files']) or '—'
            sizes.append(mode+': '+dims)
            block = f'<h3>{mode} · {html.escape(r["status"])}</h3><p>{html.escape(dims)}</p>'
            block += '<p>'+html.escape(r.get('review', '视觉未复核'))+'</p>'
            if r.get('matches_exposed_resolution_ceiling') is not None:
                block += '<p>达到本次请求暴露的分辨率上限：'+str(r['matches_exposed_resolution_ceiling'])+'。不能推出达到平台或上传源最高质量。</p>'
            for f in r['files']:
                base = f'{row.get("evidence_base", row["id"])}/{mode}/'
                previews = f['previews']
                media_url = html.escape(quote(base+f['file']))
                if f.get('retained', True):
                    block += f'<p><a href="{media_url}">原文件：{html.escape(Path(f["file"]).name)}</a> · {f["bytes"]:,} bytes · {html.escape(str(f["codec"]))}</p>'
                else:
                    block += f'<p>原文件已按用户要求清理 · 下载时 {f["bytes"]:,} bytes · {html.escape(str(f["codec"]))}</p>'
                if f['kind'] == 'video':
                    block += f'<p>{f["duration"]}s · {f["bit_rate"]} bit/s · audio={f["audio"]}</p>'
                    if f.get('retained', True):
                        block += f'<video controls preload="none" src="{media_url}"></video>'
                block += '<p>'+html.escape(f.get('review', '该文件尚未复核'))+'</p>'
                block += '<div class="frames">' + ''.join(f'<a href="{html.escape(quote(base+p["file"]))}"><img loading="lazy" src="{html.escape(quote(base+p["file"]))}"><small>{p["seconds"]}s</small></a>' for p in previews) + '</div>'
            if not r['files']:
                block += '<pre>'+html.escape('\n'.join(r['log_tail']))+'</pre>'
            body.append(block)
        public = results.get('public', {}).get('status', label)
        saved = results.get('saved', {}).get('status', '无保存登录 / 不适用')
        review = row.get('review', '待视觉判断' if any(r['files'] for r in results.values()) else '无法判断')
        lines.append(f'| {row["id"]} | {row["site"]} / {row["kind"]} | {public} | {saved} | {"; ".join(sizes) or "—"} | {review.replace("|", "/")} |')
        url = row.get('url') or ''
        # Keep note access tokens out of report text and clickable links.
        link = url.split('?', 1)[0] if row['site'] == 'xiaohongshu' else url
        same = '匿名与保存登录原文件逐字节相同。' if row.get('same_media_bytes') else ''
        cards.append(f'<section id="{html.escape(row["id"])}"><h2>{html.escape(row["id"])} · {row["site"]} / {row["kind"]}</h2><p>{html.escape(row.get("note", ""))}</p><a href="{html.escape(link)}">来源帖子</a><p>最高质量：未证实。{html.escape(review)} {same}</p>{"".join(body) or html.escape(label)}</section>')
    (root / 'report.md').write_text('\n'.join(lines)+'\n')
    page = '<!doctype html><meta charset="utf-8"><title>linkget 媒体验证</title><style>body{font:15px system-ui;max-width:1400px;margin:32px auto;padding:0 24px;color:#222;background:#f7f7f5}section{background:white;border:1px solid #ddd;border-radius:12px;padding:20px;margin:20px 0}h2{font-size:19px}h3{font-size:16px}p{color:#555}a{color:#296bc4}a:hover{color:#123e7b;background:#edf3ff}video{max-width:600px;max-height:420px}.frames{display:flex;gap:10px;overflow:auto}.frames a{flex:0 0 200px}.frames img{width:200px;max-height:220px;object-fit:contain}small{display:block}pre{white-space:pre-wrap;font-size:12px}</style><h1>linkget 实际媒体验证</h1><p>全部匿名请求均隔离登录。saved 为同 URL 强制使用已有会话的测试对照，不刷新浏览器、不修改账户。预览不是完整水印证明；点击原文件检查。分辨率相同也不证明画质相同。</p>' + ''.join(cards)
    (root / 'review.html').write_text(page)


def regressions(rows, baseline):
    """Previously successful controls must remain valid; existing failures aren't new regressions."""
    previous = {(row['id'], result['mode']): result for row in baseline for result in row.get('results', [])
                if result.get('status') == 'downloaded' and result.get('files')
                and result.get('count_check') is not False and all(f.get('probe_ok') for f in result['files'])}
    current = {(row['id'], result['mode']): result for row in rows for result in row.get('results', [])}
    selected = {row['id'] for row in rows}
    failures = []
    for (case, mode), old in previous.items():
        if case not in selected:
            continue  # --sites intentionally narrows the run.
        new = current.get((case, mode))
        reason = None
        if not new or new.get('status') != 'downloaded' or not new.get('files'):
            reason = 'Previously successful download no longer completed'
        elif new.get('count_check') is False or not all(f.get('probe_ok') for f in new['files']):
            reason = 'Invalid or incomplete media'
        elif len(new['files']) != len(old['files']):
            reason = 'Media count changed; review required'
        else:
            sizes = lambda result: sorted((f['kind'], f.get('width') or 0, f.get('height') or 0) for f in result['files'])
            if any(k != pk or w < pw or h < ph for (k, w, h), (pk, pw, ph) in zip(sizes(new), sizes(old))):
                reason = 'Exposed dimensions decreased; review required'
        if reason:
            failures.append({'case': case, 'mode': mode, 'reason': reason})
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('tests/fixtures/media_cases.json'))
    parser.add_argument('--extra', type=Path)
    parser.add_argument('--sites', nargs='+', choices=list(accounts.SITES))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--compare-saved', action='store_true')
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--report-only', action='store_true', help='Regenerate reports from annotated results.json without downloading.')
    parser.add_argument('--baseline', type=Path, help='Fail when a previously successful control regresses in count, validity or dimensions.')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--case', help=argparse.SUPPRESS)
    parser.add_argument('--cookie', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    if args.child:
        return child(json.loads(args.case), args.output, args.cookie)
    if args.report_only:
        if args.output is None:
            parser.error('--report-only requires --output')
        report(json.loads((args.output / 'results.json').read_text()), args.output)
        rows = json.loads((args.output / 'results.json').read_text())
        failures = regressions(rows, baseline) if baseline is not None else []
        if baseline is not None:
            write_json(args.output / 'regressions.json', failures)
        return 1 if failures else 0
    cases = json.loads(args.manifest.read_text())['cases']
    if args.extra:
        cases += json.loads(args.extra.read_text())['cases']
    if args.sites:
        cases = [c for c in cases if c['site'] in args.sites]
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d_%H%M%S')
    root = (args.output or Path('.local/media-validation') / stamp).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(root.iterdir()):
        parser.error('Use an empty output directory to preserve previous evidence.')
    write_json(root / 'manifest.json', {'cases': cases})
    write_json(root / 'environment.json', {'time': stamp, 'version': cli.VERSION, 'python': sys.version, 'tools': {t: subprocess.check_output([t, '--version' if t in {'gallery-dl', 'yt-dlp'} else '-version'], text=True).splitlines()[0] for t in ('gallery-dl','yt-dlp','ffmpeg','ffprobe')}})
    print('OUTPUT', root, flush=True)
    groups = {site: [c for c in cases if c['site'] == site] for site in dict.fromkeys(c['site'] for c in cases)}
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_channel, group, root, args.compare_saved, args.timeout): site for site, group in groups.items()}
        for future in as_completed(futures):
            rows += future.result()
            report(sorted(rows, key=lambda r: next(i for i,c in enumerate(cases) if c['id']==r['id'])), root)
    print('REPORT', root / 'report.md', flush=True)
    failures = regressions(rows, baseline) if baseline is not None else []
    if baseline is not None:
        write_json(root / 'regressions.json', failures)
        print('REGRESSIONS', len(failures), flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
