"""Read-only diagnostics; live checks require one explicit target."""
from datetime import datetime, timezone
import os
import sys
import ctypes
import time

if __package__:
    from . import accounts, rendered_page
    from .failures import redact
else:
    import accounts, rendered_page
    from failures import redact


def chrome_automation_access():
    """Ask the OS whether existing access is usable; never show consent UI."""
    if sys.platform != 'darwin':
        return 'unavailable'
    class Descriptor(ctypes.Structure):
        # The macOS SDK AEDataModel.h uses pragma pack(push,2).
        _layout_='ms'
        _pack_=2
        _fields_=[('descriptorType',ctypes.c_uint32),('dataHandle',ctypes.c_void_p)]
    try:
        library=ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        library.AECreateDesc.argtypes=[ctypes.c_uint32,ctypes.c_void_p,ctypes.c_long,ctypes.POINTER(Descriptor)]
        library.AECreateDesc.restype=ctypes.c_int16
        library.AEDisposeDesc.argtypes=[ctypes.POINTER(Descriptor)]
        library.AEDisposeDesc.restype=ctypes.c_int16
        library.AEDeterminePermissionToAutomateTarget.argtypes=[ctypes.POINTER(Descriptor),ctypes.c_uint32,ctypes.c_uint32,ctypes.c_ubyte]
        library.AEDeterminePermissionToAutomateTarget.restype=ctypes.c_int32
        descriptor=Descriptor();target=b'com.google.Chrome'
        if library.AECreateDesc(int.from_bytes(b'bund','big'),target,len(target),ctypes.byref(descriptor)):
            return 'unknown'
        try:
            result=library.AEDeterminePermissionToAutomateTarget(ctypes.byref(descriptor),int.from_bytes(b'****','big'),int.from_bytes(b'****','big'),False)
        finally:
            library.AEDisposeDesc(ctypes.byref(descriptor))
        return 'granted' if result==0 else 'not granted' if result in {-1743,-1744} else 'target not running' if result==-600 else 'unknown'
    except (OSError,AttributeError,TypeError,ctypes.ArgumentError):
        return 'unknown'


def browser_probe(name):
    """Check selected transport/access evidence; no page is opened or navigated."""
    if name == 'neo':
        if __package__:
            from . import neo_browser
        else:
            import neo_browser
        try:
            client = neo_browser._RPCClient(os.environ.get('LINKGET_NEO_MCP_ENDPOINT',neo_browser.DEFAULT_ENDPOINT), time.monotonic()+8)
            client.initialize()
            return 'connected', 'Neo MCP '+client.server_version+'; DOM/site access not tested.'
        except (OSError, ValueError, RuntimeError) as error:
            return 'unavailable', redact(error)+' Check the existing Neo MCP connection; no Chrome fallback was attempted.'
    if name == 'chrome':
        access=chrome_automation_access()
        if access=='granted':
            return 'OS access checked', ('The OS reports automation access for this CLI process. '
                'The separate Chrome controller sender, JavaScript and site access were not exercised; no event or permission request was sent.')
        return 'not checked', 'Existing CLI automation access is '+access+'; no consent request or control script was sent.'
    return 'unsupported controller', 'Only Chrome and Neo have page controllers; choose config --reader-browser chrome or neo.'


def inspect_data(args=None):
    browser = getattr(args, 'browser', 'auto')
    choice = rendered_page.selection(getattr(args, 'rendered_backend', None), browser=browser)
    rows = [('Page controller', choice['browser'] + ' · ' + choice['source'] +
             (' · supported; connection not checked' if choice['supported'] else ' · disabled' if not choice['enabled'] else ' · unsupported controller'))]
    rows.append(('Browser next', 'linkget doctor --probe-browser ' + choice['browser'] + ' runs a limited connection/access check; DOM and site access are not tested.' if choice['supported'] else 'Choose config --reader-browser chrome or neo for rendered pages; direct downloads are unaffected.'))
    settings = accounts.preferences()
    blocked = set(settings.get('blocked', []))
    sessions = {}
    for site, domain in accounts.SITES.items():
        path = accounts.path_for(domain)
        if domain in blocked:
            state = 'disconnected; browser access blocked'
            sessions[site] = {'domain': domain, 'state': 'disconnected', 'detail': state}
        elif path.is_symlink():
            state = 'unknown; saved session path is a symbolic link'
            sessions[site] = {'domain': domain, 'state': 'unknown', 'detail': state}
        elif not path.is_file():
            state = 'not saved'
            sessions[site] = {'domain': domain, 'state': 'missing', 'detail': state}
        else:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='seconds')
            state = 'saved · modified ' + modified + ' · validity unknown (not checked)'
            sessions[site] = {'domain': domain, 'state': 'saved_unverified', 'modified': modified, 'detail': state}
        rows.append(('Session ' + site, state))
    rows.append(('Session next', 'linkget doctor --check-session SITE explicitly checks one existing saved session; no browser export.'))
    code = 0
    browser_check = None
    session_check = None
    probe = getattr(args, 'probe_browser', None)
    if probe:
        if browser == 'none':
            rows.append(('Browser check', 'disabled by --browser none'))
            browser_check = {'browser': probe, 'state': 'disabled', 'detail': 'disabled by --browser none', 'ok': False}
            code = 1
        else:
            state, detail = browser_probe(probe)
            ok = state in {'connected', 'OS access checked'}
            rows.append((probe + ' check', state + ' · ' + detail))
            browser_check = {'browser': probe, 'state': state, 'detail': detail, 'ok': ok}
            code = int(not ok)
    site = getattr(args, 'check_session', None)
    if site:
        domain = accounts.SITES[site]
        if domain in blocked or browser == 'none':
            rows.append((site + ' check', 'disabled; no saved credentials were read'))
            session_check = {'site': site, 'domain': domain, 'state': 'disabled', 'detail': 'disabled; no saved credentials were read', 'ok': False}
            code = 1
        else:
            state = accounts.status(domain)
            detail = redact(state.detail)
            rows.append((site + ' check', state.state + ' · ' + detail))
            session_check = {'site': site, 'domain': domain, 'state': state.state, 'detail': detail, 'ok': state.state == 'valid'}
            if state.state != 'valid':
                code = 1
                next_step = ('Complete the website check in your configured login browser.' if state.state == 'attention' else
                             'Use linkget auth ' + site + ' for explicit reconnection; saved data was preserved.' if state.state in {'invalid', 'missing'} else
                             'The website did not establish login validity; retry the explicit check later. Saved data was preserved.')
                rows.append(('Session next', next_step))
                session_check['next_step'] = next_step
    data = {
        'page_controller': choice,
        'sessions': sessions,
        'browser_check': browser_check,
        'session_check': session_check,
    }
    return data, rows, code


def inspect(args=None):
    _, rows, code = inspect_data(args)
    return rows, code
