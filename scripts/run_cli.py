#!/usr/bin/env python3
"""Run the standard CLI ergonomics probes against a command and record the results.

Usage:
  python3 run_cli.py "<command>" [--out cli-runs] [--sub "<read-only subcommand>"]
                     [--sample "<read-only args>"] [--bad-input "<args>"] [--timeout 15]
                     [--skip-bare] [--keep-home] [--keep-cwd] [--keep-env NAME]... [--inherit-env]

  python3 run_cli.py "mytool" --sub "list" --sample "list --limit 3" --bad-input "--since yesterdayy"
  python3 run_cli.py git --sub log --keep-cwd        # extra plain words are appended to the command

Extra words after the command that start with "-" must be run_cli options (a typo such as --skipbare
is a usage error, so a mistyped safety flag never reaches the tool); put the tool's own flags inside
the quoted command. Everything after a lone "--" is appended to the command unchecked.

<command> is split with shlex (no shell, no eval). Python >= 3.9, standard library only.

Probes, in this order, each run at most once (TARGET = command + --sub, SAMPLE = --sample or "--help"):
  help-80     <cmd> --help with COLUMNS=80 LINES=24 (its duration is startup_ms)
  version     <cmd> --version
  bare        <cmd> with no arguments                   (skipped with --skip-bare)
  wrong-flag  TARGET --definitely-not-a-flag            (expect a usage error on stderr, exit 2)
  bad-input   TARGET <--bad-input args>                 (default placeholder: --input /definitely/missing/file.csv)
  sub-help    <cmd> <sub> --help, COLUMNS=80            (only with --sub)
  tty         TARGET SAMPLE on an 80x24 pseudo-terminal  (streams merged; skipped where there is no pty, e.g. Windows)
  piped       TARGET SAMPLE, stdout/stderr to pipes     (no colour, no progress bars, no prompts expected)
  no-color    as piped, with NO_COLOR=1
  term-dumb   as piped, with TERM=dumb

Writes <out>/<probe>.txt (command line, exit code, stdout, stderr) and <out>/summary.json (exit codes,
byte counts, ANSI escapes found, help lines wider than 80 columns, startup ms, automated flags).
Every probe has stdin closed (except tty), runs in its own process group and is killed after
--timeout seconds (recorded as exit 124, "timed_out": true). PAGER=cat GIT_PAGER=cat MANPAGER=cat
LESS=FRX are set for every probe so nothing waits in a pager.

Safety (read before running):
  * Read `<cmd> --help` yourself first. The runner executes the command for real.
  * Pass only read-only values to --sub and --sample (list, status, show, --help), never ones
    that write, send, delete or spend. Nothing here passes --yes or confirms anything.
  * Use --skip-bare if the bare command does anything (starts a server, syncs, opens a REPL).
  * By default probes run with a scratch HOME / USERPROFILE / XDG_* dirs in a temp folder (removed
    afterwards), in a scratch working directory, and with secret-looking variables removed
    (names matching KEY, TOKEN, SECRET, PASSWORD, AUTH, SESSION, COOKIE, DSN, PRIVATE, ...).
    The dropped names (never values) are listed in summary.json as "env_dropped".
  * --keep-home   use the real HOME, when the CLI needs its config or toolchain from HOME
                  (e.g. rustup/cargo, npm/nvm, pyenv shims, a config file under ~/.config).
  * --keep-cwd    run in the current directory (for tools that act on the project in cwd, such as
                  git or cargo). The current directory is also kept automatically when the command
                  is a relative path (./bin/x) or names an existing relative file (python3 app.py).
  * --keep-env NAME   keep one scrubbed variable (repeatable), e.g. a dummy key for a test account.
  * --inherit-env     no scrub and the real HOME: the full parent environment.
Point the command at sample data and test accounts, never at real data or live credentials.

Exit status: 0 after the probes ran (findings are in summary.json and on stderr), 2 on a usage error.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import platform
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata

try:  # POSIX only; the tty probe is skipped without these
    import fcntl
    import pty
    import struct
    import termios
    HAVE_PTY = True
except ImportError:  # pragma: no cover - Windows
    HAVE_PTY = False

POSIX = os.name == "posix"
DEFAULT_BAD_INPUT = "--input /definitely/missing/file.csv"
DEFAULT_SAMPLE = "--help"
USAGE_EXITS = (2, 64, 128, 129)
SECRET_RE = re.compile(
    r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS$|CREDENTIAL|AUTH|SESSION|COOKIE|DSN|DATABASE_URL|PRIVATE",
    re.IGNORECASE)
ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\]")
ANSI_STRIP_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]")
_LINGERING: list = []  # Popen objects whose pipes an escaped background process still holds
NO_PAGER = {"PAGER": "cat", "GIT_PAGER": "cat", "MANPAGER": "cat", "LESS": "FRX"}

USAGE = ('usage: run_cli.py "<command>" [--out cli-runs] [--sub "<read-only subcommand>"] '
         '[--sample "<read-only args>"] [--bad-input "<args>"] [--timeout 15] [--skip-bare] '
         '[--keep-home] [--keep-cwd] [--keep-env NAME]... [--inherit-env]')


def log(msg: str) -> None:
    print(f"[run_cli] {msg}", file=sys.stderr)


def usage_error(msg: str) -> "SystemExit":
    print(f"run_cli: {msg}\n{USAGE}", file=sys.stderr)
    return SystemExit(2)


def split_words(text: str) -> list:
    if POSIX:
        return shlex.split(text)
    # Windows: keep backslashes in paths, then drop the surrounding quotes shlex leaves in place.
    return [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in "\"'" else w
            for w in shlex.split(text, posix=False)]


def parse_args(argv: list) -> dict:
    opts = {"out": "cli-runs", "sub": None, "sample": None, "bad_input": None, "timeout": 15.0,
            "skip_bare": False, "keep_home": False, "keep_cwd": False, "keep_env": [],
            "inherit_env": False}
    valued = {"--out": "out", "--sub": "sub", "--sample": "sample", "--bad-input": "bad_input",
              "--timeout": "timeout", "--keep-env": "keep_env"}
    flags = {"--skip-bare": "skip_bare", "--keep-home": "keep_home", "--keep-cwd": "keep_cwd",
             "--inherit-env": "inherit_env"}
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            positional.extend(argv[i + 1:])
            break
        if a in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        name, eq, inline = a.partition("=")
        if name in valued:
            if eq:
                val = inline
            else:
                if i + 1 >= len(argv):
                    raise usage_error(f"{name} needs a value")
                i += 1
                val = argv[i]
            key = valued[name]
            if key == "keep_env":
                opts["keep_env"].append(val)
            elif key == "timeout":
                try:
                    opts["timeout"] = float(val)
                except ValueError:
                    raise usage_error(f"--timeout expects seconds, got {val!r}") from None
                if opts["timeout"] <= 0:
                    raise usage_error("--timeout must be greater than 0")
            else:
                opts[key] = val
        elif a in flags:
            opts[flags[a]] = True
        elif a.startswith("-") and a != "-":
            # a mistyped run_cli option must not be passed to the real tool (and must not silently drop
            # a safety flag such as --skip-bare); the tool's own flags belong inside the quoted command
            known = sorted(set(valued) | set(flags) | {"--help"})
            close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
            hint = f" (did you mean {close[0]}?)" if close else ""
            raise usage_error(f"unknown option {name}{hint} \u2014 put the tool's own arguments inside the quoted "
                              f"command, or use --sample")
        else:
            positional.append(a)
        i += 1
    if not positional or not positional[0].strip():
        raise usage_error("no command given")
    try:
        cmd = split_words(positional[0]) + positional[1:]
    except ValueError as e:
        raise usage_error(f"cannot parse the command: {e}") from None
    if not cmd:
        raise usage_error("no command given")
    opts["cmd"] = cmd
    opts["command_display"] = " ".join(positional)
    return opts


# ---------------------------------------------------------------- environment

def build_env(opts: dict, scratch_home: str | None) -> tuple:
    base = dict(os.environ)
    dropped = []
    if not opts["inherit_env"]:
        keep = {k.upper() for k in opts["keep_env"]}
        for name in sorted(base):
            if SECRET_RE.search(name) and name.upper() not in keep:
                dropped.append(name)
                del base[name]
    if scratch_home:
        dirs = {"HOME": "", "XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share",
                "XDG_CACHE_HOME": ".cache", "XDG_STATE_HOME": ".local/state"}
        for var, sub in dirs.items():
            path = os.path.join(scratch_home, *sub.split("/")) if sub else scratch_home
            os.makedirs(path, exist_ok=True)
            base[var] = path
        if not POSIX:
            base["USERPROFILE"] = scratch_home
    base.update(NO_PAGER)
    return base, dropped


def pick_cwd(opts: dict, scratch_work: str) -> str:
    """Scratch cwd unless the command or its arguments refer to paths relative to the current dir."""
    if opts["keep_cwd"]:
        return os.getcwd()
    first = opts["cmd"][0]
    if not os.path.isabs(first) and (os.sep in first or (os.altsep and os.altsep in first)):
        return os.getcwd()
    for word in opts["cmd"][1:]:
        cand = word.split("=", 1)[1] if word.startswith("-") and "=" in word else word
        if cand and not os.path.isabs(cand) and not cand.startswith("-") and os.path.exists(cand):
            return os.getcwd()
    return scratch_work


def resolve_exe(first: str, env: dict) -> str:
    """Absolute path of the program, so a scratch cwd and Windows .cmd shims work. Raises if not found."""
    if os.sep in first or (os.altsep and os.altsep in first):
        path = os.path.abspath(first)
        if not os.path.exists(path):
            raise FileNotFoundError(first)
        return path
    found = shutil.which(first, path=env.get("PATH", os.defpath))
    if not found:
        raise FileNotFoundError(first)
    return os.path.abspath(found)


# ---------------------------------------------------------------- running

def kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the probe's whole process group (POSIX) or process tree (Windows)."""
    if POSIX:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:  # group already gone
            pass
        return
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def popen_kwargs() -> dict:
    if POSIX:
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}


def _drain(fd: int, sink: list) -> None:
    try:
        while True:
            data = os.read(fd, 65536)
            if not data:
                return
            sink.append(data)
    except OSError:
        return


def run_piped(argv: list, exe: str, env: dict, cwd: str, timeout: float, grace: float = 1.0) -> dict:
    """Run with stdout/stderr on pipes. Returns exit, timed_out, duration_ms, stdout, stderr, held_open.

    held_open: the command exited but something it started in the background kept the pipes open
    (output after that point is not captured; the background process is killed if it is in the
    probe's process group, otherwise left alone and reported)."""
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(argv, executable=exe if POSIX else None, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
                                **popen_kwargs())
    except OSError as e:
        return {"exit": 127 if isinstance(e, FileNotFoundError) else 126, "timed_out": False,
                "duration_ms": int((time.monotonic() - t0) * 1000), "stdout": b"",
                "stderr": f"run_cli: could not start: {e}\n".encode(), "held_open": False}
    out, err = [], []
    readers = [threading.Thread(target=_drain, args=(proc.stdout.fileno(), out), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr.fileno(), err), daemon=True)]
    for t in readers:
        t.start()
    timed_out = held_open = False
    try:
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out, code = True, 124
            kill_group(proc)
            proc.wait(timeout=5)
        duration_ms = int((time.monotonic() - t0) * 1000)

        def join(limit: float) -> bool:
            end = time.monotonic() + limit
            for t in readers:
                t.join(max(0.0, end - time.monotonic()))
            return not any(t.is_alive() for t in readers)

        if not join(grace):
            kill_group(proc)  # background children still in the probe's group
            if not join(grace):
                held_open = not timed_out
    except BaseException:  # Ctrl-C: kill the probe, release the pipes, re-raise
        kill_group(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.SubprocessError:
            pass
        for t in readers:
            t.join(grace)
        if not any(t.is_alive() for t in readers):
            proc.stdout.close()
            proc.stderr.close()
        else:
            _LINGERING.append(proc)
        raise
    if any(t.is_alive() for t in readers):
        _LINGERING.append(proc)  # a reader still owns the fds: never close them under it
    else:
        for f in (proc.stdout, proc.stderr):
            try:
                f.close()
            except OSError:
                pass
    return {"exit": code, "timed_out": timed_out, "duration_ms": duration_ms,
            "stdout": b"".join(list(out)), "stderr": b"".join(list(err)), "held_open": held_open}


def _make_ctty() -> None:  # runs in the child after setsid(): adopt the pty slave as controlling tty
    req = getattr(termios, "TIOCSCTTY", None)
    if req is not None:
        try:
            fcntl.ioctl(0, req, 0)
        except OSError:
            pass


def run_tty(argv: list, exe: str, env: dict, cwd: str, timeout: float,
            cols: int = 80, rows: int = 24) -> dict:
    t0 = time.monotonic()
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    chunks = []
    timed_out = False
    try:
        try:
            proc = subprocess.Popen(argv, executable=exe, stdin=slave, stdout=slave, stderr=slave,
                                    env=env, cwd=cwd, start_new_session=True, preexec_fn=_make_ctty)
        except OSError as e:
            return {"exit": 127 if isinstance(e, FileNotFoundError) else 126, "timed_out": False,
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "stdout": f"run_cli: could not start: {e}\n".encode(), "stderr": b""}
        deadline = t0 + timeout
        try:
            # The parent keeps its slave fd open until the child exits, so reading never depends on
            # EOF/EIO semantics (which differ between Linux and macOS); then drain what is buffered.
            while True:
                r, _, _ = select.select([master], [], [], 0.05)
                if r:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        data = b""
                    if data:
                        chunks.append(data)
                if proc.poll() is not None:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    kill_group(proc)
                    proc.wait(timeout=5)
                    break
            while True:
                r, _, _ = select.select([master], [], [], 0.05)
                if not r:
                    break
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
        except BaseException:
            kill_group(proc)
            proc.wait()
            raise
        code = 124 if timed_out else proc.returncode
    finally:
        for fd in (slave, master):
            try:
                os.close(fd)
            except OSError:
                pass
    return {"exit": code, "timed_out": timed_out, "duration_ms": int((time.monotonic() - t0) * 1000),
            "stdout": b"".join(chunks), "stderr": b""}


# ---------------------------------------------------------------- text helpers

def has_ansi(data: bytes) -> bool:
    return bool(ANSI_RE.search(data))


def display_width(line: str) -> int:
    width = 0
    for ch in line:
        if ch == "\t":
            width += 8 - width % 8
            continue
        if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf", "Cc"):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def lines_over(text: str, limit: int = 80) -> int:
    clean = ANSI_STRIP_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    return sum(1 for line in clean.split("\n") if display_width(line) > limit)


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def env_prefix(overrides: dict) -> str:
    return " ".join(f"{k}={shlex.quote(v)}" for k, v in overrides.items())


# ---------------------------------------------------------------- main

def main(argv: list) -> int:
    opts = parse_args(argv)
    cmd = opts["cmd"]
    sample_default = opts["sample"] is None
    bad_default = opts["bad_input"] is None
    bad_text = DEFAULT_BAD_INPUT if bad_default else opts["bad_input"]
    try:
        sub_words = split_words(opts["sub"]) if opts["sub"] else []
        sample_words = split_words(DEFAULT_SAMPLE if sample_default else opts["sample"])
        bad_words = split_words(bad_text)
    except ValueError as e:
        raise usage_error(f"cannot parse --sub/--sample/--bad-input: {e}") from None
    target = cmd + sub_words
    timeout = opts["timeout"]
    out_dir = opts["out"]

    tmp = tempfile.mkdtemp(prefix="run_cli-")
    try:
        use_scratch_home = not (opts["keep_home"] or opts["inherit_env"])
        scratch_home = os.path.join(tmp, "home") if use_scratch_home else None
        scratch_work = os.path.join(tmp, "work")
        os.makedirs(scratch_work, exist_ok=True)
        env, dropped = build_env(opts, scratch_home)
        cwd = pick_cwd(opts, scratch_work)
        try:
            exe = resolve_exe(cmd[0], env)
        except FileNotFoundError:
            print(f"run_cli: command not found: {cmd[0]}\n{USAGE}", file=sys.stderr)
            return 2
        os.makedirs(out_dir, exist_ok=True)

        def real(words: list) -> list:
            # POSIX: argv[0] stays as typed (usage lines print it) and `executable` points at the
            # resolved program. Windows: argv[0] must be the resolved path for .cmd/.bat shims.
            return words if POSIX else [exe] + words[1:]

        tty_term = {} if env.get("TERM") not in (None, "", "dumb") else {"TERM": "xterm-256color"}
        size80 = {"COLUMNS": "80", "LINES": "24"}
        plan = [
            ("help-80", cmd + ["--help"], size80, "pipe", None),
            ("version", cmd + ["--version"], {}, "pipe", None),
            ("bare", list(cmd), {}, "pipe", None),
            ("wrong-flag", target + ["--definitely-not-a-flag"], {}, "pipe", None),
            ("bad-input", target + bad_words, {}, "pipe", bad_default),
        ]
        if sub_words:
            plan.append(("sub-help", cmd + sub_words + ["--help"], size80, "pipe", None))
        plan += [
            ("tty", target + sample_words, {**size80, **tty_term}, "tty", sample_default),
            ("piped", target + sample_words, {}, "pipe", sample_default),
            ("no-color", target + sample_words, {"NO_COLOR": "1"}, "pipe", sample_default),
            ("term-dumb", target + sample_words, {"TERM": "dumb"}, "pipe", sample_default),
        ]

        probes, results = [], {}
        for name, words, overrides, mode, default_args in plan:
            shown = (env_prefix(overrides) + " " if overrides else "") + shlex.join(words)
            skip = None
            if name == "bare" and opts["skip_bare"]:
                skip = "skipped with --skip-bare"
            elif mode == "tty" and not HAVE_PTY:
                skip = f"no pseudo-terminal support on {platform.system() or sys.platform}"
            if skip:
                entry = {"probe": name, "argv": shown, "skipped": skip}
                if default_args is not None:
                    entry["default_args"] = default_args
                probes.append(entry)
                results[name] = entry
                with open(os.path.join(out_dir, f"{name}.txt"), "w", encoding="utf-8", newline="\n") as f:
                    f.write(f"$ {shown}\nskipped: {skip}\n")
                continue

            penv = {**env, **overrides}
            if mode == "tty":
                r = run_tty(real(words), exe, penv, cwd, timeout)
            else:
                r = run_piped(real(words), exe, penv, cwd, timeout)
            entry = {"probe": name, "argv": shown, "env": overrides, "exit": r["exit"],
                     "timed_out": r["timed_out"], "duration_ms": r["duration_ms"],
                     "stdout_bytes": len(r["stdout"]), "stderr_bytes": len(r["stderr"]),
                     "ansi_stdout": has_ansi(r["stdout"]), "ansi_stderr": has_ansi(r["stderr"]),
                     "tty": mode == "tty"}
            if r.get("held_open"):
                entry["held_open"] = True
            if default_args is not None:
                entry["default_args"] = default_args
            probes.append(entry)
            results[name] = dict(entry, _stdout=r["stdout"])
            header = f"$ {shown}" + (" (pseudo-tty 80x24)" if mode == "tty" else " | (piped)")
            exit_line = f"exit: {r['exit']}" + (f" (timed out after {timeout:g}s)" if r["timed_out"] else "")
            stdout_text = decode(r["stdout"]).rstrip("\n")
            stderr_text = decode(r["stderr"]).rstrip("\n")
            if mode == "tty":
                stderr_text = "(merged into stdout on the pseudo-terminal)"
            body = [header, exit_line, "--- stdout"] + ([stdout_text] if stdout_text else [])
            body += ["--- stderr"] + ([stderr_text] if stderr_text else [])
            with open(os.path.join(out_dir, f"{name}.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(body) + "\n")

        help_probe = results["help-80"]
        help_wide = lines_over(decode(help_probe["_stdout"]))
        startup_ms = help_probe["duration_ms"]

        flags = []
        for p in probes:
            if p.get("timed_out"):
                flags.append(f"{p['probe']} did not exit within {timeout:g}s (interactive, pager or long-running)"
                             + ("; if the bare command starts something, rerun with --skip-bare" if p["probe"] == "bare" else ""))
            if p.get("held_open"):
                flags.append(f"{p['probe']} exited but left a background process holding its output open")
        wf = results["wrong-flag"]
        if not wf["timed_out"]:
            if wf["exit"] not in USAGE_EXITS:
                flags.append(f"wrong flag exited {wf['exit']} (usage errors conventionally exit 2)")
            if wf["stderr_bytes"] == 0:
                flags.append("wrong flag reported nothing on stderr")
        bi = results["bad-input"]
        if bi["exit"] == 0:
            flags.append("bad input exited 0" + (" (default placeholder args: pass --bad-input for this tool)"
                                                 if bad_default else ""))
        for name, label in (("piped", "ANSI escapes in piped stdout"),
                            ("no-color", "ANSI escapes in piped stdout with NO_COLOR=1"),
                            ("term-dumb", "ANSI escapes in piped stdout with TERM=dumb")):
            if results[name].get("ansi_stdout"):
                flags.append(label)
        bare = results["bare"]
        if "skipped" not in bare and not bare["timed_out"] and bare["exit"] not in (0, 1, 2):
            flags.append(f"bare command exit {bare['exit']}")
        if help_wide > 0:
            flags.append(f"{help_wide} help lines wider than 80 columns")
        if startup_ms > 1000 and not help_probe["timed_out"]:
            flags.append(f"startup {startup_ms} ms")
        if results["version"]["exit"] != 0 and not results["version"]["timed_out"]:
            flags.append(f"--version failed (exit {results['version']['exit']})")
        if "skipped" in results["tty"]:
            flags.append(f"tty probe not run: {results['tty']['skipped']}")
        if use_scratch_home and help_probe["exit"] != 0 and not help_probe["timed_out"]:
            flags.append(f"--help exited {help_probe['exit']} with a scratch HOME "
                         "(rerun with --keep-home if the tool needs config or a toolchain from HOME)")

        summary = {
            "command": opts["command_display"],
            "sub": opts["sub"],
            "sample": DEFAULT_SAMPLE if sample_default else opts["sample"],
            "bad_input": bad_text,
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "timeout_s": timeout,
            "home": "scratch" if use_scratch_home else "real",
            "cwd": "scratch" if cwd == scratch_work else cwd,
            "env_dropped": dropped,
            "help_lines_over_80": help_wide,
            "startup_ms": startup_ms,
            "probes": probes,
            "flags": flags,
        }
        summary_path = os.path.join(out_dir, "summary.json")
        with open(summary_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
            f.write("\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ran = sum(1 for p in probes if "skipped" not in p)
    log(f"{ran} probes → {summary_path}  help>80cols:{help_wide} startup:{startup_ms}ms")
    log("automated flags:" + ("".join(f"\n  - {x}" for x in flags) if flags else " none"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        log("interrupted; the running probe was killed")
        sys.exit(130)
