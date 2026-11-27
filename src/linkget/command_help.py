"""Focused help assembled from existing CLI options; command behavior is unchanged."""
import argparse
from copy import copy

COMMON={'folder','photos','audio','audio_format','article','no_assets','browser','profile','live_photos','date_now','report','json','non_interactive','clip','max_duration','max_size'}
SPECS={
 'config':('View or save personal defaults. Omit options for a read-only query.',
           {'folder','photos','audio_folder','clear_audio_folder','article_folder','clear_article_folder','x_article_photos','reader_browser','json','non_interactive'},
           'linkget config\nlinkget config --reader-browser neo\nlinkget config --folder ~/Downloads\nlinkget config --audio-folder ~/Music\nlinkget config --article-folder ~/Articles\nlinkget config --x-article-photos\nThese settings are permanent. One-time page override: linkget URL --rendered-backend chrome.\n--reader-browser is configuration only; omit config for one-time --folder/--photos.'),
 'doctor':('Inspect local tools, page-controller selection and saved-session metadata. Default: no website or browser requests.',
           {'folder','photos','audio','audio_format','live_photos','browser','rendered_backend','probe_browser','check_session','json','non_interactive'},
           'linkget doctor\nlinkget doctor --probe-browser neo\nlinkget doctor --check-session bilibili\nConnection/control checks do not establish actual article or website availability.'),
 'inspect':('Inspect one link without downloading or modifying files. Returns routing, session, tool, destination, and optional metadata.',
            {'save_as','audio','audio_format','article','video','browser','no_network','json','non_interactive'},
            'linkget inspect URL\nlinkget inspect URL --video --no-network --json\nlinkget inspect URL --audio --json'),
 'retry':('Retry unfinished download items; preserve checked retained resources and frozen controller choices.',
          COMMON|{'jobs','redownload','rendered_backend','page_data'},
          'linkget retry TASK\nlinkget retry TASK --rendered-backend neo\nlinkget retry TASK --redownload\nUse read retry for retained reading folders.'),
 'recover':('List retained downloads or save a complete checked task. --verify verifies Photos originals and can remove verified staged copies.',
            (COMMON-{'article','no_assets','audio_format','clip','max_duration','max_size'})|{'verify','item'},
            'linkget recover\nlinkget recover TASK --folder ~/Downloads\nlinkget recover TASK --verify\nUse tasks for read-only inspection; recovery can save or remove checked staging files.'),
 'batch':('Download multiple posts with bounded concurrency and per-site pacing.',
          COMMON|{'save_as','video','article_folder','jobs','rendered_backend'},
          'linkget batch URL URL --folder ~/Downloads\nlinkget batch --jobs 2\nOmit links to paste one per line. Each item keeps its task/recovery evidence.'),
 'clean':('Inspect or remove old temporary staging directories from completed tasks while preserving active, incomplete, unverified-Photos, symlink, and user folders.',
          {'older_than','dry_run','apply','json','non_interactive'},
          'linkget clean\nlinkget clean --older-than 7d --json\nlinkget clean --apply --older-than 3d --json\nDefault is a read-only dry run. Pass --apply to delete eligible linkget-* staging folders.'),
 'mcp':('Start a thin stdlib JSON-RPC 2.0 stdio MCP server exposing inspect_url, download_media, batch_download, read_content, get_task, retry_task, clean_stages, and doctor.',
        set(),
        'linkget mcp\nSpeak newline-delimited JSON-RPC 2.0 or Content-Length-framed MCP messages on stdin/stdout.'),
 'auth':('Check saved logins and public access. Select a site for explicit connection. No profile is copied by help.',
         {'browser','profile','non_interactive'},'linkget auth\nlinkget auth bilibili\nNoninteractive checks do not automatically export browser credentials.'),
 'logout':('Remove the selected saved login and block automatic browser access until explicitly reconnected.',
           {'json','non_interactive'},'linkget logout bilibili\nlinkget logout all'),
 'compare':('Compare two existing evidence reports without downloading.',{'json','non_interactive'},'linkget compare first.json second.json'),
 'sites':('List supported media routes and their scope.',{'json','non_interactive'},'linkget sites'),
}


def show(parser,tokens):
    command=tokens[1] if len(tokens)>1 and tokens[0]=='help' else tokens[0] if tokens else None
    if command not in SPECS or not (tokens[0]=='help' or any(item in {'-h','--help'} for item in tokens[1:])):
        return False
    description,options,examples=SPECS[command]
    focused=argparse.ArgumentParser(prog='linkget '+command,description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,epilog='Examples:\n'+examples)
    if command in {'retry','recover'}:focused.add_argument('task',nargs='?',metavar='TASK')
    elif command=='batch':focused.add_argument('links',nargs='*',metavar='LINK')
    elif command=='inspect':focused.add_argument('target',metavar='URL_OR_PATH')
    elif command in {'auth','logout'}:focused.add_argument('site',nargs='?' if command=='auth' else None,metavar='SITE')
    elif command=='compare':focused.add_argument('reports',nargs=2,metavar='REPORT')
    destination=focused.add_mutually_exclusive_group() if {'folder','photos'}<=options else None
    for action in parser._actions:
        if action.dest in options:
            selected=copy(action)
            overrides={
                'config':{'folder':'Save a fixed default folder; omit PATH for the current folder on each run.',
                          'photos':'Save Photos as the default destination.',
                          'audio_folder':'Save a default folder for audio and music downloads; omit PATH for the current folder on each run.',
                          'clear_audio_folder':'Clear the saved default audio folder.',
                          'article_folder':'Save a default folder for article downloads; omit PATH for the current folder on each run.',
                          'clear_article_folder':'Clear the saved default article folder.',
                          'x_article_photos':'Render X Articles as PNGs for Photos (disable with --no-x-article-photos).',
                          'reader_browser':'Save the personal page reader; auto follows the saved/system browser.'},
                'doctor':{'folder':'Check destination writability; no media is saved.',
                          'photos':'Check Photos staging prerequisites; no import.',
                          'live_photos':'Check local pairing prerequisites; no import.',
                          'browser':'Preview selection; none disables live diagnostic checks.'},
                'auth':{'browser':'Select a login source for an explicit connection; none prevents browser export.'}}
            selected.help=overrides.get(command,{}).get(action.dest,action.help)
            (destination if destination is not None and action.dest in {'folder','photos'} else focused)._add_action(selected)
    focused.print_help()
    return True
