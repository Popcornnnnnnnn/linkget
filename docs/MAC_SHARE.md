# LinkGet for Mac

The desktop companion and Share extension were uninstalled on 2026-10-08 at the user's request. LinkGet is maintained and used as a command-line tool; its installed CLI, saved settings and content were preserved. The following describes the earlier optional implementation, not the current installed product. No desktop bundle is installed or registered on this Mac.

The native companion offers Read / Download, a link or local-file field, Choose file, Start, Cancel, Details and Open result. Read uses the CLI's saved model/language/export settings. Results are saved under Downloads. Download saves media to a new folder. It does not silently import into Photos.

It locates `/opt/homebrew/bin/linkget` or `/usr/local/bin/linkget` and requires the CLI to be installed. Read accepts local media; Download expects a supported web link. No shell command interpolation is used. Cancel signals the isolated CLI job and its media subprocesses; read checkpoints remain available through `linkget read recover` / `retry`.

The Share extension appears as **LinkGet** in compatible system Share menus. Choose Read or Download, then **Open in LinkGet**. The host presents the input; click **Start** to process it. File sharing resolves Finder's file-reference URLs before handoff. Read/Download Services are also registered for text/URL selections.

Both panels default to English and Light. Language and Light/Dark/System choices are saved locally; the host and sandboxed extension keep their own preferences. The share extension only passes input to the host and does not run downloaders or access browser credentials.

## Build and install

On Apple Silicon with Xcode/Swift and Python available:

```sh
python3 macos/build.py /path/to/empty/build-folder
```

The builder creates an ad-hoc signed `LinkGet.app` containing `LinkGetShare.appex`; it does not install or open anything. Copy the resulting app to `/Applications/LinkGet.app`, register it with LaunchServices and register/enable the extension with PlugInKit, or enable it through the system's extension settings if necessary. The source targets macOS 13+ on arm64. Intel, a fresh Mac and distribution signing/notarization are not validated.

Installed identities:

- App: `com.popcorn.linkget`
- Share: `com.popcorn.linkget.share`
- Historical companion: `0.2.1.dev14` (removed); current CLI: `0.2.1.dev17`.

Actual acceptance includes the installed window, local image reading, opening the result folder, Finder's Share menu entry and handoff into the host. English/Chinese and Light/Dark were checked in the real window; returning to English/Light was confirmed. Desktop Download saved the YouTube sample as MP4. Cancel stopped a generated 768-second local audio task, retained source media, wrote a cancelled checkpoint and left no media-tool process or partial content result. Installed `read retry` subsequently processed both chunks, completed the read and cleaned retained media. The first menu-localization implementation crashed at startup because macOS adds menu items; the corrected tagged-item lookup passed normal launch and cold Share-to-host launch. A native hover treatment is implemented, but the automation tool has no pointer-hover action, so exhaustive hover verification remains manual.
