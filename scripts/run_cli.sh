#!/bin/sh
# Kept for backward compatibility: the CLI probes now live in run_cli.py (timeouts, portable, no eval).
exec python3 "$(dirname "$0")/run_cli.py" "$@"
