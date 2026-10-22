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
    """Check only the selected interface; no page is opened or navigated."""
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


def inspect(args=None):
    browser = getattr(args,'browser','auto')
    choice = rendered_page.selection(getattr(args,'rendered_backend',None),browser=browser)
    rows=[('Page controller', choice['browser']+' · '+choice['source']+
           (' · supported; connection not checked' if choice['supported'] else ' · disabled' if not choice['enabled'] else ' · unsupported controller'))]
    rows.append(('Browser next', 'linkget doctor --probe-browser '+choice['browser']+' checks connection/control only; no site is opened.' if choice['supported'] else 'Choose config --reader-browser chrome or neo for rendered pages; direct downloads are unaffected.'))
    settings=accounts.preferences();blocked=set(settings.get('blocked',[]))
    for site,domain in accounts.SITES.items():
        path=accounts.path_for(domain)
        if domain in blocked:
            state='disconnected; browser access blocked'
        elif path.is_symlink():
            state='unknown; saved session path is a symbolic link'
        elif not path.is_file():
            state='not saved'
        else:
            modified=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(timespec='seconds')
            state='saved · modified '+modified+' · validity unknown (not checked)'
        rows.append(('Session '+site,state))
    rows.append(('Session next','linkget doctor --check-session SITE explicitly checks one existing saved session; no browser export.'))
    code=0
    probe=getattr(args,'probe_browser',None)
    if probe:
        if browser=='none':
            rows.append(('Browser check','disabled by --browser none'));code=1
        else:
            state,detail=browser_probe(probe)
            rows.append((probe+' check',state+' · '+detail))
            code=int(state not in {'connected','OS access checked'})
    site=getattr(args,'check_session',None)
    if site:
        domain=accounts.SITES[site]
        if domain in blocked or browser=='none':
            rows.append((site+' check','disabled; no saved credentials were read'));code=1
        else:
            state=accounts.status(domain)
            rows.append((site+' check',state.state+' · '+redact(state.detail)))
            if state.state!='valid':
                code=1
                next_step=('Complete the website check in your configured login browser.' if state.state=='attention' else
                    'Use linkget auth '+site+' for explicit reconnection; saved data was preserved.' if state.state in {'invalid','missing'} else
                    'The website did not establish login validity; retry the explicit check later. Saved data was preserved.')
                rows.append(('Session next',next_step))
    return rows,code
