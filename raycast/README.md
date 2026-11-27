# Linkget for Raycast

Control your local [`linkget`](https://github.com/Popcornnnnnnnn/linkget) CLI directly from Raycast with a focused two-command workflow:

- **Default Destinations:** Photos and videos save to **Apple Photos**; offline articles and audio default to **`~/Downloads`**.

## Requirements

- macOS with **Raycast** installed
- **`linkget >= 0.4.0`** on your system (default: `/opt/homebrew/bin/linkget` via Homebrew: `brew install Popcornnnnnnnn/tap/linkget`), which includes `--as`, batch paste, and X Article Photos support.

> **Tip:** You can also customize your permanent CLI defaults at any time:
> ```sh
> linkget config --photos --article-folder ~/Downloads
> ```

## Commands

| Command | Mode | Description |
| --- | --- | --- |
| **Save Link** (`lg`) | Form | Paste one or more URLs or share text, review what will be saved and where, and override **Auto** with **Photos**, **Video**, **Article**, **Audio**, or **Transcript or Text**. Supports multi-link batch paste, **X Article → Apple Photos** PNG import, and background saves (`⌘B`). |
| **Status Board** (`lgs`) | List + Detail | Unified board showing recent downloads & saved articles (readable titles, clean dates, Apple Photos & article previews, toggle detail pane `⌘⇧D`, in-Raycast reader, copy Markdown, open `article.html`, reveal in Finder, retry/cancel tasks) alongside a pinned CLI health & staging overview and expandable download engines, site sessions, update checks, and confirmed `linkget clean`. |

> **Obsidian / Notion tip:** Set `linkget config --article-folder ~/path/to/Vault/Clippings` (or **Default Article Archive Folder** in Raycast Preferences) to archive articles straight into your notes folder, or use **Copy Article Markdown** (`⌘C`) from the result detail or inline reader.

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
