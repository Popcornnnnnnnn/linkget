# linkget Working Agreement

## Standard Verification Gate

Before committing or opening a PR, run the offline unit test suite (and package build when changing packaging/CLI entrypoints):

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -q < /dev/null
```

Keep tests offline with zero personal library, browser, or network dependencies and zero machine-specific paths.

## Major-Update Formal State-Machine Check

Before finalizing a major update or any change touching Photos shared-original staging/cleanup, `--redownload` receipt handling, batch coordinator shutdown, or detached read-task monotonicity (`src/linkget/photos.py`, `src/linkget/live.py`, `src/linkget/destinations.py`, `src/linkget/recovery.py`, `src/linkget/cli.py`, `src/linkget/batch.py`, `src/linkget/runtime.py`, `src/linkget/read_tasks.py`, `src/linkget/read_store.py`, `src/linkget/content.py`, `src/linkget/read_formats.py`):

1. Follow `.agents/skills/verify-media-lifecycle/SKILL.md` (mirrored at `.claude/skills/verify-media-lifecycle/SKILL.md`).
2. Run `formal/LinkgetMediaLifecycle.tla` with `/statechart` into a fresh `/tmp` output folder; verify all invariants and `NoDeadlock` pass.
3. If covered files changed past the skill's `sync_commit`, update the model and confirm a scratch mutation fails before advancing `sync_commit`.
4. Replay any TLC counterexample against the real Python modules in `tests/test_lifecycle_invariants.py` before treating it as a product bug or model drift.
