# CLI checklist — what to check and how

Run `scripts/run_cli.sh <command>` first. It produces `cli-runs/` with one file per probe (help at 80 columns, bare command, wrong flag, bad input, piped, `NO_COLOR`, `TERM=dumb`) and `cli-runs/summary.json` with exit codes. Then run the two or three real commands the user runs most, on sample data, and the common mistakes.

## Help and discoverability

- `--help` fits 80 columns without wrapping mid-word; shows the commands people actually run first, with one example each; says where config lives.
- `<cmd> <sub> --help` exists for every subcommand and is specific to it.
- The bare command does something harmless and useful (help, status) — never an action.
- Version is available (`--version`) and includes enough to report a bug.

## Errors that say how to fix

- A wrong flag: names the flag, suggests the closest valid one, exit code 2.
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
- Startup time of the bare command (`time <cmd> --help`): more than a second is a finding for a tool run often.

## Consistency with the rest of the project

- Same nouns and verbs as the TUI, web UI and docs; same config file; same flag names across subcommands (`--output`, not `--out` in one and `-o` in another).
