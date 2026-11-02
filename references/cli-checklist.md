# CLI checklist — what to check and how

First read `<cmd> --help` yourself, so you know what the bare command and each subcommand do. Then run:

```sh
python3 <skill-dir>/scripts/run_cli.py "<cmd>" --sub "<main read-only subcommand>" \
  --sample "<read-only args, e.g. list --limit 3>" --bad-input "<args wrong for this tool>"
```

**What it runs.** Each probe runs **once**, with a timeout (`--timeout`, default 15 s) and stdin from `/dev/null`. The `tty` probe is the exception: its stdin is the pseudo-terminal.

- `help-80`: `--help` with `COLUMNS=80`. Its run time is `startup_ms`.
- `version`
- `bare`: the command with no arguments.
- `wrong-flag`
- `bad-input`
- `sub-help`: only when `--sub` is given.
- `tty`: the target plus `--sample` on a real 80×24 pseudo-terminal.
- `piped`, `no-color` (`NO_COLOR=1`) and `term-dumb` (`TERM=dumb`): the same command, non-tty.

**What it writes.** Output goes to `cli-runs/<probe>.txt`, plus `cli-runs/summary.json` with exit codes, timeouts, ANSI detection, help width and a list of automated `flags`. Put that summary path in `review.json` → `cli_summary` and the report shows it.

**Safety defaults** (they exist because the command really runs):

- Pagers are off (`PAGER=cat`, `GIT_PAGER=cat`, `MANPAGER=cat`, `LESS=FRX`).
- Environment variables that look like secrets (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `DATABASE_URL`, …) are removed. `summary.json` → `env_dropped` lists their names, never their values.
- `HOME`/`XDG_*` point to a scratch folder, and the command runs in a scratch folder.
- A probe that doesn't exit in time is killed with its whole process group, recorded as exit 124, and flagged.

**Options to choose deliberately:**

| Option | When |
|---|---|
| `--skip-bare` | `--help` shows the bare command *does* something (starts a server, syncs, opens a UI) |
| `--sub` / `--sample` | Always read-only (list, status, show). Never a subcommand that writes, sends, spends or deletes: it runs several times |
| `--bad-input` | Something wrong *for this tool*: a missing positional file, a malformed date, an out-of-range number. The default is a placeholder, and the probe records `default_args: true` |
| `--keep-cwd` | Tools that act on the current project (git, cargo, make) |
| `--keep-home` | Tools that need their config or toolchain from `HOME` (rustup/cargo, nvm, pyenv shims). A `--help` that fails with a scratch `HOME` is flagged |
| `--keep-env NAME` / `--inherit-env` | A tool that needs one of the removed variables |

Then run the two or three real commands the user runs most, on sample data, and the common mistakes. Save each transcript as `cli-runs/<state>.txt` with its exit code: `cmd args > cli-runs/<state>.txt 2>&1; echo "exit: $?" >> cli-runs/<state>.txt`.

## Help and discoverability

- `--help` fits 80 columns without wrapping mid-word; shows the commands people actually run first, with one example each; says where config lives.
- `<cmd> <sub> --help` exists for every subcommand and is specific to it.
- The bare command does something harmless and useful (help, status) — never an action.
- Version is available (`--version`) and includes enough to report a bug.

## Errors that say how to fix

- A wrong flag: names the flag, suggests the closest valid one, exit code 2. Also accepted: 64 (EX_USAGE), and git-style 128/129. A framework default of 1 (cobra, commander) is P3 unless scripts depend on telling usage errors apart.
- Bad input (missing file, malformed value): names the input, the expected format, exit code 1.
- Missing config or credentials: says which file or variable, and how to create it, without printing secrets.
- No stack traces for expected failures; stack traces only with `--debug`/`-v`.
- Errors go to stderr so they don't corrupt piped output.

## Human vs machine output

- When stdout is a TTY: aligned tables, colour, progress. When piped (`| cat`): plain lines, no colour, no progress bars, no prompts that hang waiting for input.
- `--json` (or `--format json`) for every command that lists or reports, with stable field names; one object per line for streams.
- `NO_COLOR=1` and `TERM=dumb` respected.
- Quiet (`-q`) and verbose (`-v`) both exist and do what they say.

## Safety

- Destructive or spending commands need `--yes` (or a typed confirmation when interactive) and offer `--dry-run` that prints exactly what would happen.
- Confirmations state the specifics ("will delete 12 files under ./data"), not "are you sure?".
- Interrupt (`Ctrl-C`) during a long command: partial output flushed, state not corrupted, exit 130.
- Nothing writes outside the project or the stated config dir without saying so.

## Exit codes and scripting

- 0 success, 1 failure, 2 usage error, 130 interrupted; documented in `--help` or docs.
- Idempotent re-runs where it makes sense (re-running `init` doesn't clobber).
- Reads from stdin when given `-` as a file; works in a pipeline.

## Input validation

- Values validated before work starts (fail fast), with all problems listed at once rather than one per run.
- Dates, numbers and paths accept the forms a person types (`2026-11-02`, `1,000`, `~/data`).
- Defaults shown in `--help`.

## Performance feel and progress

- Anything over ~2 s shows progress or at least "working…" with elapsed time; the user can tell it isn't hung.
- Startup time (`summary.json` → `startup_ms`, the `--help` run): more than a second is a finding for a tool run often. Note that `COLUMNS=80` is only an environment variable: tools that read the terminal size directly are measured at whatever width they choose, so judge help width from the `tty` probe too.

## Consistency with the rest of the project

- Same nouns and verbs as the TUI, web UI and docs; same config file; same flag names across subcommands (`--output`, not `--out` in one and `-o` in another).
