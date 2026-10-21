# Offline upstream compatibility checks

Run from a source checkout with Python 3.10+:

```sh
PYTHONPATH=src python3 scripts/check_compatibility.py
# Optional: write a new private report; never overwrite an earlier result.
PYTHONPATH=src python3 scripts/check_compatibility.py --output /tmp/linkget-compat-NEW.json
```

The default command makes no website requests, opens no browser or Photos, reads no saved account/session store, and installs nothing. It records Python, OS and linkget versions, whether engine executables are on PATH, and the Python-package versions visible to this interpreter. Package presence does not establish that its corresponding executable is the same version. Missing versions remain `null`; engine version processes are not run by default.

The seven metadata/command fixtures in `tests/fixtures/compatibility/cases.json` are newly generated and redistributable. They cover selected routes, public-only command construction, an incomplete source-counted gallery, Bilibili picture/motion pairing and an unknown source count. Their fake bytes are not media-decoding tests. The command also reuses the existing six-case `tests/test_instagram.py` suite, which replaces its upstream login/post acquisition with synthetic data. DNS, socket connections and subprocess creation are blocked while that suite imports and runs, so a future upstream initialization change fails the check instead of making a request. It does not repeat the real-URL media corpus or run `validate_media.py`.

If gallery-dl's Python package is absent, five extractor tests are skipped and the report is `partial`, not a full engine compatibility pass. If the package is present but the existing suite cannot import its extractor interface, the report is `failed` with `extractor_import_failed`; that needs inspection rather than being treated as an absent optional dependency. The command-boundary test still runs. `--require-extractor` makes a partial result nonzero. Run with an already installed, trusted gallery-dl Python environment to cover its actual parser without accessing Instagram:

```sh
# Typical Apple Silicon Homebrew location; use your existing installation.
PYTHONPATH=src /opt/homebrew/opt/gallery-dl/libexec/bin/python \
  scripts/check_compatibility.py --require-extractor
```

Extractor coverage has an explicit identity contract: the five existing `InstagramExtractorTests` cases for a missing downloaded resource, mixed gallery order/count, Reel music exclusion, missing source media, and story music exclusion must each be collected once and actually succeed. The report's `extractor.coverage` lists required, collected, executed, successful, skipped, missing and duplicate identities. Zero tests, a command-only suite, renamed/missing cases, duplicate cases or collected-but-unexecuted cases are `failed` with `coverage_drift`; they are not reported as an absent optional dependency. Skipped cases never satisfy the contract, and `--require-extractor` exits nonzero until all named extractor cases pass. When deliberately replacing or renaming these fixtures, review and update the contract together with their equivalent behavioral coverage.

To record executable versions, explicitly select the local probe. Use it only for already installed, trusted programs on PATH:

```sh
PYTHONPATH=src python3 scripts/check_compatibility.py --probe-engines
```

This runs only `gallery-dl --config-ignore --version`, `yt-dlp --ignore-config --version`, `ffmpeg -version` and `ffprobe -version`, each with an eight-second timeout and no stdin. It does not install, update, download or invoke a site's extractor. Reports keep a short version label and discard raw stdout/stderr, absolute executable paths and exception traces. Version-probe failure or absence is recorded separately from fixture failure; it is not proof that a website changed. There is no network option in this command.

## Test matrix and interpreting failures

| Boundary | Reproducible evidence | What it can establish |
| --- | --- | --- |
| linkget routes and engine command construction | Seven generated fixtures, normal Python | A local routing/metadata contract changed; not whether an engine accepts every option or a site is reachable |
| gallery-dl and the linkget Instagram extension | Reused synthetic upstream extractor suite under gallery-dl's Python | Parser/extension compatibility for those fixture shapes; failures still need inspection to attribute them to gallery-dl or linkget |
| Installed tool environment | Optional bounded version commands | Which local executable version answered; not extraction availability or quality |
| Website/platform | Explicitly `not_checked` | No online claim can be made from this report |
| Photos and playback | Outside this check | No library import, permissions or playback claims |

The existing CI matrix uses Linux/Python 3.10 and macOS/Python 3.14, with gallery-dl 1.32.14. This command does not change that workflow. For a candidate engine upgrade, save a report from the current trusted environment and one from the candidate environment, then compare the engine versions, fixture IDs, result status and actual skipped cases. Do not infer the cause from version numbers alone. A missing package, command-contract failure and native upstream extractor failure are different evidence. A fresh permitted online sample is needed to attribute a failure to the platform; that is a separate explicit action, with browser access disabled unless separately authorized. No online check is run or scheduled here.

Exit codes: `0` means the available offline checks passed (possibly `partial` if the optional extractor is absent); `1` means a fixture failed, or `--require-extractor` was unmet; `2` means the check/report could not be completed. An existing report is preserved on failure. A `passed` report always retains `platform.status = not_checked`.

Run the command's own safety/contract tests with:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_compatibility.py -v
```
