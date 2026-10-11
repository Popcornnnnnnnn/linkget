"""macOS Vision OCR, with a source-keyed helper and no third-party model."""
import json
from pathlib import Path
import subprocess
import sys

if __package__:
    from . import read_store
else:
    import read_store


def images(paths):
    if sys.platform != 'darwin':
        raise RuntimeError('Image OCR currently requires macOS Vision; post text is available without OCR.')
    source = Path(__file__).with_suffix('.swift')
    helper = read_store.root() / 'read-tools' / ('ocr-' + read_store.digest(source)[:16])
    helper.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with read_store.lock(helper.with_suffix('.lock')):
        if not helper.exists():
            subprocess.run(['/usr/bin/swiftc', str(source), '-o', str(helper), '-framework', 'Vision', '-framework', 'AppKit'],
                           check=True, capture_output=True, timeout=120)
            helper.chmod(0o700)
    result = subprocess.run([str(helper), *(str(p) for p in paths)], check=True, capture_output=True, text=True,
                            timeout=max(30, len(paths) * 20))
    return json.loads(result.stdout)
