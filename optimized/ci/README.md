# CANIS — CI setup

This folder holds one file meant to leave `optimized/`: a GitHub Actions
workflow that runs the pytest suite (`optimized/tests/`, 51 tests) on every
push and pull request.

## Where to put it in the real repo

GitHub only picks up workflows from a specific path: `.github/workflows/`
at the **repository root** (not inside `optimized/`, not inside `ci/` —
GitHub Actions ignores workflow files anywhere else).

1. In the actual (non-`optimized/`) CANIS repo, create the folder if it
   doesn't exist yet:
   ```bash
   mkdir -p .github/workflows
   ```
2. Copy the workflow file there, keeping (or choosing) a descriptive name:
   ```bash
   cp optimized/ci/github-actions-tests.yml .github/workflows/tests.yml
   ```
3. Commit `.github/workflows/tests.yml` and push. GitHub picks it up
   automatically — no dashboard configuration needed. Check the "Actions"
   tab of the repo to watch the first run.

The workflow assumes the real repo keeps the `optimized/` folder (or
whatever it's renamed to at the Phase-10 reorg — see
`reviews/ROADMAP.md` D1) with `tests/`, `dashboard/`, and `vision/` in the
same relative layout used here. If the reorg flattens `optimized/` into
the repo root, update the two `optimized/` path references inside the
workflow (`pytest optimized/tests/ -q` and the `requirements.txt` install
path) to match the new location.

## What it runs

`pytest optimized/tests/ -q` — the 51 pure-logic tests: state machine
timing/hysteresis, dt clamp, reward gating, level-1 safety rewrite, vision
latch TTL, marker-loss grace + frozen-frame guard, dispenser fault policy,
event log + session aggregation, and the `.h`↔`.py` config parity check.
No Arduino toolchain, no OpenCV, no camera, and no hardware are required —
everything here is stdlib Python plus Flask/waitress (the dashboard's own
runtime dependency, needed because two test files import the dashboard
package).

## Badge

Once the workflow file lives at `.github/workflows/tests.yml` in the real
repo, add this near the top of the repo's `README.md` (swap `OWNER/REPO`
and the workflow filename if you named it differently):

```markdown
[](https://github.com/OWNER/REPO/actions/workflows/tests.yml)
```

It renders as a live "passing"/"failing" badge that links straight to the
latest run — a small, honest signal that the 51 tests actually run on
every commit, not just on the author's laptop.
