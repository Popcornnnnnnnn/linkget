# Linkget for Raycast

Control your local [`linkget`](https://github.com/Popcornnnnnnnn/linkget) CLI directly from Raycast to inspect URLs, archive X/web/WeChat/Zhihu/Toutiao articles offline, save photos/videos/audio, browse recent downloads, and manage local stage cleanup.

## Requirements

- macOS with **Raycast** installed
- **`linkget >= 0.3.2`** on your system (default: `/opt/homebrew/bin/linkget` via Homebrew: `brew install Popcornnnnnnnn/tap/linkget`)

> **Tip:** Set a permanent default folder for offline articles once with:
> ```sh
> linkget config --article-folder ~/Documents/Articles
> ```
> Or set **Default Article Archive Folder** in Extension Preferences (falls back to `~/Downloads/Linkget Articles` when saving articles).

## Commands

| Command | Mode | Description |
| --- | --- | --- |
| **Save Link** (`lg`) | Form | Auto-reads clipboard or grabs the frontmost browser tab (`⌘⇧B`), runs `linkget inspect`, auto-picks Article/Media/Audio/Read mode, supports clip & size caps, and opens rich result actions. |
| **Quick Save Article** (`lga`) | No-View | One-shot offline article archive from an optional argument, clipboard URL, or active browser tab; notifies with one-click actions to open `article.html` or reveal in Finder. |
| **Browse Downloads & Articles** (`lgt`) | List + Detail | Browse the latest 50 download and background read tasks, filter by Articles / Media / Reads / Needs Attention, preview cover images & Markdown inside Raycast, open `article.html`, read full Markdown, or retry failed items. |
| **Linkget Status & Maintenance** (`lgs`) | List + Detail | Inspect CLI version, saved destination defaults, installed engines (`gallery-dl`, `yt-dlp`, `ffmpeg`, `ffprobe`, `deno`), site sessions, GitHub update checks, and confirmed `linkget clean` staging cleanup. |

## Preferences

- **Linkget Binary Path:** Defaults to `/opt/homebrew/bin/linkget` (automatically falls back to `/usr/local/bin/linkget` when left at default).
- **Default Article Archive Folder:** Used for article downloads when `linkget config --article-folder` is not configured.
- **Default Media & Audio Folder:** Optional override when you prefer a folder over `linkget config` (audio falls back to `~/Downloads`).
- **Default Browser Session:** `auto`, `none`, `chrome`, `safari`, `firefox`, `edge`, or `brave`.
- **Auto-Detect Clipboard URL:** Pre-fills **Save Link** when the clipboard contains a URL or sharing text.

## Local Development & Loading into Raycast

```sh
cd raycast
npm install
npm test
npm run lint
npm run build
npm run dev
```

Or in Raycast, open **Import Extension** and select the `raycast/` folder.
