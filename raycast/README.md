# Linkget for Raycast

Control your local [`linkget`](https://github.com/Popcornnnnnnnn/linkget) CLI directly from Raycast with a focused two-command workflow:

- **Default Destinations:** Photos and videos save to **Apple Photos**; offline articles and audio default to **`~/Downloads`**.

## Requirements

- macOS with **Raycast** installed
- **`linkget >= 0.3.2`** on your system (default: `/opt/homebrew/bin/linkget` via Homebrew: `brew install Popcornnnnnnnn/tap/linkget`)

> **Tip:** You can also customize your permanent CLI defaults at any time:
> ```sh
> linkget config --photos --article-folder ~/Downloads
> ```

## Commands

| Command | Mode | Description |
| --- | --- | --- |
| **Save Link** (`lg`) | Form | Paste a URL or share text (or auto-fill from clipboard / active browser tab via `⌘⇧B`), review live `linkget inspect` detection, choose parameters below (Mode, Destination, Live Photos, Article Assets, Audio Format, Keyframes/OCR, or toggle **Advanced** for Clip Range, Max Duration/Size, and Browser Session), and either wait for the result (still continues in background if Raycast closes) or launch immediately in background (`⌘B`). |
| **Status Board** (`lgs`) | List + Detail | Unified board showing recent downloads & saved articles (readable titles, clean dates, Apple Photos & article previews, toggle detail pane `⌘⇧D`, in-Raycast reader, open `article.html`, reveal in Finder, retry/cancel tasks) alongside a pinned CLI health & staging overview and expandable download engines, site sessions, update checks, and confirmed `linkget clean`. |

## Preferences

- **Linkget Binary Path:** Defaults to `/opt/homebrew/bin/linkget` (falls back to `/usr/local/bin/linkget` when left at default).
- **Default Article Archive Folder:** Optional folder for offline article archives (defaults to `~/Downloads` when unset).
- **Default Media & Audio Folder:** Optional folder for photos and videos (leave blank to save pics and videos to **Apple Photos**; audio falls back to `~/Downloads`).
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
