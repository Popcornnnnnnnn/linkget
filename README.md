# linkget

Save photos and videos from a post link to **macOS Photos** or a folder.
A small command-line tool with English prompts, browser login reuse and no hosted service.

**Public trial · macOS first · MIT license**

## Install

With [Homebrew](https://brew.sh/):

```sh
brew install Popcornnnnnnnn/tap/linkget
linkget
```

Homebrew installs the download engines, Python and Deno (needed for YouTube). See [installation, updates and removal](docs/INSTALL.md) for details and the wheel alternative.

## Use

The simplest way avoids shell quoting entirely:

```sh
linkget                  # paste a link; use your default (initially Photos)
linkget --photos         # save to Photos this time
linkget --folder         # paste a link; save to the current folder
linkget --folder ~/Downloads
```

Or pass a URL on the command line:

```sh
linkget 'https://www.bilibili.com/video/BV1bK411W797/' --folder
```

Use straight ASCII quotes (`'` or `"`) around command-line URLs. Shells interpret `&`, `?` and other characters before linkget runs; curly quotes (`“ ”`) do not protect a URL. Shared post text containing one supported URL is also accepted at the paste prompt.

| Command | Purpose |
| --- | --- |
| `linkget help` | Commands, options and examples |
| `linkget sites` | Supported websites and post types |
| `linkget doctor` | Check download tools, without account requests |
| `linkget config` | View or change the default save destination |
| `linkget auth` | Check website logins; connect missing sites interactively |
| `linkget logout bilibili` | Remove Bilibili login and disable its automatic browser access |
| `linkget logout all` | Disconnect all sites |

`--folder [PATH]` selects a folder; omit the path for the current directory. `--photos` selects Photos. Without either option, linkget uses your saved default, initially Photos. The two options cannot be combined. `--date-now` changes the date of new Photos items; use `--photos --date-now` when your default is a folder. Files are not overwritten.

## Default save destination

```sh
linkget config                       # show the current default
linkget config --photos              # default to Photos
linkget config --folder              # default to the current folder on each run
linkget config --folder ~/Downloads  # default to a fixed folder
```

Relative folder paths supplied to `config --folder PATH` are resolved when saved. Bare `config --folder` follows your working directory on each subsequent download. Configuration does not create the destination folder; it is created when downloading.

Download options override the saved default for that run only. The `Destination` line shows the actual resolved destination. Updating the default preserves your browser choice and saved website sessions.

## Supported posts

| Website | Media |
| --- | --- |
| Instagram | Post photos, videos and Reels |
| X / Twitter | Post photos and videos |
| Bilibili | BV/av videos and Opus media; one selected part per URL |
| TikTok | Photo posts and videos, including supported share links |
| Douyin | Photo posts and videos, including copied share text and short links |
| Xiaohongshu (trial) | Note image sets and videos; full links, `xhslink.com` and `xhslink.cn` shares |
| Weibo | Post photos and videos; mobile links, video pages and `t.cn` shares |
| YouTube | Single videos and Shorts; `youtu.be` shares |

Live streams, playlists and whole-account downloads are outside this trial. Post text and photo-post background music are not saved. Website availability and account access still apply.

Keep the full Xiaohongshu share link, including `xsec_token` when present. Removing or reusing an expired access token can make an otherwise visible note unavailable to the downloader. Xiaohongshu image sets preserve their order and original formats, including HEIC/HEIF; Live Photo motion and background music are not included. This adapter still needs live user acceptance with current video share links.

YouTube links with a playlist parameter download only the explicitly selected video. Playlist-only URLs and active/upcoming streams are rejected. A browser login does not bypass membership, geographic restrictions or website verification.

## Browser connection

On first interactive use, choose Chrome, Firefox, Edge or Safari; a supported default browser is preselected. You can skip this. The chosen browser is read once and supported website sessions are checked concurrently before being saved. Failed verification is not reported as a successful connection.

On macOS, Chrome/Edge may need Keychain access. The system request may name `security` and the browser's **Safe Storage** item. Safari may need terminal access under System Settings > Privacy & Security. A denied request does not trigger prompts for every other browser.

Normal downloads use public access first. Bilibili uses an already-connected login before selecting formats so anonymous quality restrictions do not hide available quality. When a download needs a login, linkget can refresh the previously chosen browser; if you need to sign in, the same command provides the website URL and waits for Enter. There is no separate login/import/refresh command.

Use `linkget auth` to connect a website before downloading. If all websites are connected, it prints the results and exits. Network check failures do not remove saved logins. After logout, reconnecting requires your explicit choice.

In an interactive terminal, `auth` automatically refreshes expired or rejected saved logins from the previously connected browser/profile. Sites using the same browser/profile share one read. Only newly verified sessions replace saved data; network errors, rate limits and website verification challenges do not trigger automatic refresh. `--browser none` and noninteractive account checks never read browsers automatically.

`--browser none` disables all login use. `--browser firefox --profile '/path/to/profile'` selects a specific browser and account profile. Otherwise the download engine chooses its recently used profile. Noninteractive commands do not run setup or automatically read browsers; explicitly requesting `--browser` still requests that browser.

## Quality and Photos originals

- Bilibili and YouTube preserve the best available MP4 video stream, including HEVC/AV1 where offered, and merge audio without re-encoding. Resolution, codec and frame rate are shown when available. Login and paid-tier restrictions still apply; linkget cannot grant higher account permissions.
- Prefer sources without platform watermarks. When no usable clean alternative is available, keep the available media. There is no crop, blur, re-encoding or AI watermark repair; marks embedded in an upload may remain.
- Douyin prefers recognized clean image variants and ordinary video playback. Xiaohongshu prefers original images and the exposed original video. If these are unavailable, exposed fallback media are retained with a notice; one unavailable original does not downgrade the rest of a gallery.
- Weibo JPEG images use the `oslarge` rendition when available. This removed platform marks in the checked samples, but can reduce resolution; the CLI reports the number of smaller images. If unavailable, it keeps the downloaded copy and warns. Videos retain the best exposed source and may contain embedded marks.
- Instagram, X and TikTok use playback/original media from the download engine. Bilibili and YouTube keep the best available stream; embedded logos are retained when no clean alternative is available. These are source-selection strategies, not an automatic watermark detector or a guarantee for every post. See the [eight-channel media audit](docs/WATERMARKS.md).
- **Photos mode retains the downloaded file unchanged** in `~/Library/Application Support/linkget/originals/`. This can be a smaller clean rendition, not necessarily the largest platform image. Retention uses additional disk space and protects referenced Photos items from temporary-file cleanup. Previously downloaded or imported marked copies are not modified or deleted; download the link again for the new source selection.
- Originals use a SHA-256-derived filename: different contents with the same download name stay distinct; identical contents are reused. Imports made by older versions may be imported once again because they did not use these filenames.
- Some codecs may not be supported by your Photos/macOS version. If importing fails, the original is retained and linkget reports its location; `--folder` avoids the Photos importer. Files are never transcoded to hide this limitation.

Do not remove retained originals until you have consolidated referenced items in Photos. See [data retention and uninstall](docs/INSTALL.md#user-data).

## Privacy and support

No website passwords are collected and no credentials are sent to the project author. Saved sessions and temporary browser exports are local, with user-only file permissions; temporary exports are removed. Website checks send the relevant session only to its website. There is no telemetry.

Include `linkget --version`, `linkget doctor`, the website and the visible error in a [bug report](https://github.com/Popcornnnnnnnn/linkget/issues). Do not attach cookie files or private account data. Network errors, expired sessions and unavailable posts are different failures.

## Development

Python 3.10+; external engines are gallery-dl, yt-dlp and ffmpeg, with Deno for YouTube's JavaScript challenges. Core code uses only the Python standard library.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -q
uv build
```

See [validation scope](docs/TESTING.md). Actual Photos and OS permission dialogs require manual acceptance; unit tests never open personal apps or read real browser sessions.

### Development handoff — 2026-09-29

- The local checkout is `/Users/forge/Workspace/linkget`, registered as a dedicated Codex project with the original conversation. The development environment was recreated after relocation and no longer references the old checkout. Use `PYTHONPATH=src .venv/bin/python` for source validation.
- This is an independent public MIT repository with its own Homebrew tap. The release version is maintained in `src/linkget/version.py`.
- Preserve the CLI-only design, saved destination settings, original media preference and single-post restriction. `auth` manages website connections; `doctor` checks dependencies. Do not introduce a separate login command.
- Verified: all eight website checks passed with an authorized Chrome export. An expired YouTube snapshot automatically recovered using real Chrome cookies in an isolated temporary account store. Other browsers' real OS access is not thereby verified. See the validation document for automated coverage.
- YouTube diagnosis: the old saved snapshot returned `LOGGED_IN=false` and `loggedOut=true`, while a fresh Chrome export returned the opposite. Seven cookie values differed although local expiry checks passed. Refreshing from Chrome restored the saved connection. No credential values were retained in development evidence.
- Implemented: interactive `auth` refreshes explicitly rejected or expired snapshots once per authorized browser/profile, preserves sessions after failed validation, and respects logout and noninteractive checks. Cookies renewed during validation are included in newly saved snapshots.
- Before further work, verify the working directory, this README and Git status. Real permission prompts, other browser profiles and long-term behavior under upstream website changes remain acceptance limits.
