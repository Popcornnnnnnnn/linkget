# Install, update, uninstall

## macOS: recommended

Install [Homebrew](https://brew.sh/) first if it is not installed. Then:

```sh
brew install Popcornnnnnnnn/tap/linkget
linkget
```

The tap installs Python, gallery-dl, yt-dlp, ffmpeg and Deno as dependencies. Deno runs YouTube's JavaScript challenges. Homebrew may ask you to confirm trust in this third-party formula. After installation, `linkget` is available on PATH; follow Homebrew's shellenv instructions if `brew` itself is not on PATH.

To update the trial and download engines:

```sh
brew update
brew upgrade linkget gallery-dl yt-dlp ffmpeg deno
```

To uninstall:

```sh
linkget logout all     # optional: remove saved website sessions first
brew uninstall linkget
```

Homebrew may also remove dependencies installed only for this tool when no other installed package needs them. Dependencies explicitly installed by you, or still required elsewhere, are preserved. Inspect unused dependencies with `brew autoremove --dry-run`; `brew autoremove` removes them. It applies to all unused Homebrew dependencies, not just linkget.

Your downloads, Photos items, and linkget user data are not removed by package uninstallation.

## User data

On macOS, data lives in `~/Library/Application Support/linkget/`:

| Path | Contents | Removal |
| --- | --- | --- |
| `sessions/` | Website login copies; restricted to your user | `linkget logout all` removes them |
| `preferences.json` | Default save destination, browser choice and disconnected sites | Can be removed manually after uninstall |
| `originals/` | Full-resolution originals used for Photos imports | Keep these files: Photos may reference them |

**Do not delete `originals/` just to uninstall linkget.** If Photos uses referenced files, deleting originals breaks the items in your library. To reclaim the retained copies, first use Photos > File > Consolidate for those items and verify that the library has its own originals. linkget does not automate this operation.

`LINKGET_HOME=/path/to/data` overrides the data directory for isolated testing or a portable setup. Do not point it to a temporary folder when importing into Photos, because that folder will hold the retained originals.

## Wheel alternative

The GitHub release includes a wheel. Use an isolated installer:

```sh
uv tool install ./linkget-0.2.0b4-py3-none-any.whl
# or: pipx install ./linkget-0.2.0b4-py3-none-any.whl
```

This installs linkget but not its external download engines. On macOS:

```sh
brew install gallery-dl yt-dlp ffmpeg deno
linkget doctor
```

Uninstall with the same installer (`uv tool uninstall linkget` or `pipx uninstall linkget`). Avoid keeping both a Homebrew and a uv/pipx installation on PATH: use `which -a linkget` to see which command is running.

No PyPI release is available for this trial. Do not assume a similarly named package belongs to this project.
