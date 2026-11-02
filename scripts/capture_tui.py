#!/usr/bin/env python3
"""Snapshot a terminal UI at several terminal sizes.

Two modes:

1. Textual app (preferred, exact rendering, SVG output):
     python capture_tui.py textual myapp.tui:MyApp --out captures-tui
     python capture_tui.py textual myapp.tui:MyApp --keys "tab,j,j,enter" --state after-select
   `module:Class` is imported and run headless through Textual's pilot at each size.
   Keys are pressed in order before the snapshot (Textual key names: tab, enter, escape, j, ctrl+c, ...).

2. Any other TUI (curses, Bubble Tea, Ratatui, htop-style), via a pseudo-terminal and a VT100 emulator:
     python capture_tui.py pty "python -m myapp" --out captures-tui
     python capture_tui.py pty "./mytui --demo" --keys "tab,j,j,enter" --settle 1.5
   The screen is dumped as text (what a screen reader or a text dump would see). Colour is lost;
   that is a feature for the "readable without colour" check. Rendered PNGs can be made from the
   text with --png (needs Pillow and a monospace font).

Sizes default to 80x24, 120x40 and 200x60 (--sizes to change). Each run writes
<out>/<name>__<cols>x<rows>[__<state>].svg|txt and <out>/manifest.json with per-size notes:
whether the app drew anything, how many lines were clipped (text wider than the terminal), and
the pty exit status.

Install: pip install textual pyte pexpect
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import importlib
import json
import pathlib
import re
import shlex
import sys
import time

DEFAULT_SIZES = ["80x24", "120x40", "200x60"]


def parse_sizes(sizes):
    out = []
    for s in sizes:
        c, r = s.lower().split("x")
        out.append((int(c), int(r)))
    return out


def parse_keys(keys: str | None):
    return [k.strip() for k in keys.split(",") if k.strip()] if keys else []


# ---------------------------------------------------------------- Textual mode
def textual_mode(target: str, sizes, keys, state, out: pathlib.Path, settle: float, name: str | None):
    try:
        from textual.app import App  # noqa: F401
    except ImportError:
        sys.exit("textual is not installed: pip install textual")
    mod_name, _, cls_name = target.partition(":")
    if not cls_name:
        sys.exit("textual target must be module:Class, e.g. myapp.tui:MyApp")
    sys.path.insert(0, str(pathlib.Path.cwd()))
    mod = importlib.import_module(mod_name)
    app_cls = getattr(mod, cls_name)
    name = name or cls_name
    entries = []

    async def run_one(cols, rows):
        app = app_cls()
        async with app.run_test(size=(cols, rows)) as pilot:
            await pilot.pause(settle)
            for k in keys:
                await pilot.press(k)
                await pilot.pause(0.05)
            await pilot.pause(0.2)
            fname = f"{name}__{cols}x{rows}" + (f"__{slug(state)}" if state else "")
            svg = out / f"{fname}.svg"
            app.save_screenshot(filename=svg.name, path=str(out))
            # a plain-text rendering (what a screen reader or a NO_COLOR terminal gets) for the
            # "readable without colour" check and for diffing between runs
            txt = out / f"{fname}.txt"
            text = export_text(app, cols, rows)
            txt.write_text(text if text is not None else svg_to_text(svg.read_text()))
            lines = txt.read_text().splitlines()
            entries.append({"mode": "textual", "cols": cols, "rows": rows, "state": state, "keys": keys,
                            "svg": str(svg), "txt": str(txt), "drew_anything": any(l.strip() for l in lines),
                            "clipped_lines": sum(1 for l in lines if len(l) > cols),
                            "screen_title": getattr(app, "title", None), "focused": type(app.focused).__name__ if app.focused else None,
                            "bindings": sorted({b.key for b in getattr(app, "BINDINGS", []) if hasattr(b, "key")})})
            print(f"  {svg}", file=sys.stderr)

    for cols, rows in sizes:
        try:
            asyncio.run(run_one(cols, rows))
        except Exception as e:  # noqa: BLE001
            entries.append({"mode": "textual", "cols": cols, "rows": rows, "state": state, "error": str(e)[:300]})
            print(f"  {cols}x{rows}: ERROR {str(e)[:120]}", file=sys.stderr)
    return entries


def export_text(app, cols: int, rows: int) -> str | None:
    """Render the current screen to plain text the same way Textual renders its SVG screenshot."""
    try:
        import io
        from rich.console import Console
        console = Console(width=cols, height=rows, file=io.StringIO(), force_terminal=True, color_system="truecolor",
                          record=True, legacy_windows=False, safe_box=False)
        render = app.screen._compositor.render_update(full=True, screen_stack=app._background_screens, simplify=True)
        console.print(render)
        return console.export_text(styles=False)
    except Exception:  # noqa: BLE001 — private API; fall back to parsing the SVG
        return None


def svg_to_text(svg: str) -> str:
    """Rough text extraction from a Textual SVG screenshot (one line per <text> row group)."""
    rows: dict[float, list[tuple[float, str]]] = {}
    for m in re.finditer(r'<text[^>]*x="([\d.]+)"[^>]*y="([\d.]+)"[^>]*>(.*?)</text>', svg, re.S):
        x, y, t = float(m.group(1)), float(m.group(2)), re.sub(r"<[^>]+>", "", m.group(3))
        t = t.replace("&#160;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        rows.setdefault(round(y, 1), []).append((x, t))
    return "\n".join("".join(t for _, t in sorted(parts)) for _, parts in sorted(rows.items()))


# -------------------------------------------------------------------- pty mode
def pty_mode(command: str, sizes, keys, state, out: pathlib.Path, settle: float, name: str | None, png: bool, env_extra: dict):
    try:
        import pexpect
        import pyte
    except ImportError:
        sys.exit("pyte and pexpect are needed for pty mode: pip install pyte pexpect")
    import os

    argv = shlex.split(command)
    name = name or slug(pathlib.Path(argv[0]).name + ("-" + argv[1] if len(argv) > 1 and not argv[1].startswith("-") else ""))
    entries = []
    for cols, rows in sizes:
        screen = pyte.Screen(cols, rows)
        stream = pyte.ByteStream(screen)
        env = {**os.environ, "TERM": "xterm-256color", "COLUMNS": str(cols), "LINES": str(rows), **env_extra}
        child = pexpect.spawn(argv[0], argv[1:], dimensions=(rows, cols), env=env, timeout=1, encoding=None)
        t_end = time.time() + settle
        feed(child, stream, t_end)
        for k in keys:
            child.send(key_to_bytes(k))
            feed(child, stream, time.time() + 0.3)
        feed(child, stream, time.time() + 0.3)
        lines = [line.rstrip() for line in screen.display]
        fname = f"{name}__{cols}x{rows}" + (f"__{slug(state)}" if state else "")
        txt = out / f"{fname}.txt"
        txt.write_text("\n".join(lines))
        entry = {"mode": "pty", "cols": cols, "rows": rows, "state": state, "keys": keys, "txt": str(txt),
                 "drew_anything": any(l.strip() for l in lines),
                 "used_rows": sum(1 for l in lines if l.strip()),
                 "cursor": {"x": screen.cursor.x, "y": screen.cursor.y},
                 "alive_after_capture": child.isalive()}
        if png:
            entry["png"] = render_png(lines, out / f"{fname}.png", cols, rows)
        # try to leave politely, then force
        for seq in (b"q", b"\x1b", b"\x03"):
            if not child.isalive():
                break
            child.send(seq)
            feed(child, stream, time.time() + 0.2)
        if child.isalive():
            child.terminate(force=True)
        entry["exit_status"] = child.exitstatus
        entries.append(entry)
        print(f"  {txt}", file=sys.stderr)
    return entries


def feed(child, stream, until: float):
    import pexpect
    while time.time() < until:
        try:
            data = child.read_nonblocking(size=65536, timeout=0.05)
            if data:
                stream.feed(data)
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            break


KEYMAP = {"enter": b"\r", "return": b"\r", "tab": b"\t", "shift+tab": b"\x1b[Z", "escape": b"\x1b", "esc": b"\x1b",
          "space": b" ", "backspace": b"\x7f", "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C", "left": b"\x1b[D",
          "pageup": b"\x1b[5~", "pagedown": b"\x1b[6~", "home": b"\x1b[H", "end": b"\x1b[F", "f1": b"\x1bOP", "question_mark": b"?"}


def key_to_bytes(k: str) -> bytes:
    k = k.strip()
    if k.lower() in KEYMAP:
        return KEYMAP[k.lower()]
    if k.lower().startswith("ctrl+") and len(k) == 6:
        return bytes([ord(k[-1].lower()) - 96])
    return k.encode()


def render_png(lines, path: pathlib.Path, cols: int, rows: int) -> str | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    font = None
    for cand in ("DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "Menlo.ttc", "Consolas.ttf"):
        try:
            font = ImageFont.truetype(cand, 14)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    cw, ch = 9, 18
    img = Image.new("RGB", (cols * cw + 16, rows * ch + 16), (20, 20, 24))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines[:rows]):
        d.text((8, 8 + i * ch), line[:cols], fill=(220, 220, 220), font=font)
    img.save(path)
    return str(path)


def slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-")[:40]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["textual", "pty"])
    ap.add_argument("target", help="textual: module:Class   pty: the command to run (quoted)")
    ap.add_argument("--out", default="captures-tui")
    ap.add_argument("--sizes", nargs="*", default=DEFAULT_SIZES, help="COLSxROWS list")
    ap.add_argument("--keys", help="comma-separated keys to press before the snapshot")
    ap.add_argument("--state", help="label for this snapshot, e.g. after-select, empty, error")
    ap.add_argument("--name", help="file name prefix (default: class or command name)")
    ap.add_argument("--settle", type=float, default=1.0, help="seconds to wait for the first draw")
    ap.add_argument("--png", action="store_true", help="pty mode: also render the text dump to PNG")
    ap.add_argument("--env", action="append", default=[], help="pty mode: extra env, NAME=value (e.g. NO_COLOR=1)")
    a = ap.parse_args(argv)

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sizes = parse_sizes(a.sizes)
    keys = parse_keys(a.keys)
    env_extra = dict(kv.split("=", 1) for kv in a.env)

    if a.mode == "textual":
        entries = textual_mode(a.target, sizes, keys, a.state, out, a.settle, a.name)
    else:
        entries = pty_mode(a.target, sizes, keys, a.state, out, a.settle, a.name, a.png, env_extra)

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"captures": []}
    manifest["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    manifest["captures"].extend(entries)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[capture_tui] {len(entries)} snapshots → {manifest_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
