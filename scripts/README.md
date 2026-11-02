# uiux-review scripts

## Overview

These are the standalone command-line tools behind the `uiux-review` skill
(see [../SKILL.md](../SKILL.md)). They cover the automated layer of a UI/UX
review. Three capture tools collect evidence from a running product:

- `capture_web.py` runs a web app in a headless browser.
- `capture_tui.py` snapshots a terminal UI.
- `run_cli.py` runs a fixed set of probes against a CLI. `run_cli.sh` is a
  three-line wrapper that `exec`s `run_cli.py`, kept for old commands.

Two tools then turn the judged findings into deliverables:

- `annotate.py` marks up a capture to show a fix.
- `build_report.py` validates `review.json`, scans for sensitive content, and
  writes `report.html`, the cumulative `findings.json` ledger and `fixes.md`.

No Python script imports another. They connect through files on disk:
captures, `manifest.json`, `summary.json`, `review.json` and `findings.json`
(see [Manifest entries](#manifest-entries-cross-script-interface) and
[run_cli summary.json](#run_cli-summaryjson-cross-script-interface)).

`capture_web.Capturer` is also meant to be imported by your own walk scripts.
That is how you capture non-default states for the state matrix:

```python
from capture_web import Capturer
with Capturer(out="captures") as c:
    page = c.new_page(width=360, height=800, scheme="light")
    c.goto(page, "http://localhost:5173/orders/new"); page.fill("#qty", "10")
    c.snap(page, route="/orders/new", state="validation-error")
# the captures are merged into captures/manifest.json when the with-block exits
```

## Interface

### `capture_web.py` — web capture and automated checks

- Entry point: `main(argv=None)`. `--url` is required unless `--check` is
  given.
- Arguments:
  - `--check`: preflight. Prints the Python and Playwright versions, the
    browser that launches, the axe source and version, and whether Pillow is
    importable, then exits.
  - `--url` (base URL), `--routes` (default `["/"]`)
  - `--out` (default `captures`), `--fresh` (start a new manifest instead of
    merging)
  - `--viewports` (default `DEFAULT_VIEWPORTS` = `360x800 768x1024 1440x900 1920x1080`)
  - `--schemes` (default `light dark`; choices `light`, `dark`)
  - `--quick`, `--no-reduced-motion`, `--no-zoom`, `--no-axe`,
    `--viewport-only`, `--full-width`
  - `--axe PATH`, `--channel`, `--executable-path`, `--storage-state`
  - `--header 'Name: value'` (repeatable), `--locale`, `--timezone`
  - `--wait` (default `load`; choices `load`, `domcontentloaded`,
    `networkidle`), `--wait-for SELECTOR`
  - `--settle` ms (default `500`), `--timeout` ms (default `20000`),
    `--tab-steps` (default `30`)
  - `--mask SELECTOR` (repeatable), `--keep-query`, `--block-third-party`,
    `--allow-origin ORIGIN` (repeatable; an origin or a bare host name)
- `--quick` replaces `--viewports` and `--schemes` with 1440x900, light only,
  and skips both variants.
- `class Capturer` is a context manager. Constructor keywords: `out`,
  `channel`, `executable_path`, `axe`, `storage_state`, `locale`, `timezone`,
  `wait` (default `"load"`), `settle_ms` (500), `timeout_ms` (20000),
  `tab_steps` (30), `headers`, `full_width`, `wait_for`, `base_origin`,
  `masks`, `keep_query`, `block_third_party`, `allow_origins`, `fresh`.
  Methods:
  - `new_page(width, height, scheme="light", reduced_motion=False)`
  - `goto(page, url)`: returns the load time in ms including the `--settle`
    wait, or `None` if the wait timed out. Raises `NavigationFailed` for a
    navigation error.
  - `snap(page, route, state="default", variant=None, run_axe=True, tab_walk=None, full_page=True, load_ms=None)`:
    returns one manifest entry.
  - `run_axe`, `walk_tabs`, `merged_captures`, `write_manifest`, `clean_url`,
    `scrub`, `is_app_url`
- Module functions: `redact`, `clean_url`, `origin_of`, `scrub`, `slug`,
  `reset_log`, `find_axe`, `axe_version`, `launch_browser`, `run_check`.
- Constants: `DEFAULT_VIEWPORTS`, `DEFAULT_MASKS`, `LOCAL_HOSTS`,
  `UNREACHABLE`, `REDACT_PATTERNS`, `INTERACTIVE` (the selector for focusable
  elements), `JS_OVERFLOW`, `JS_SMALL_TARGETS`, `JS_FOCUS_INFO`,
  `JS_PAGE_INFO`.
- Dependencies:
  - `playwright` is required. It is imported at module load.
  - axe-core is optional. `find_axe` uses `--axe`, then the
    `axe-playwright-python` package, then `./node_modules/axe-core`, then
    `~/node_modules/axe-core`. If none is found, each capture records
    `axe.skipped`.
  - Pillow is only reported by `--check`. This script does not use it.
- Exit codes:
  - `0` when the run finishes. Per-capture failures are recorded in the
    manifest and do not change the exit code.
  - `1` if Playwright is missing, no browser can be launched, the first
    navigation fails with a code in `UNREACHABLE`, or `--check` finds no
    browser.
  - `2` on an argparse usage error, including a missing `--url`.
  - A malformed `--viewports` value or an unreadable `--axe` file ends in a
    traceback (exit `1`).

### `capture_tui.py` — terminal UI snapshots

- Entry point: `main(argv=None)`. It takes two positional arguments:
  - `mode`: `textual` or `pty`
  - `target`: `module:Class` for `textual`, or a quoted command for `pty`
- Arguments:
  - `--out` (default `captures-tui`)
  - `--sizes` (default `DEFAULT_SIZES` = `80x24 120x40 200x60`)
  - `--keys` (comma-separated), `--state`, `--name`
  - `--settle` seconds (default `1.0`)
  - pty mode only: `--png`, `--env NAME=value` (repeatable),
    `--keep-env NAME` (repeatable), `--inherit-env`
- Functions: `scrub_env`, `touches_edge`, `line_cells`, `ellipsis_count`,
  `parse_sizes`, `parse_keys`, `textual_mode`, `wide_widgets`, `export_text`,
  `svg_to_text`, `pty_mode`, `feed`, `key_to_bytes`, `render_png`, `slug`.
- Constants: `DEFAULT_SIZES`, `SECRET_ENV`, `KEYMAP`.
- `export_text` uses a private Textual API (`app.screen._compositor`). If that
  fails, the `.txt` capture falls back to `svg_to_text` on the saved SVG.
- Key syntax: textual mode passes key names straight to Textual's pilot. In
  pty mode, `key_to_bytes` handles three forms:
  - names in `KEYMAP` (for example `enter`, `tab`, `shift+tab`, `esc`, the
    arrow keys, `pageup`, `f1`, `question_mark`), in any case
  - `ctrl+<letter>`
  - any other key, which is sent as its literal bytes
- Dependencies, each imported only when needed:
  - `textual` for textual mode. `rich` (installed with Textual) for
    `export_text` and `line_cells`.
  - `pexpect` and `pyte` for pty mode.
  - Pillow for `--png`.
- Exit codes:
  - `0` when the run finishes. If one size fails in textual mode, the error
    is recorded in the manifest and the script carries on.
  - `1` if the chosen mode's dependency is missing, the textual target has no
    `:Class`, or `--png` is given without Pillow.
  - `2` on an argparse usage error.
  - These end in a traceback (exit `1`): the textual module cannot be
    imported, the class is not in the module, a size is not `COLSxROWS`, an
    `--env` value has no `=`, `pexpect` cannot start the command, or the
    existing `manifest.json` is not valid JSON.

### `run_cli.py` — CLI ergonomics probes

- Usage: `run_cli.py "<command>" [--out cli-runs] [--sub "<read-only subcommand>"] [--sample "<read-only args>"] [--bad-input "<args>"] [--timeout 15] [--skip-bare] [--keep-home] [--keep-cwd] [--keep-env NAME]... [--inherit-env]`
- Entry point: `main(argv) -> int`, called with `sys.argv[1:]`. Options are
  parsed by `parse_args`, not argparse. `--opt=value` works for every option
  that takes a value.
- Arguments:
  - `<command>` is split with `shlex` (`split_words`). It is never passed to
    a shell. Extra plain words are appended to it. Everything after a lone
    `--` is appended unchecked.
  - `--out` (default `cli-runs`)
  - `--sub` (default none), `--sample` (default `DEFAULT_SAMPLE` = `--help`)
  - `--bad-input` (default `DEFAULT_BAD_INPUT` =
    `--input /definitely/missing/file.csv`)
  - `--timeout` seconds per probe (default `15`; must be a number above 0)
  - `--skip-bare`, `--keep-home`, `--keep-cwd`, `--keep-env NAME`
    (repeatable), `--inherit-env`
  - `-h` / `--help` prints the module docstring to stdout.
- Probes, in order. TARGET is the command plus `--sub`; SAMPLE is `--sample`
  or `--help`:
  1. `help-80`: `<cmd> --help` with `COLUMNS=80 LINES=24`. Its duration is
     `startup_ms`.
  2. `version`: `<cmd> --version`
  3. `bare`: `<cmd>` alone (skipped with `--skip-bare`)
  4. `wrong-flag`: TARGET `--definitely-not-a-flag`
  5. `bad-input`: TARGET plus the `--bad-input` words
  6. `sub-help`: `<cmd> <sub> --help` with `COLUMNS=80 LINES=24` (only with
     `--sub`)
  7. `tty`: TARGET SAMPLE on an 80x24 pseudo-terminal. `TERM=xterm-256color`
     is set when `TERM` is unset, empty or `dumb`. Skipped when the `pty`
     module is unavailable (`HAVE_PTY` is false).
  8. `piped`: TARGET SAMPLE with stdout and stderr on pipes
  9. `no-color`: as `piped`, with `NO_COLOR=1`
  10. `term-dumb`: as `piped`, with `TERM=dumb`
- Functions: `log`, `usage_error`, `split_words`, `parse_args`, `build_env`,
  `pick_cwd`, `resolve_exe`, `kill_group`, `popen_kwargs`, `run_piped`,
  `run_tty`, `has_ansi`, `display_width`, `lines_over`, `decode`,
  `env_prefix`.
- Constants: `DEFAULT_BAD_INPUT`, `DEFAULT_SAMPLE`, `USAGE_EXITS`
  (`2, 64, 128, 129`), `SECRET_RE`, `ANSI_RE`, `ANSI_STRIP_RE`, `NO_PAGER`,
  `USAGE`, `HAVE_PTY`, `POSIX`.
- Dependencies: Python standard library only. `fcntl`, `pty`, `struct` and
  `termios` are optional; without them the `tty` probe is skipped. On Windows,
  `taskkill` is used to kill a probe's process tree.
- Exit codes:
  - `0` after the probes ran. Findings go to `summary.json` and stderr and do
    not change the exit code.
  - `0` for `-h` / `--help`.
  - `2` on a usage error: no command, an unknown option starting with `-`
    (with a "did you mean" hint), an option without its value, a bad
    `--timeout`, a command or `--sub` / `--sample` / `--bad-input` that
    `shlex` cannot split, or a command that cannot be found.
  - `130` on Ctrl-C. The running probe is killed first.

### `run_cli.sh` — wrapper

- Body: `exec python3 "$(dirname "$0")/run_cli.py" "$@"` under `#!/bin/sh`.
- It passes every argument through. Its exit code is `run_cli.py`'s.
- Dependencies: `sh` and `python3` on `PATH`.

### `annotate.py` — callouts on a capture

- Entry point: `main(argv=None)`. It takes the image path as a positional
  argument.
- Arguments:
  - `--box X1,Y1,X2,Y2 LABEL` (repeatable)
  - `--arrow X1,Y1,X2,Y2` (repeatable)
  - `--crop X1,Y1,X2,Y2`, `--out`
  - `--color` (default `#e5484d`), `--scale` (default `1.0`)
  - `--no-legend` (labels beside the boxes instead of a legend strip under
    the image)
- Functions: `font(size)`, which tries DejaVu Sans Bold, then Arial Bold,
  then Helvetica, then Pillow's default font; and `parse_rect(s)`.
- Dependencies: Pillow is required. Without it, the script exits `1` with
  `Pillow is needed: pip install pillow`.
- Exit codes: `0` on success, `1` without Pillow, `2` on an argparse usage
  error. A bad rectangle or an unreadable image ends in a traceback (exit
  `1`).

### `build_report.py` — `review.json` to report, ledger and fixes

- Entry point: `main(argv=None)`. It takes the path to `review.json` as a
  positional argument.
- Arguments:
  - `--out` (default `report.html` beside `review.json`)
  - `--findings-out` (default `findings.json` beside `review.json`)
  - `--fixes-out` (default `fixes.md` beside `review.json`)
  - `--prior PATH`: the previous `findings.json`. Without it,
    `scope.prior_review` is used, resolved relative to `review.json`.
  - `--inline` (embed images as data URIs)
  - `--strict` (treat warnings as errors)
  - `--allow-sensitive` (build even when the sensitive-content scan has hits)
- Constants: `PRIORITIES`, `EVIDENCE`, `LEVELS`, `SCORES`, `SCORE_RANK`,
  `REQUIRED`, `PRIORITY_HELP`, `STATUSES`, `NEEDS_CAPTURE`, `NEEDS_NOTE`,
  `STILL_OPEN`, `COUNTS_AGAINST_SCORE`, `KNOWN_DIMENSIONS`, `ITEM_KEYS`,
  `IMAGE_EXT`, `UX_RE`, `CELL_GRAMMAR`, `SENSITIVE`, `EMAIL_RE`,
  `SAFE_EMAIL_DOMAINS`, `SAFE_EMAIL_TLDS`, `FILE_TLDS`, `NEUTRAL_SUMMARY`,
  `CSS`.
- Functions and classes:
  - Loading: `load_json_file`, `normalise(review) -> errors`,
    `load_prior(path, base) -> (ledger, warnings)`, `prior_by_id`
  - Validation: `validate(review, base, prior_ledger=None) -> (errors, warnings)`,
    `parse_cell`, `matrix_rows`, `open_findings_by_dim`, `suggest_score`,
    `canon_dim`, `id_num`, `id_sort_key`
  - Sensitive scan: `scan_text`, `scan_files(review_text, rpath, review, base) -> (hits, warnings)`,
    `mask`, `email_is_safe`
  - Paths: `resolve`, `relpath`, `rebase_item`, `rebaser`, `manifest_paths`
  - Rendering: `class Images` (`href`, `data_uri`, `link`, `tag`; `.txt`
    captures render as `<pre class="tui">`), `render(review, base, images, prior_ledger=None, merged=None) -> str`,
    `render_manifest`, `render_cli`, `render_cell`, `summary_chips`,
    `capture_flags`
  - Ledger and hand-off: `merge_ledger(review, prior_ledger, report_name, base) -> dict`,
    `write_ledger(ledger, base, path)`, `fixes_md(ledger, base, fixes_dir) -> str`,
    `is_quick_win`, `quick_wins`
  - Helpers: `esc`, `die`, `as_list`, `yesno`
- Dependencies: standard library only.
- Exit codes:
  - `0` when the three files are written.
  - `1` if `review.json` or the prior ledger is missing, not UTF-8, not valid
    JSON or not a JSON object (`die`).
  - `1` on any validation error, on any warning with `--strict`, or on any
    sensitive-content hit without `--allow-sensitive`. Nothing is written in
    these cases.
  - `2` on an argparse usage error.

### Manifest entries (cross-script interface)

`build_report.py:render_manifest` reads each manifest named by
`review["captures_manifest"]` (a string or a list). It uses these keys:

| Key | Written by | Used for |
| --- | --- | --- |
| `summary` (top level) | `capture_web.py:write_manifest` | Summary chips. Every key is shown; `axe_skipped` > 0 adds an "axe: not run" chip |
| `captures[]` (top level) | both capture scripts | The list of entries |
| `width` | `capture_web.py:snap` | Marks the entry as a web capture |
| `cols`, `rows` | `capture_tui.py` | Marks the entry as a TUI snapshot |
| `file` / `svg` / `png` / `txt` | web / textual / pty `--png` / textual and pty | Image path, resolved against the manifest's folder. `.txt` renders as `<pre>` |
| `route`, `scheme`, `state`, `variant` | `capture_web.py:snap` | Contact-sheet grid. Variants and non-default states go in a separate list |
| `overflow.overflow` | `JS_OVERFLOW` | `overflow` flag |
| `console_errors`, `page_errors` | `new_page` listeners | `console` flag |
| `failed_requests` | `new_page` listeners | `req` flag |
| `axe.counts.critical`, `axe.counts.serious` | `run_axe` | `axe N` flag |
| `axe.skipped` | `run_axe`, `main` (`--no-axe`) | `axe not run` flag |
| `tab_walk.invisible_focus` | `walk_tabs` | `focus` flag |
| `wait_timed_out` | `snap` | `wait timeout` flag |
| `error` | `snap`, `main` (navigation), `textual_mode` | `error` flag or note |
| `blocked` | `snap` (with `--block-third-party`) | `blocked` flag |
| `mode`, `state` (TUI) | `capture_tui.py` | Snapshot label |
| `edge_lines`, `ellipsis_lines` | `capture_tui.py` | Notes under the snapshot |
| `wide_widgets` | `capture_tui.py:wide_widgets` (textual) | Note: count of widgets wider than the view |
| `drew_anything` | `capture_tui.py` | "drew nothing" note when `false` |
| `txt` (TUI) | `capture_tui.py` | "plain text" link, and scanned by the sensitive-content gate |

`render_manifest` also reads `clipped_lines`. The current `capture_tui.py`
does not write it.

These fields are written for the reviewer but not read by the report:

- Web entries: `height`, `reduced_motion`, `url`, `load_ms`, `masked`,
  `title`, `h1`, `lang`, `overflow.scroll_width`, `overflow.client_width`,
  `small_targets[]`, `axe.violations[]`, `axe.error`, `tab_walk.steps`,
  `tab_walk.reached`, `tab_walk.offscreen`, `tab_walk.error`, `third_party`.
- Web top level: `generated_at`, `browser`, `axe` (source label),
  `axe_version`.
- TUI entries: `keys`, `screen_title`, `focused`, `bindings` (textual);
  `used_rows`, `cursor`, `alive_after_capture`, `exit_status` (pty).
- TUI top level: `generated_at`, `env_dropped` (pty mode only).

The web `summary` keys are `captures`, `with_overflow`,
`with_console_errors`, `with_failed_requests`, `axe_serious_or_critical`,
`axe_skipped`, `axe_errors`, `with_targets_below_24`, `with_invisible_focus`,
`tab_walk_errors`, `with_wait_timeouts`, `third_party_origins`,
`with_blocked`, `evaluate_errors`, `screenshot_errors` and `errors`.
`capture_tui.py` writes no `summary`.

### `run_cli` summary.json (cross-script interface)

`build_report.py:render_cli` reads each file named by `review["cli_summary"]`
(a string or a list):

| Key | Written by `run_cli.py` | Used by `render_cli` |
| --- | --- | --- |
| `command` | the command as typed | Meta line |
| `home` | `scratch` or `real` | Meta line |
| `probes[]` | one entry per probe | Probe table |
| `probes[].probe`, `.argv` (display string), `.env` (overrides) | every probe | Row label, link to `<probe>.txt` beside the summary, tooltip |
| `probes[].exit`, `.timed_out`, `.ansi_stdout`, `.duration_ms` | probes that ran | Table cells |
| `probes[].skipped` | skipped probes | "skipped" cell |
| `flags[]` | automated flags | "Automated flags" list |
| `sub`, `sample`, `bad_input`, `platform`, `python`, `timeout_s`, `cwd`, `help_lines_over_80`, `startup_ms` | summary | Shown as chips (any scalar key not handled above) |

Not read by the report: `generated_at`, `env_dropped`, and per-probe
`stdout_bytes`, `stderr_bytes`, `ansi_stderr`, `tty`, `held_open` and
`default_args`. `render_cli` looks for a `timeout` key for the meta line;
`run_cli.py` writes `timeout_s`, which appears as a chip instead.
`scan_files` scans the summary and every `*.txt` in its folder.

## Invariants

### `build_report.py` validation

These are errors. Each one blocks every output file:

- Structure (`normalise`): `findings`, `prior`, `jobs`, `keep`,
  `not_checked`, `users` and `assumptions` must be lists; `scorecard` and
  `scope` must be objects. Each finding and prior entry must be an object.
  A non-string `id` is an error. `seen` and `do` must be an object or a
  string (a string becomes `{"text": ...}`). Each `instances` entry needs a
  `where`. A job's `matrix` must be an object.
- Every finding has each key in `REQUIRED`: `id`, `title`, `priority`,
  `evidence`, `where`, `seen`, `why`, `do`, `verify` and `effort`. `None`,
  `""`, `{}` and `[]` count as missing. `verify` must be a string.
- `priority` is in `PRIORITIES` (`P0`–`P3`). `evidence` is in `EVIDENCE`
  (`automated`, `judged`, `inferred`). `effort` is `S`, `M` or `L`. `level`,
  if given, is in `LEVELS` (`token`, `component`, `pattern`, `screen`,
  `copy`, `flow`).
- Finding IDs are unique. They must not match any `prior[]` ID or any item in
  the prior ledger. With a prior ledger loaded, a new ID whose trailing
  number is below the ledger's `next_id` is an error.
- Each `prior[]` entry has an `id`, appears once, and has a status in
  `STATUSES` (`fixed`, `partly`, `open`, `regressed`, `wontfix`, `obsolete`,
  `not-retested`). `fixed`, `partly` and `regressed` (`NEEDS_CAPTURE`) need
  `captures`. `wontfix`, `obsolete` and `not-retested` (`NEEDS_NOTE`) need a
  non-blank `note`.
- Every prior-ledger item whose status is in `STILL_OPEN` (`open`,
  `regressed`, `partly`, `not-retested`) must have a `prior[]` entry.
- `scope.prior_review` is set but is not a file (only checked when `--prior`
  is not given).
- `prior[]` has entries but no prior ledger was loaded.
- Scorecard values are keys of `SCORES` (`good`, `needs-work`, `broken`,
  `n/a`, `not-checked`).
- Matrix cells: see the grammar below.
- `verdict` is present and not empty.

These are warnings. They block only with `--strict`:

- A finding without `level`; a `dimension` not in `KNOWN_DIMENSIONS`; an ID
  that does not end in a number; `quick_win: true` with effort other than
  `S`.
- A capture path that does not exist (`seen` / `do` captures and mockups,
  instance captures, prior captures). Paths resolve against the folder
  holding `review.json`.
- More than 5 new findings at P0 or P1.
- `regressed` on an item whose ledger status was not `fixed`, `partly` or
  `regressed`. A `prior[]` ID not found in the loaded ledger (accepted as a
  new minimal item). A ledger item skipped because its `id` is not a string.
- A regressed P0/P1 whose ID does not appear in the verdict text.
- Scorecard: the same dimension given twice, an unknown dimension, a score
  better than `suggest_score` (P0/P1 open → `broken`, P2 → `needs-work`,
  P3 → `good`), `n/a` / `not-checked` while findings are open there, a known
  dimension with no value, or no scorecard at all. Dimension names compare
  case-insensitively, with `&` read as `and`.
- Matrix: a row whose cell count is neither the number of `sizes` nor 1; a
  `UX-###` that matches no finding, prior entry or ledger item; a cell with
  free text; a job where over 80% of cells are `ok` and fewer than half of
  them have distinct evidence.
- A `captures_manifest` or `cli_summary` path that does not exist.
- No `date` (today's date is used in the ledger).
- A JSON file within two folder levels of the review folder that has
  `cookies` and `origins` keys (a Playwright storage state).

After writing, two more warnings are printed. They never change the exit
code, even with `--strict`: `report.html` over 16 MB, and referenced files
outside the report folder (`../`).

### Matrix cell grammar (`parse_cell`)

The cell is stripped, then matched in this order:

1. Empty → error.
2. `ok:<evidence>` (case-insensitive) → ok. Empty evidence is an error.
3. Any other text starting with `ok` (`ok`, `ok - fine`) → error.
4. `n/a`, `na` or `not checked` (also `not_checked`, `not-checked`,
   `notchecked`), then an optional `:`, then a reason. A missing reason is an
   error.
5. Text containing `UX-<digits>` → finding references.
6. Anything else → warning (free text).

### `build_report.py` sensitive-content gate

`scan_files` scans the raw `review.json`, each captures manifest, each `.txt`
capture referenced by a finding or prior entry, each manifest entry's `txt`,
each CLI summary and every `*.txt` beside it. Any hit makes the script exit
`1` unless `--allow-sensitive` is given. Hits print with file, line and a
masked snippet (`mask`: first 4 characters, then up to 12 `*`).

Patterns in `SENSITIVE`: bearer token (8+ characters with a digit), JWT,
`sk_` / `pk_` / `rk_` live or test key, GitHub token (`gh[pousr]_`,
`github_pat_`), GitLab token (`glpat-`), Anthropic key (`sk-ant-`), OpenAI
key (`sk-`, with a digit), Slack token (`xox?-`), Google API key (`AIza`),
AWS access key (`AKIA`), private-key block, and a `secret parameter`
(`token=`, `api_key=`, `secret=`, `password=`, `passwd=`, `sig=`,
`signature=`, `auth=` with 4+ characters). A secret parameter whose value
starts with `redacted` or contains only `*`, `x`, `•` and `.` is skipped.

Email addresses are hits unless `email_is_safe`: the domain is `localhost`
or under `example.com`, `example.org` or `example.net`; the last label is
`test`, `example`, `invalid`, `local`, `localhost` or a file extension in
`FILE_TLDS` (so `logo@2x.png` is not an email); or the local part is `git` or
starts with `noreply` / `no-reply`.

### `build_report.py` ledger rules

- `load_prior` accepts schema-2 ledgers (`items[]`; a missing status becomes
  `open`) and old ledgers (`findings[]` become open items, `prior[]` become
  minimal items). Capture paths are rebased from the ledger's folder to the
  review folder. A non-integer `next_id` is read as 0.
- `merge_ledger` starts from every prior-ledger item. Each `prior[]` entry
  sets that item's `status` and `last_seen` and appends a `history` entry
  (`date`, `status`, and `captures` / `note` when given). A `prior[]` ID not
  in the ledger becomes a new minimal item with its `title`, `priority` and
  `dimension`.
- Each new finding becomes an item with the `ITEM_KEYS` fields and any other
  keys, `quick_win` set by `is_quick_win`, `status` `open`, `first_seen` and
  `last_seen` set to the review date, and one `history` entry.
- `next_id` is one more than the largest of: every item's trailing number,
  the prior `next_id` minus 1, and 0.
- The ledger has `schema: 2`, `project`, `date`, `report` (the report path
  relative to the ledger), `next_id`, `scorecard` and `items` sorted by ID
  number. `write_ledger` rebases capture paths relative to the ledger's
  folder.
- Quick wins (`quick_wins`) are items whose status is in `STILL_OPEN` and
  where `is_quick_win` is true: `quick_win` if set, otherwise effort `S` at
  P0–P2. The report and `fixes.md` use this one list.
- `fixes.md` lists every `STILL_OPEN` item by priority, then ID number, with
  status, last note, level, where, instances, do (captures and mockups),
  verify, effort, evidence and related. Paths are relative to `fixes.md`.

### `capture_web.py`

- `--schemes` and `--wait` accept only their listed choices. Viewports must
  be `WxH`.
- `launch_browser` tries these in order and uses the first that starts:
  1. `--executable-path`
  2. `--channel`
  3. Playwright's bundled Chromium
  4. `channel=chrome`
  5. `channel=chromium`
  6. `/usr/bin/google-chrome`
  7. `/usr/bin/chromium`
- Navigation: if the first `goto` fails with a code in `UNREACHABLE`, the run
  exits. Any other navigation error is recorded as a file-less entry with
  `error: "navigation: ..."` and the run goes on.
- Waits: a `networkidle` timeout switches that URL to `load` for the rest of
  the run. A wait timeout or a missing `--wait-for` selector sets
  `wait_timed_out: true` and `load_ms: null`; the page is captured anyway.
- Per-capture logs are cleared by `goto` and after each `snap`. Each entry
  keeps at most 20 console errors, 20 page errors and 30 failed requests,
  de-duplicated. Failed requests include HTTP status 400 and above.
- Redaction: console text, page errors and error messages pass through
  `scrub` (URLs cleaned, then `REDACT_PATTERNS`: bearer tokens, JWTs,
  `sk_`/`pk_`/`rk_` keys, AWS keys, and `token=`-style values). Routes pass
  through `redact`. Manifest URLs drop `user:password@`, and drop the query
  string and fragment unless `--keep-query` (then they are redacted).
- Masking: `DEFAULT_MASKS` (password inputs, `cc-number`, `cc-csc`,
  `[data-secret]`) plus each `--mask` are blacked out in screenshots. An
  invalid selector is skipped with a message. `JS_SMALL_TARGETS` and
  `JS_FOCUS_INFO` do not record the value of those secret fields.
- Origins: `is_app_url` treats non-network URLs, `localhost`, `127.0.0.1`,
  `::1` (any port), the `--url` origin and each `--allow-origin` as the app.
  Other origins are listed per capture in `third_party`. `--header` values
  are added only to requests to the `--url` origin. `--block-third-party`
  aborts other requests and lists them in `blocked`; their failures and
  console messages are not counted as errors.
- The Tab-key walk runs by default only on light-scheme captures that are not
  a variant and not reduced-motion. It stops after `--tab-steps` presses,
  when focus returns to `<body>` after the first press, or when an element
  repeats from the fifth press on (marked `note: "cycle"`).
- `JS_SMALL_TARGETS` flags visible interactive elements smaller than 44 px in
  either dimension, marks those under 24 px as `below_24`, and lists at most
  60.
- Screenshots: by default full-page captures are clipped to the viewport
  width and capped at 8000 px tall. `--full-width` uses Playwright's plain
  full-page screenshot. `--viewport-only` captures the viewport only.
- Variants: reduced-motion (1440×900) and `zoom200` (640×720) always use the
  light scheme and never run axe. With `--no-axe`, the other captures record
  `axe: {"skipped": "--no-axe"}`.
- Manifest merge (`merged_captures`): entries are keyed by file name; a new
  capture of the same file replaces the old entry in place. File-less error
  entries are keyed by route, width, scheme, variant and state, and an old
  one is dropped when this run captured that combination. An unreadable
  manifest is renamed to `manifest.json.bak` and a new one is started.
  `--fresh` ignores the old manifest. `file` is a bare name relative to the
  manifest's folder.

### `capture_tui.py`

- The textual target must contain `:`. Sizes must be `COLSxROWS`. Each
  `--env` entry is split on its first `=`.
- Pty mode env scrub (`scrub_env`): variables whose name matches
  `SECRET_ENV` (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, a trailing
  `PASS`, `CREDENTIAL`, `AUTH`, `SESSION`, `COOKIE`, `DSN`, `DATABASE_URL`,
  `PRIVATE`; case-insensitive) are dropped unless named by `--keep-env`.
  `--inherit-env` drops nothing. Only names are recorded, as `env_dropped`.
  Textual mode runs in-process and does not scrub.
- Pty timing: the first draw waits `--settle` seconds, then 0.3 s after each
  key and 0.3 s before the snapshot. Textual: `--settle`, then 0.05 s after
  each key and 0.2 s before the snapshot.
- `edge_lines` counts rows whose text ends within one column of the right
  edge in pty mode (`slack=1`), or in the last column in textual mode
  (`slack=0`). One trailing box-drawing or block glyph (U+2500–U+259F) is
  looked past. `ellipsis_lines` counts rows containing `…` or `...`.
- `wide_widgets` lists at most 20 widgets, each with `why` set to `nowrap`,
  `offscreen` or `scroll`. It is `None` when the screen cannot be walked.
- Each run appends its entries to an existing `manifest.json`. It does not
  replace or de-duplicate earlier entries.

### `run_cli.py`

- The command is never run through a shell. `resolve_exe` finds it as a path
  (if it contains a separator) or on the probe environment's `PATH`.
- An unknown option starting with `-` is a usage error, so a mistyped safety
  flag never reaches the tool.
- Each probe runs once, in its own process group (`start_new_session`), with
  stdin from `/dev/null` (the `tty` probe uses the pseudo-terminal). After
  `--timeout` seconds the whole group gets `SIGKILL`, and the probe records
  exit `124` and `timed_out: true`. A command that cannot start records exit
  `127` (not found) or `126`.
- If the command exits but a background child keeps its pipes open for more
  than 1 s, the group is killed. If the pipes are still held, the probe
  records `held_open: true`.
- Environment (`build_env`):
  - Unless `--inherit-env`, variables whose name matches `SECRET_RE` (same
    list as `SECRET_ENV` above) are removed, except names given with
    `--keep-env` (compared in upper case). Removed names go in
    `env_dropped`.
  - Unless `--keep-home` or `--inherit-env`, `HOME`, `XDG_CONFIG_HOME`,
    `XDG_DATA_HOME`, `XDG_CACHE_HOME` and `XDG_STATE_HOME` (and `USERPROFILE`
    on Windows) point into a scratch folder.
  - `NO_PAGER` (`PAGER=cat`, `GIT_PAGER=cat`, `MANPAGER=cat`, `LESS=FRX`) is
    always set.
- Working directory (`pick_cwd`): a scratch folder, unless `--keep-cwd`, the
  command is a relative path with a separator, or any later word (or the
  value of a `--flag=value` word) names an existing relative path.
- Automated flags are raised when:
  - any probe timed out or left its output `held_open`
  - `wrong-flag` (not timed out) exits outside `USAGE_EXITS`, or writes
    nothing to stderr
  - `bad-input` exits `0`
  - `piped`, `no-color` or `term-dumb` stdout contains ANSI escapes
  - `bare` (run, not timed out) exits other than `0`, `1` or `2`
  - any line of `help-80` stdout is wider than 80 columns after stripping
    ANSI (wide characters count 2, tabs to the next multiple of 8)
  - `startup_ms` is over 1000 (and `help-80` did not time out)
  - `--version` exits non-zero (and did not time out)
  - the `tty` probe was skipped
  - `--help` exits non-zero with a scratch `HOME`

### `annotate.py`

- `parse_rect` needs exactly 4 comma-separated numbers, truncates them to
  integers and orders them as min and max corners. It runs after argument
  parsing, so a bad value ends in a traceback: `ArgumentTypeError` for the
  wrong count, `ValueError` for a value that is not a number.
- `--crop`, `--box` and `--arrow` coordinates are all in the original
  image's pixels. Boxes and arrows are shifted by the crop offset.
- `--scale` is applied last, to the image and legend together.

## Side Effects

### `capture_web.py`

- Creates `--out` (default `captures/`) and writes
  `<slug(route)>__<width>__<scheme>[__<slug(state)>][__<variant>].png`
  files.
- Writes `<out>/manifest.json` when the `Capturer` with-block exits,
  merging into the existing file unless `--fresh`. It may rename an
  unreadable manifest to `manifest.json.bak`. The manifest holds
  `generated_at`, `browser`, `axe` (a source label, not a path),
  `axe_version`, `summary` and `captures`.
- Launches headless Chromium or Chrome with `--no-sandbox`. Each capture gets
  its own browser context with the storage state, locale, time zone, colour
  scheme and reduced-motion setting given; `main` closes it after the
  capture.
- Network: loads `--url` plus each route. The page then fetches whatever it
  references, from any origin, unless `--block-third-party` is set.
- Runs `axe.min.js` and the `JS_*` snippets in the page, and presses Tab up
  to `--tab-steps` times. `JS_FOCUS_INFO` blurs and refocuses the active
  element at each step.
- Reads the axe file and `--storage-state` file.
- Writes progress, the browser chosen, wait timeouts and a summary line to
  stderr. `--check` launches and closes one browser and prints its report to
  stdout.

### `capture_tui.py`

- Creates `--out` (default `captures-tui/`).
- Textual mode:
  - Puts the current directory first on `sys.path`.
  - Imports the user's module (running its top-level code) and runs the app
    headless through `run_test`, in this process, with the full environment.
  - Writes `<name>__<cols>x<rows>[__<slug(state)>].svg` and `.txt`. `name`
    defaults to the class name.
- Pty mode:
  - Starts the command through `pexpect.spawn` (no shell) with the scrubbed
    environment plus `TERM=xterm-256color`, `COLUMNS`, `LINES`, `PAGER=cat`
    and any `--env` values.
  - Sends the `--keys`, then `q`, ESC and Ctrl-C until the process exits,
    and force-terminates it if it is still running.
  - Writes a `.txt` file, plus a `.png` with `--png`. `name` defaults to the
    program name, plus the first argument if it does not start with `-`.
- Appends entries to `<out>/manifest.json` and updates `generated_at`. Pty
  runs overwrite the top-level `env_dropped`.
- Prints each output path, the dropped variable names and a summary line to
  stderr.

### `run_cli.py`

- Runs the user's command once per probe, up to 10 times, each run in a new
  process group or session.
- Creates a temporary folder (`run_cli-*` under the system temp dir) holding
  the scratch `home` and `work` folders, and removes it at the end.
- Creates `--out` (default `cli-runs/`) and writes one `<probe>.txt` per
  probe (command line, exit, stdout, stderr; or `skipped: <reason>`) plus
  `summary.json`.
- Kills probe process groups on timeout, on held-open pipes, and on Ctrl-C.
- Does not change its own environment; each probe gets its own copy.
- Prints the probe count, `summary.json` path and the automated flags to
  stderr. `--help` prints to stdout.

### `run_cli.sh`

- Replaces itself with `python3 run_cli.py` from its own folder. All side
  effects are `run_cli.py`'s.

### `annotate.py`

- Writes the annotated PNG to `--out`, creating missing folders. The default
  depends on where the image is:
  - in a folder: `<image's grandparent>/annotated/<stem>__do.png`, which puts
    `annotated/` beside the captures folder
  - in the current directory: `./annotated/<stem>__do.png`
- Prints the output path to stdout.

### `build_report.py`

- Reads `review.json`, the prior ledger, the captures manifests, the CLI
  summaries, `.txt` captures and probe files. With `--inline`, it also reads
  the image files and embeds them as base64.
- Walks the review folder two levels deep (skipping `.git`, `node_modules`,
  `venv`, `.venv`, `site-packages`, `__pycache__` and dot folders) and parses
  each `.json` file up to 20 MB to look for storage-state files.
- Only when every check passes, writes `report.html`, `findings.json` and
  `fixes.md`, creating missing folders.
- Prints warnings, errors, sensitive hits and a result line (sizes,
  `next_id`, open and quick-win counts) to stderr. It then lists the files the
  page references (relative to the report), for an Artifact `files` map.
  With `--inline`, only non-image links are listed.

## Verification

There is no build manifest or test suite. To syntax-check the scripts, run
this from this folder:

```sh
python3 -m py_compile capture_web.py capture_tui.py annotate.py build_report.py run_cli.py && sh -n run_cli.sh
```

`python3 capture_web.py --check` is a runtime preflight for the web path: it
exits `0` only if a browser launches.
