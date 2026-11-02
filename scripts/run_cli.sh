#!/usr/bin/env bash
# Run the standard CLI ergonomics probes against a command and record the results.
#
#   scripts/run_cli.sh "<command>" [--out cli-runs] [--sub "<subcommand>"] [--bad-input "<args>"]
#
# Probes (each writes <out>/<probe>.txt with the command, stdout, stderr and exit code):
#   help-80        <cmd> --help, rendered at 80 columns (flags lines wider than 80 are counted)
#   bare           <cmd> with no arguments (should be harmless: help or status)
#   version        <cmd> --version
#   wrong-flag     <cmd> <sub> --definitely-not-a-flag    (expect usage error, exit 2, a suggestion)
#   bad-input      <cmd> <sub> <--bad-input args>         (default: --input pointing at a missing file)
#   sub-help       <cmd> <sub> --help                     (when --sub is given)
#   tty            <cmd> <sub or nothing> on a pseudo-tty (how a person sees it: colour, tables)
#   piped          <cmd> <sub or nothing> | cat           (no colour codes, no progress bars, no prompts)
#   no-color       NO_COLOR=1 <cmd> <sub or nothing>      (no ANSI escapes)
#   term-dumb      TERM=dumb <cmd> <sub or nothing>
#   startup        time of <cmd> --help in ms
#
# Then <out>/summary.json: exit codes, whether stderr/stdout were used, ANSI escapes found when they
# shouldn't be, help width, startup ms. The command runs as given: point it at a sample project and
# never at real data or live credentials. Nothing here passes --yes or confirms anything.
set -u

CMD=""; OUT="cli-runs"; SUB=""; BAD_INPUT="--input /definitely/missing/file.csv"
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2;;
    --sub) SUB="$2"; shift 2;;
    --bad-input) BAD_INPUT="$2"; shift 2;;
    *) if [ -z "$CMD" ]; then CMD="$1"; else CMD="$CMD $1"; fi; shift;;
  esac
done
[ -z "$CMD" ] && { echo "usage: run_cli.sh \"<command>\" [--out dir] [--sub \"<subcommand>\"] [--bad-input \"<args>\"]" >&2; exit 2; }
mkdir -p "$OUT"

has_ansi() { grep -qP '\x1b\[[0-9;]*[A-Za-z]' "$1" 2>/dev/null && echo true || echo false; }

run_probe() {  # name, env-prefix, command-string, [mode: "" | pipe | tty]
  local name="$1" envp="$2" full="$3" mode="${4:-}"
  local so="$OUT/$name.stdout" se="$OUT/$name.stderr" rc
  case "$mode" in
    pipe)  ( eval "env $envp $full" </dev/null 2>"$se" | cat >"$so"; exit "${PIPESTATUS[0]}" ); rc=$?;;
    tty)   # script(1) gives a pseudo-tty so the command renders as it would for a person (streams merge)
           if command -v script >/dev/null 2>&1; then
             COLUMNS=80 LINES=24 script -qefc "env $envp $full" /dev/null >"$so" 2>"$se" </dev/null; rc=$?
           else
             eval "env $envp $full" >"$so" 2>"$se" </dev/null; rc=$?
           fi;;
    *)     eval "env $envp $full" >"$so" 2>"$se" </dev/null; rc=$?;;   # streams kept apart, exit code exact
  esac
  {
    echo "\$ $envp $full $( [ "$mode" = pipe ] && echo '| cat' ) $( [ "$mode" = tty ] && echo '  (pseudo-tty, 80x24)' )"
    echo "exit: $rc"; echo "--- stdout"; sed 's/\r$//' "$so"; echo "--- stderr"; sed 's/\r$//' "$se"
  } > "$OUT/$name.txt"
  printf '{"probe":"%s","exit":%s,"stdout_bytes":%s,"stderr_bytes":%s,"ansi_stdout":%s,"ansi_stderr":%s}' \
    "$name" "$rc" "$(wc -c <"$so")" "$(wc -c <"$se")" "$(has_ansi "$so")" "$(has_ansi "$se")"
  rm -f "$so" "$se"
}

ENTRIES=()
ENTRIES+=("$(run_probe help-80 "COLUMNS=80" "$CMD --help")")
HELP_WIDE=$(sed 's/\x1b\[[0-9;]*[A-Za-z]//g' "$OUT/help-80.txt" | awk 'length > 80 {n++} END {print n+0}')
ENTRIES+=("$(run_probe bare "" "$CMD")")
ENTRIES+=("$(run_probe version "" "$CMD --version")")
TARGET="$CMD${SUB:+ $SUB}"
ENTRIES+=("$(run_probe wrong-flag "" "$TARGET --definitely-not-a-flag")")
ENTRIES+=("$(run_probe bad-input "" "$TARGET $BAD_INPUT")")
[ -n "$SUB" ] && ENTRIES+=("$(run_probe sub-help "COLUMNS=80" "$CMD $SUB --help")")
ENTRIES+=("$(run_probe tty "" "$TARGET" tty)")
ENTRIES+=("$(run_probe piped "" "$TARGET" pipe)")
ENTRIES+=("$(run_probe no-color "NO_COLOR=1" "$TARGET")")
ENTRIES+=("$(run_probe term-dumb "TERM=dumb" "$TARGET")")

T0=$(date +%s%N); eval "$CMD --help" >/dev/null 2>&1 </dev/null; T1=$(date +%s%N)
STARTUP_MS=$(( (T1 - T0) / 1000000 ))

{
  echo "{"
  echo "  \"command\": $(printf '%s' "$CMD" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),"
  echo "  \"generated_at\": \"$(date -Iseconds)\","
  echo "  \"help_lines_over_80\": $HELP_WIDE,"
  echo "  \"startup_ms\": $STARTUP_MS,"
  echo "  \"probes\": ["
  for i in "${!ENTRIES[@]}"; do
    [ "$i" -gt 0 ] && echo ","
    printf '    %s' "${ENTRIES[$i]}"
  done
  echo; echo "  ]"; echo "}"
} > "$OUT/summary.json"

echo "[run_cli] $(( ${#ENTRIES[@]} )) probes → $OUT/summary.json  help>80cols:$HELP_WIDE  startup:${STARTUP_MS}ms" >&2
python3 - "$OUT/summary.json" <<'EOF'
import json, sys
s = json.load(open(sys.argv[1]))
flags = []
by = {p["probe"]: p for p in s["probes"]}
if by.get("wrong-flag", {}).get("exit") not in (2, 64): flags.append("wrong flag did not exit 2 (usage error)")
if by.get("wrong-flag", {}).get("stderr_bytes") == 0: flags.append("wrong flag reported nothing on stderr")
if by.get("bad-input", {}).get("exit") == 0: flags.append("bad input exited 0")
if by.get("piped", {}).get("ansi_stdout"): flags.append("ANSI escapes in piped stdout")
if by.get("no-color", {}).get("ansi_stdout"): flags.append("ANSI escapes with NO_COLOR=1")
if by.get("term-dumb", {}).get("ansi_stdout"): flags.append("ANSI escapes with TERM=dumb")
if by.get("bare", {}).get("exit") not in (0, 1, 2): flags.append(f"bare command exit {by['bare']['exit']}")
if s["help_lines_over_80"]: flags.append(f"{s['help_lines_over_80']} help lines wider than 80 columns")
if s["startup_ms"] > 1000: flags.append(f"startup {s['startup_ms']} ms")
if by.get("version", {}).get("exit") != 0: flags.append("--version failed")
print("[run_cli] automated flags:" + ("".join(f"\n  - {f}" for f in flags) if flags else " none"), file=sys.stderr)
EOF
