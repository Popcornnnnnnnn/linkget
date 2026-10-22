"""Local installation identity; no Git, network or user-data inspection."""
import argparse
import json
from pathlib import Path
import re

if __package__:
    from .version import VERSION
else:
    from version import VERSION


def describe():
    package=Path(__file__).resolve().parent
    result={'version':VERSION,'installation':'unknown','build_revision':None,
            'source_commit':None,'reviewed_head':None}
    # A Homebrew formula embeds identity outside the Python package. Never
    # search home directories, site metadata URLs or arbitrary parent trees.
    if package.parent.name=='libexec':
        identity=package.parent.parent/'BUILD_IDENTITY.json'
        try:
            if identity.is_symlink() or identity.stat().st_size>16384:
                return result
            data=json.loads(identity.read_text())
            if not isinstance(data,dict) or data.get('version')!=VERSION:
                return result
            revision=data.get('homebrew_revision')
            commit=data.get('source_commit')
            if (type(revision) is not int or revision<0 or revision>999999 or
                    not re.fullmatch('[a-f0-9]{40}',str(commit))):
                return result
            result.update(installation='Homebrew (local build identity)',build_revision=revision,source_commit=commit)
            reviewed=data.get('reviewed_head')
            if re.fullmatch('[a-f0-9]{40}',str(reviewed)):
                result['reviewed_head']=reviewed
        except (OSError,ValueError):
            pass
    elif package.parent.name in {'site-packages','dist-packages'}:
        result['installation']='Python package'
    elif package.parent.name=='src' and (package.parent.parent/'pyproject.toml').is_file():
        result['installation']='source tree'
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(prog='linkget version',description='Show detailed local build identity without network or Git. The short --version output stays unchanged.')
    parser.add_argument('--json',action='store_true',help='Print machine-readable identity; absent evidence is null')
    args=parser.parse_args(argv)
    data=describe()
    if args.json:
        print(json.dumps(data))
    else:
        for label,key in [('LinkGet','version'),('Installation','installation'),('Build revision','build_revision'),('Source commit','source_commit'),('Reviewed head','reviewed_head')]:
            print(label+': '+str(data[key] if data[key] is not None else 'unknown (not embedded)'))
    return 0
