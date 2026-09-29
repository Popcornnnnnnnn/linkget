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
linkget                  # paste a link when prompted; save to Photos
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
| `linkget auth` | Check website logins; connect missing sites interactively |
| `linkget logout bilibili` | Remove Bilibili login and disable its automatic browser access |
| `linkget logout all` | Disconnect all sites |

`--folder [PATH]` selects a folder; omit the path for the current directory. Without `--folder`, the destination is Photos. `--date-now` changes the date of new Photos items. Files are not overwritten.

## Supported posts

| Website | Media |
| --- | --- |
| Instagram | Post photos, videos and Reels |
| X / Twitter | Post photos and videos |
| Bilibili | BV/av videos and Opus media; one selected part per URL |
| TikTok | Photo posts and videos, including supported share links |
| Douyin | Photo posts and videos, including copied share text and short links |
| Xiaohongshu (trial) | Note image sets and videos; full links and `xhslink.com` shares |
| Weibo | Post photos and videos; mobile links, video pages and `t.cn` shares |
| YouTube | Single videos and Shorts; `youtu.be` shares |

Live streams, playlists and whole-account downloads are outside this trial. Post text and photo-post background music are not saved. Website availability and account access still apply.

Keep the full Xiaohongshu share link, including `xsec_token` when present. Removing or reusing an expired access token can make an otherwise visible note unavailable to the downloader. Xiaohongshu image sets preserve the order and full web display variants; Live Photo motion and background music are not included. This new adapter still needs live user acceptance with current share links.

YouTube links with a playlist parameter download only the explicitly selected video. Playlist-only URLs and active/upcoming streams are rejected. A browser login does not bypass membership, geographic restrictions or website verification.

## Browser connection

On first interactive use, choose Chrome, Firefox, Edge or Safari; a supported default browser is preselected. You can skip this. The chosen browser is read once and supported website sessions are checked concurrently before being saved. Failed verification is not reported as a successful connection.

On macOS, Chrome/Edge may need Keychain access. The system request may name `security` and the browser's **Safe Storage** item. Safari may need terminal access under System Settings > Privacy & Security. A denied request does not trigger prompts for every other browser.

Normal downloads use public access first. Bilibili uses an already-connected login before selecting formats so anonymous quality restrictions do not hide available quality. When a download needs a login, linkget can refresh the previously chosen browser; if you need to sign in, the same command provides the website URL and waits for Enter. There is no separate login/import/refresh command.

Use `linkget auth` to connect a website before downloading. If all websites are connected, it prints the results and exits. Network check failures do not remove saved logins. After logout, reconnecting requires your explicit choice.

`--browser none` disables all login use. `--browser firefox --profile '/path/to/profile'` selects a specific browser and account profile. Otherwise the download engine chooses its recently used profile. Noninteractive commands do not run setup or automatically read browsers; explicitly requesting `--browser` still requests that browser.

## Quality and Photos originals

- Bilibili and YouTube preserve the best available MP4 video stream, including HEVC/AV1 where offered, and merge audio without re-encoding. Resolution, codec and frame rate are shown when available. Login and paid-tier restrictions still apply; linkget cannot grant higher account permissions.
- Bilibili platform watermarks are accepted to preserve source quality. There is no crop, blur or watermark repair step.
- Douyin only accepts the currently recognized clean media sources; if none are available, it fails instead of silently using a known watermarked stream. This does not detect or remove marks baked in by an author.
- Xiaohongshu and Weibo keep the media supplied by their web pages. No watermark-free guarantee is made for these new adapters; embedded marks are not removed.
- **Photos mode retains a full-resolution original** in `~/Library/Application Support/linkget/originals/`. This intentionally uses additional disk space and protects referenced Photos items from temporary-file cleanup.
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
