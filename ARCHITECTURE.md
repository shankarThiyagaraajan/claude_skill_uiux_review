# uiux-review — Architecture

## System Overview

`uiux-review` is a Claude skill that runs a UI/UX lead review of a real,
running product: a web app, a terminal UI (TUI) or a CLI. It captures the
product's actual screens at several sizes and states, then gives prioritized
findings with evidence and effort.

[SKILL.md](./SKILL.md) is the entry point. It defines the review in six
steps, numbered 0 to 5:

0. Scope: one question about how to run it, which jobs, where prior reviews
   live, and depth.
1. Users, jobs and context of use.
2. Capture: run the bundled scripts before judging anything.
3. Walk each job against the state matrix.
4. Judge each dimension with the checklist for the surface.
5. Calibrate, write `review.json`, run `build_report.py`, deliver the report
   and give a chat summary.

The code in [scripts](./scripts/README.md) produces the evidence (captures,
`manifest.json`, CLI probe output and `summary.json`) and renders the final
`report.html`, the cumulative `findings.json` ledger and `fixes.md`. The
Markdown files in `references/` are the checklists and the schema the
reviewer reads while judging.

Data flows in one direction within a review. The ledger feeds the next one:

```text
capture_web.py ──► captures/*.png      + captures/manifest.json
capture_tui.py ──► captures-tui/*.svg|.txt|.png + captures-tui/manifest.json
run_cli.py     ──► cli-runs/<probe>.txt + cli-runs/summary.json
(run_cli.sh execs run_cli.py)
        │
        ▼
reviewer judgment (SKILL.md + references/*.md) ──► review.json
        │                 annotate.py ──► annotated/<stem>__do.png
        ▼
build_report.py review.json [--prior <previous findings.json>]
        │  validate ─► sensitive-content scan ─► write
        ▼
report.html + findings.json (schema 2) + fixes.md
        │
        └──► next review: scope.prior_review or --prior ──► build_report.py
```

The scripts connect only through files. `review.json` names the capture
manifests in `captures_manifest` and the CLI summaries in `cli_summary`.
`build_report.py` reads them to build the contact sheets, the automated flags
and the "CLI probes" tables. It also reads the previous `findings.json` (from
`--prior` or `scope.prior_review`) to carry every earlier item forward. The
key-level contract is in
[scripts/README.md](./scripts/README.md#manifest-entries-cross-script-interface).

## Subdirectories & System Boundaries

- **scripts**: [./scripts/README.md](./scripts/README.md). Six files:
  - `capture_web.py`: Playwright web capture with axe, overflow, small-target
    and focus-walk checks, URL and secret redaction, field masking and
    third-party origin tracking. Also importable as `Capturer`.
  - `capture_tui.py`: Textual pilot or pty snapshots, with cut-text counts
    and an environment scrub in pty mode.
  - `run_cli.py`: CLI ergonomics probes with timeouts, a scratch `HOME` and
    working directory, and an environment scrub. No shell, no `eval`.
  - `run_cli.sh`: a wrapper that `exec`s `run_cli.py`.
  - `annotate.py`: numbered Pillow callouts on a capture.
  - `build_report.py`: validates `review.json`, scans for sensitive content,
    and writes the report, ledger and fixes list.
- **references**: Markdown only, so it has no contract. It holds
  `heuristics.md`, `web-checklist.md`, `tui-checklist.md`,
  `cli-checklist.md`, `state-matrix.md` and `review-schema.md`.
  `review-schema.md` documents the `review.json`, `findings.json` and
  `fixes.md` fields and the rules `build_report.py` enforces.

## Global Invariants

- Finding IDs are never reused. `build_report.py:validate` rejects a new ID
  that matches a `prior[]` entry or any prior-ledger item, and, when a prior
  ledger is loaded, a new ID numbered below its `next_id`.
  `merge_ledger` sets `next_id` to one more than the highest trailing number
  ever issued.
- The ledger is cumulative. Every prior-ledger item is carried into the new
  `findings.json`. Each item that is `open`, `regressed`, `partly` or
  `not-retested` must get a new status in `prior[]`, or the build fails.
  `fixed`, `partly` and `regressed` need a fresh capture; `wontfix`,
  `obsolete` and `not-retested` need a note.
- Every finding needs `id`, `title`, `priority` (`P0`–`P3`), `evidence`
  (`automated`, `judged` or `inferred`), `where`, `seen`, `why`, `do`,
  `verify` and `effort` (`S`, `M` or `L`). These are enforced by
  `build_report.py`'s `REQUIRED`, `PRIORITIES` and `EVIDENCE` constants and
  the checks in `validate`.
- Every state-matrix cell is `ok:<evidence>`, a `UX-###`, `n/a:<reason>` or
  `not checked:<reason>`. A bare `ok`, an empty cell or a missing reason is
  an error.
- `build_report.py` writes nothing if there is any error, any warning with
  `--strict`, or any sensitive-content hit without `--allow-sensitive`.
- Secrets are kept out of the evidence where the scripts can do it:
  - `capture_web.py` strips query strings and credentials from manifest
    URLs, redacts tokens in console and error text, masks password, card and
    `[data-secret]` fields, and sends `--header` values only to the app's
    origin.
  - `capture_tui.py` (pty mode) and `run_cli.py` drop secret-looking
    environment variables and record only their names.
  - `build_report.py` scans `review.json`, manifests, `.txt` captures and CLI
    probe output for common token and key formats and real-looking email
    addresses.
- Per the ground rules in `SKILL.md`, the review only reads the product. It
  changes no code, makes no paid or external calls from the product, and
  uses only seeded data and local test accounts. The scripts do not enforce
  this: they run whatever command or URL they are given.

## Global Side Effects

- The scripts run the product under review:
  - `capture_web.py` drives a headless Chromium or Chrome at `--url`.
  - `capture_tui.py` imports and runs a Textual app in its own process, or
    starts the command on a pseudo-terminal.
  - `run_cli.py` runs the command up to 10 times, once per probe, each in its
    own process group, killed after `--timeout` seconds.
- They write files only to their output folders and their own temp folder:
  - `captures/`, `captures-tui/`, `cli-runs/`, `annotated/`
  - `report.html`, `findings.json` and `fixes.md` beside `review.json`
    (or at `--out`, `--findings-out`, `--fixes-out`)
  - `run_cli.py`'s temporary `run_cli-*` folder, removed when it finishes
- No script calls an external service of its own. The software under review
  can, though:
  - the page loaded by `capture_web.py` can fetch any third-party resource
    unless `--block-third-party` is given; other origins are listed per
    capture in `third_party`
  - the command run by `run_cli.py` or `capture_tui.py`, and the imported
    Textual app, have the network access of the user running them

  This is why `SKILL.md` requires seeded data and local test accounts.
- All scripts write progress and diagnostics to stderr. `annotate.py` prints
  its output path to stdout.

## System Verification

There is no build manifest or test suite. To syntax-check the scripts, run
this from the skill root:

```sh
python3 -m py_compile scripts/capture_web.py scripts/capture_tui.py scripts/annotate.py scripts/build_report.py scripts/run_cli.py && sh -n scripts/run_cli.sh
```

`py_compile` writes `.pyc` files into `scripts/__pycache__/`.

For a runtime preflight of the web path, run
`python3 scripts/capture_web.py --check`. It exits `0` only if a browser
launches.
