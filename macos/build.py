#!/usr/bin/env python3
"""Build an ad-hoc signed native host and macOS Share extension; no install side effects."""
import argparse
import plistlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'src'))
from linkget.version import VERSION

def plist(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(data))

def run(*args):
    subprocess.run(args, check=True)

def build(output):
    app = output / 'LinkGet.app'
    if app.exists():
        raise ValueError('Choose an empty output folder; existing app bundles are preserved.')
    base = {'CFBundleDevelopmentRegion': 'en', 'CFBundleVersion': '14', 'CFBundleShortVersionString': VERSION,
            'LSMinimumSystemVersion': '13.0', 'NSHighResolutionCapable': True}
    plist(app / 'Contents/Info.plist', {**base, 'CFBundleIdentifier': 'com.popcorn.linkget',
        'CFBundleName': 'LinkGet', 'CFBundleExecutable': 'LinkGet', 'CFBundlePackageType': 'APPL',
        'CFBundleURLTypes': [{'CFBundleURLName': 'com.popcorn.linkget.share', 'CFBundleURLSchemes': ['linkget']}],
        'CFBundleDocumentTypes': [{'CFBundleTypeName': 'Media', 'CFBundleTypeRole': 'Viewer', 'LSItemContentTypes': ['public.movie', 'public.audio', 'public.image']}],
        'NSServices': [{'NSMenuItem': {'default': 'Read with LinkGet'}, 'NSMessage': 'readSelection', 'NSPortName': 'LinkGet', 'NSSendTypes': ['public.url', 'NSStringPboardType'], 'NSUserData': 'read'},
                       {'NSMenuItem': {'default': 'Download with LinkGet'}, 'NSMessage': 'readSelection', 'NSPortName': 'LinkGet', 'NSSendTypes': ['public.url', 'NSStringPboardType'], 'NSUserData': 'download'}]})
    ext = app / 'Contents/PlugIns/LinkGetShare.appex'
    plist(ext / 'Contents/Info.plist', {**base, 'CFBundleIdentifier': 'com.popcorn.linkget.share',
        'CFBundleName': 'LinkGet', 'CFBundleDisplayName': 'LinkGet', 'CFBundleExecutable': 'LinkGetShare', 'CFBundlePackageType': 'XPC!',
        'NSExtension': {'NSExtensionPointIdentifier': 'com.apple.share-services', 'NSExtensionPrincipalClass': 'LinkGetShareController',
                       'NSExtensionAttributes': {'NSExtensionActivationRule': {'NSExtensionActivationSupportsWebURLWithMaxCount': 1, 'NSExtensionActivationSupportsText': True, 'NSExtensionActivationSupportsFileWithMaxCount': 1, 'NSExtensionActivationSupportsImageWithMaxCount': 1, 'NSExtensionActivationSupportsMovieWithMaxCount': 1}}}})
    for path in [app / 'Contents/MacOS', ext / 'Contents/MacOS']:
        path.mkdir(parents=True)
    run('/usr/bin/swiftc', '-O', '-target', 'arm64-apple-macos13.0', str(ROOT / 'LinkGet.swift'), '-o', str(app / 'Contents/MacOS/LinkGet'), '-framework', 'AppKit', '-framework', 'SwiftUI')
    run('/usr/bin/swiftc', '-O', '-target', 'arm64-apple-macos13.0', '-application-extension', '-parse-as-library', '-Xlinker', '-e', '-Xlinker', '_NSExtensionMain', '-module-name', 'LinkGetShare', str(ROOT / 'Share.swift'), '-o', str(ext / 'Contents/MacOS/LinkGetShare'), '-framework', 'AppKit', '-framework', 'UniformTypeIdentifiers')
    entitlements = output / 'share-entitlements.plist'
    plist(entitlements, {'com.apple.security.app-sandbox': True})
    run('/usr/bin/codesign', '--force', '--sign', '-', '--entitlements', str(entitlements), str(ext))
    run('/usr/bin/codesign', '--force', '--sign', '-', str(app))
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', str(app))
    print(app)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('output', type=Path)
    build(parser.parse_args().output.expanduser().resolve())
