"""Thin stdlib JSON-RPC 2.0 stdio MCP server exposing core linkget capabilities."""
import contextlib
import io
import json
from pathlib import Path
import re
import sys

if __package__:
    from . import accounts, agent_output, media_limits, read_store, read_tasks, runtime
    from .version import VERSION
else:
    import accounts, agent_output, media_limits, read_store, read_tasks, runtime
    from version import VERSION

DEFAULT_PROTOCOL = '2024-11-05'
SUPPORTED_PROTOCOLS = {'2024-11-05', '2025-03-26', '2025-06-18'}
MAX_MESSAGE_BYTES = 2 * 1024 * 1024
BROWSERS = {'auto', 'none', 'chrome', 'firefox', 'safari', 'edge', 'brave'}
AUDIO_FORMATS = {'mp3', 'm4a', 'flac', 'wav', 'opus'}
READ_RESERVED = {
    'status', 'wait', 'show', 'logs', 'cancel', 'batch',
    'translate', 'summarize', 'recover', 'retry', 'config', 'doctor', 'help', '_worker',
}
TASK_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')

TOOLS = [
    {
        'name': 'inspect_url',
        'description': 'Side-effect-free URL or local media file inspection: routing, audio/read support, required tools, session state, destination preview, and optional remote metadata.',
        'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': True},
        'inputSchema': {
            'type': 'object',
            'properties': {
                'url_or_path': {'type': 'string', 'description': 'Supported media/article URL or existing local media file path'},
                'audio': {'type': 'boolean', 'default': False, 'description': 'Check effective audio download mode'},
                'article': {'type': 'boolean', 'default': False, 'description': 'Inspect URL as an offline article archive'},
                'no_network': {'type': 'boolean', 'default': False, 'description': 'Skip short-link expansion and remote metadata probe'},
                'browser': {'type': 'string', 'enum': ['auto', 'none', 'chrome', 'firefox', 'safari', 'edge', 'brave'], 'default': 'auto'},
            },
            'required': ['url_or_path'],
        },
    },
    {
        'name': 'download_media',
        'description': 'Download media, audio, or an offline article archive to an explicit destination folder with optional clipping and safety caps.',
        'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': True},
        'inputSchema': {
            'type': 'object',
            'properties': {
                'url': {'type': 'string', 'description': 'Single supported post, article, video, or music URL'},
                'folder': {'type': 'string', 'description': 'Explicit destination directory path (required in MCP mode)'},
                'audio': {'type': 'boolean', 'default': False, 'description': 'Extract/download audio only'},
                'audio_format': {'type': 'string', 'enum': ['mp3', 'm4a', 'flac', 'wav', 'opus'], 'description': 'Optional audio output format (implies audio)'},
                'article': {'type': 'boolean', 'default': False, 'description': 'Treat an X post URL as an offline article archive'},
                'no_assets': {'type': 'boolean', 'default': False, 'description': 'Article downloads only: skip downloading embedded images and videos'},
                'clip': {'type': 'string', 'description': 'Optional time range START-END (e.g. 0:30-2:00)'},
                'max_duration': {'type': 'string', 'description': 'Maximum duration cap (e.g. 10m, 90s)'},
                'max_size': {'type': 'string', 'description': 'Maximum size cap (e.g. 100MB, 1.5GB)'},
                'browser': {'type': 'string', 'enum': ['auto', 'none', 'chrome', 'firefox', 'safari', 'edge', 'brave'], 'default': 'auto'},
            },
            'required': ['url', 'folder'],
        },
    },
    {
        'name': 'read_content',
        'description': 'Extract transcripts, subtitles, OCR, video keyframes, or X/web/WeChat/Toutiao articles.',
        'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': True},
        'inputSchema': {
            'type': 'object',
            'properties': {
                'url_or_path': {'type': 'string', 'description': 'Post/video/article URL or local media file path'},
                'folder': {'type': 'string', 'description': 'Optional new output folder path'},
                'frames': {'type': 'boolean', 'default': False, 'description': 'Extract video keyframes into frames/'},
                'max_frames': {'type': 'integer', 'minimum': 1, 'maximum': 60, 'default': 12},
                'frame_ocr': {'type': 'boolean', 'default': False, 'description': 'Run macOS Vision OCR on extracted keyframes'},
                'no_assets': {'type': 'boolean', 'default': False, 'description': 'Article reads: skip downloading embedded images and videos'},
                'language': {'type': 'string', 'description': 'Whisper language code or auto'},
                'subtitles': {'type': 'string', 'enum': ['auto', 'never', 'only'], 'default': 'auto'},
                'browser': {'type': 'string', 'enum': ['auto', 'none', 'chrome', 'firefox', 'safari', 'edge', 'brave'], 'default': 'auto'},
                'background': {'type': 'boolean', 'default': False, 'description': 'Submit asynchronously and return a task_id instead of running synchronously'},
            },
            'required': ['url_or_path'],
        },
    },
    {
        'name': 'get_task',
        'description': 'Inspect download or background read tasks, optionally loading completed read content.',
        'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
        'inputSchema': {
            'type': 'object',
            'properties': {
                'task_id': {'type': 'string', 'description': 'Specific download or read task ID; omit to list recent tasks'},
                'kind': {'type': 'string', 'enum': ['all', 'download', 'read'], 'default': 'all'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20},
                'include_content': {'type': 'boolean', 'default': False, 'description': 'When task_id is a completed read task, include content.json'},
            },
        },
    },
    {
        'name': 'doctor',
        'description': 'Inspect installed local tools, browser controller selection, and optional destination writability.',
        'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
        'inputSchema': {
            'type': 'object',
            'properties': {
                'folder': {'type': 'string', 'description': 'Optional folder path to test writability'},
                'probe_browser': {'type': 'string', 'enum': ['neo', 'chrome']},
                'check_session': {'type': 'string', 'description': 'Optional saved site session to check without browser export'},
            },
        },
    },
]


class _RPCError(Exception):
    def __init__(self, code, message, data=None, *, framed=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
        self.framed = framed


def _cli_module():
    if __package__:
        from . import cli
    else:
        import cli
    return cli


def _run_cli_json(argv, *, command=None):
    cli = _cli_module()
    cmd_name = command or (argv[0] if argv else 'cli')
    saved_modes = runtime.snapshot_modes()
    out_buf, err_buf = io.StringIO(), io.StringIO()
    code = 1
    try:
        runtime.set_non_interactive(True)
        runtime.set_json_mode(True)
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        if not out_buf.getvalue().strip():
            msg = err_buf.getvalue().strip() or f'CLI exited with code {code}.'
            return agent_output.error_payload(cmd_name, ValueError(msg), exit_code=code)
    except Exception as error:
        return agent_output.error_payload(cmd_name, error, exit_code=1)
    finally:
        runtime.restore_modes(saved_modes)

    raw_out = out_buf.getvalue().strip()
    if raw_out:
        for line in reversed(raw_out.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed
            except ValueError:
                continue
    msg = err_buf.getvalue().strip() or f'Command finished with exit code {code} and no JSON output.'
    return agent_output.error_payload(cmd_name, RuntimeError(msg), exit_code=code or 1)


def _require_str(args, key):
    val = args.get(key)
    if not isinstance(val, str) or not val.strip() or '\x00' in val:
        raise _RPCError(-32602, f'Argument "{key}" must be a non-empty string.')
    return val.strip()


def _optional_str(args, key):
    if key not in args or args[key] is None:
        return None
    val = args[key]
    if not isinstance(val, str) or not val.strip() or '\x00' in val:
        raise _RPCError(-32602, f'Argument "{key}" must be a non-empty string.')
    return val.strip()


def _require_bool(args, key, default=False):
    if key not in args or args[key] is None:
        return default
    val = args[key]
    if not isinstance(val, bool):
        raise _RPCError(-32602, f'Argument "{key}" must be a boolean.')
    return val


def _optional_enum(args, key, allowed):
    if key not in args or args[key] is None:
        return None
    val = args[key]
    if not isinstance(val, str) or val not in allowed:
        raise _RPCError(-32602, f'Argument "{key}" must be one of: {", ".join(sorted(allowed))}.')
    return val


def _safe_target(target, *, reject_read_reserved=False):
    expanded = Path(target).expanduser()
    if expanded.is_symlink():
        raise _RPCError(-32602, 'Symbolic links are not permitted for url_or_path.')
    if expanded.exists():
        return str(expanded.resolve())
    if target.startswith('-'):
        raise _RPCError(-32602, 'Argument "url_or_path" must be a valid URL or existing local file path.')
    if reject_read_reserved and target.lower() in READ_RESERVED:
        raise _RPCError(-32602, 'Argument "url_or_path" must be a URL or existing local file path, not a read subcommand.')
    return target


def _call_tool(name, arguments):
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise _RPCError(-32602, 'Tool arguments must be a JSON object.')

    if name == 'inspect_url':
        target = _safe_target(_require_str(arguments, 'url_or_path'))
        argv = ['inspect', target, '--non-interactive', '--json']
        if _require_bool(arguments, 'audio'):
            argv.append('--audio')
        if _require_bool(arguments, 'article'):
            argv.append('--article')
        if _require_bool(arguments, 'no_network'):
            argv.append('--no-network')
        browser = _optional_enum(arguments, 'browser', BROWSERS)
        if browser:
            argv.append(f'--browser={browser}')
        return _run_cli_json(argv, command='inspect')

    if name == 'download_media':
        url = _require_str(arguments, 'url')
        if not url.startswith(('http://', 'https://')):
            raise _RPCError(-32602, 'Argument "url" must start with http:// or https://.')
        folder = _require_str(arguments, 'folder')
        clip = _optional_str(arguments, 'clip')
        if clip:
            try:
                media_limits.parse_clip(clip)
            except ValueError as error:
                raise _RPCError(-32602, str(error)) from error
        max_duration = _optional_str(arguments, 'max_duration')
        if max_duration:
            try:
                media_limits.parse_duration(max_duration)
            except ValueError as error:
                raise _RPCError(-32602, str(error)) from error
        max_size = _optional_str(arguments, 'max_size')
        if max_size:
            try:
                media_limits.parse_size(max_size)
            except ValueError as error:
                raise _RPCError(-32602, str(error)) from error
        argv = [url, f'--folder={folder}', '--non-interactive', '--json']
        if _require_bool(arguments, 'audio'):
            argv.append('--audio')
        audio_format = _optional_enum(arguments, 'audio_format', AUDIO_FORMATS)
        if audio_format:
            argv.append(f'--audio-format={audio_format}')
        if _require_bool(arguments, 'article'):
            argv.append('--article')
        if _require_bool(arguments, 'no_assets'):
            argv.append('--no-assets')
        if clip:
            argv.append(f'--clip={clip}')
        if max_duration:
            argv.append(f'--max-duration={max_duration}')
        if max_size:
            argv.append(f'--max-size={max_size}')
        browser = _optional_enum(arguments, 'browser', BROWSERS)
        if browser:
            argv.append(f'--browser={browser}')
        return _run_cli_json(argv, command='download')

    if name == 'read_content':
        target = _safe_target(_require_str(arguments, 'url_or_path'), reject_read_reserved=True)
        argv = ['read', target, '--non-interactive', '--json']
        if not _require_bool(arguments, 'background'):
            argv.append('--foreground')
        folder = _optional_str(arguments, 'folder')
        if folder:
            argv.append(f'--folder={folder}')
        if _require_bool(arguments, 'frames'):
            argv.append('--frames')
        if 'max_frames' in arguments and arguments['max_frames'] is not None:
            if type(arguments['max_frames']) is not int or not 1 <= arguments['max_frames'] <= 60:
                raise _RPCError(-32602, 'max_frames must be an integer between 1 and 60.')
            argv.append(f"--max-frames={arguments['max_frames']}")
        if _require_bool(arguments, 'frame_ocr'):
            argv.append('--frame-ocr')
        if _require_bool(arguments, 'no_assets'):
            argv.append('--no-assets')
        language = _optional_str(arguments, 'language')
        if language:
            if language.startswith('-'):
                raise _RPCError(-32602, 'Argument "language" must not start with "-".')
            argv.append(f'--language={language}')
        subtitles = _optional_enum(arguments, 'subtitles', {'auto', 'never', 'only'})
        if subtitles:
            argv.append(f'--subtitles={subtitles}')
        browser = _optional_enum(arguments, 'browser', BROWSERS)
        if browser:
            argv.append(f'--browser={browser}')
        return _run_cli_json(argv)

    if name == 'get_task':
        argv = ['tasks', '--non-interactive', '--json']
        task_id = _optional_str(arguments, 'task_id')
        if task_id:
            if not TASK_ID_RE.fullmatch(task_id):
                raise _RPCError(-32602, 'Argument "task_id" contains unsupported characters.')
            argv.append(task_id)
        kind = _optional_enum(arguments, 'kind', {'all', 'download', 'read'})
        if kind in {'download', 'read'}:
            argv.append(f'--kind={kind}')
        limit = arguments.get('limit')
        if limit is not None:
            if type(limit) is not int or not 1 <= limit <= 100:
                raise _RPCError(-32602, 'limit must be an integer between 1 and 100.')
            argv.append(f'--limit={limit}')
        include_content = _require_bool(arguments, 'include_content')
        payload = _run_cli_json(argv)
        if include_content and task_id:
            for row in payload.get('tasks', []):
                if row.get('kind') == 'read' and row.get('status') == 'completed' and row.get('outputs'):
                    out_dir = Path(row['outputs'][0])
                    if not out_dir.is_symlink() and out_dir.is_dir():
                        content_file = out_dir / 'content.json'
                        if not content_file.is_symlink() and content_file.is_file():
                            try:
                                row['content'] = read_store.read_json(content_file)
                            except (OSError, ValueError):
                                row['content_error'] = 'Could not read saved content.json.'
        return payload

    if name == 'doctor':
        argv = ['doctor', '--non-interactive', '--json']
        folder = _optional_str(arguments, 'folder')
        if folder:
            argv.append(f'--folder={folder}')
        probe_browser = _optional_enum(arguments, 'probe_browser', {'neo', 'chrome'})
        if probe_browser:
            argv.append(f'--probe-browser={probe_browser}')
        check_session = _optional_enum(arguments, 'check_session', set(accounts.SITES))
        if check_session:
            argv.append(f'--check-session={check_session}')
        return _run_cli_json(argv)

    raise _RPCError(-32602, f'Unknown tool: {name}')


def _read_message(stream):
    while True:
        raw_line = stream.readline()
        if not raw_line:
            return None, False
        if not raw_line.strip():
            continue
        stripped = raw_line.lstrip()
        if stripped.startswith((b'{', b'[')) or not re.match(rb'^[A-Za-z0-9-]+\s*:', stripped):
            try:
                return json.loads(raw_line.decode('utf-8')), False
            except (UnicodeDecodeError, ValueError) as error:
                raise _RPCError(-32700, f'Parse error: {error}') from error
        headers = {}
        line = raw_line
        while True:
            text = line.decode('ascii', errors='replace').strip()
            if not text:
                break
            if ':' in text:
                k, v = text.split(':', 1)
                headers[k.strip().lower()] = v.strip()
            line = stream.readline()
            if not line:
                return None, True
        length_str = headers.get('content-length')
        if not length_str or not re.fullmatch(r'\d+', length_str):
            raise _RPCError(-32700, 'Missing or invalid Content-Length header.', framed=True)
        length = int(length_str)
        if length <= 0 or length > MAX_MESSAGE_BYTES:
            raise _RPCError(-32600, 'Message length out of bounds.', framed=True)
        body = stream.read(length)
        if len(body) != length:
            return None, True
        try:
            return json.loads(body.decode('utf-8')), True
        except (UnicodeDecodeError, ValueError) as error:
            raise _RPCError(-32700, f'Parse error: {error}', framed=True) from error


def _write_message(stream, payload, *, framed=False):
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if framed:
        header = f'Content-Length: {len(raw)}\r\n\r\n'.encode('ascii')
        stream.write(header + raw)
    else:
        stream.write(raw + b'\n')
    stream.flush()


def handle_request(message):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        raise _RPCError(-32600, 'Invalid JSON-RPC 2.0 request.')
    method = message.get('method')
    if not isinstance(method, str) or not method:
        raise _RPCError(-32600, 'Missing JSON-RPC method.')
    has_id = 'id' in message
    req_id = message.get('id')
    if has_id and req_id is not None and (isinstance(req_id, bool) or not isinstance(req_id, (str, int, float))):
        raise _RPCError(-32600, 'Invalid JSON-RPC id.')
    params = message.get('params')

    if method == 'notifications/initialized' or not has_id:
        return None
    if method == 'initialize':
        client_proto = params.get('protocolVersion') if isinstance(params, dict) else None
        proto = client_proto if client_proto in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {
                'protocolVersion': proto,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'linkget', 'version': VERSION},
            },
        }
    if method == 'ping':
        return {'jsonrpc': '2.0', 'id': req_id, 'result': {}}
    if method == 'tools/list':
        return {'jsonrpc': '2.0', 'id': req_id, 'result': {'tools': TOOLS}}
    if method == 'tools/call':
        if not isinstance(params, dict) or not isinstance(params.get('name'), str):
            raise _RPCError(-32602, 'tools/call requires params.name.')
        payload = _call_tool(params['name'], params.get('arguments'))
        text = json.dumps(payload, ensure_ascii=False)
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {
                'content': [{'type': 'text', 'text': text}],
                'structuredContent': payload,
                'isError': not bool(payload.get('ok')),
            },
        }
    raise _RPCError(-32601, f'Method not found: {method}')


def serve(in_stream=None, out_stream=None):
    reader = in_stream if in_stream is not None else sys.stdin.buffer
    writer = out_stream if out_stream is not None else sys.stdout.buffer
    framed = False
    while True:
        has_id = False
        req_id = None
        try:
            message, msg_framed = _read_message(reader)
            framed = msg_framed if message is not None else framed
            if message is None:
                return 0
            if isinstance(message, dict) and 'id' in message:
                has_id = True
                req_id = message.get('id')
            response = handle_request(message)
            if response is not None:
                _write_message(writer, response, framed=msg_framed)
        except _RPCError as error:
            reply_framed = error.framed if error.framed is not None else framed
            if error.framed is not None:
                framed = error.framed
            if has_id or error.code in {-32700, -32600}:
                _write_message(
                    writer,
                    {'jsonrpc': '2.0', 'id': req_id if has_id else None, 'error': {'code': error.code, 'message': error.message}},
                    framed=reply_framed,
                )
        except (KeyboardInterrupt, EOFError):
            return 0
        except Exception as error:
            if has_id:
                _write_message(
                    writer,
                    {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32603, 'message': f'Internal error: {error}'}},
                    framed=framed,
                )
