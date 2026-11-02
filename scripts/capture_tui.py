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
   The child gets a scrubbed environment: variables whose name looks secret (KEY, TOKEN, SECRET,
   PASSWORD, AUTH, SESSION, COOKIE, DSN, DATABASE_URL, PRIVATE, ...) are dropped; --keep-env NAME
   keeps one, --inherit-env keeps all. The dropped names (never values) go in the manifest as
   "env_dropped". PAGER=cat is set so the app can't block on a pager.

Sizes default to 80x24, 120x40 and 200x60 (--sizes to change). Each run writes
<out>/<name>__<cols>x<rows>[__<state>].svg|txt and <out>/manifest.json with per-size notes:
whether the app drew anything; possible clipping as "edge_lines" (rows whose text ends within one
column of the right edge, so it is probably cut or broken mid-word by wrapping: curses apps often
write text[:width-1] because the bottom-right cell can't be written. In Textual mode only text in
the last column counts, since wide_widgets measures clipping exactly and a padded right-aligned
footer hint would otherwise flag every screen. One trailing border/scrollbar glyph is looked past,
so text cut by a frame counts too; right-aligned text with one cell of padding is a known false
positive in pty mode) and
"ellipsis_lines" (rows containing "…" or "..."); in Textual mode also "wide_widgets" (widgets
whose content is wider than the space they get: nowrap text cut, a widget running off the right
edge, or a container that scrolls/hides content horizontally); and the pty exit status.

Install: pip install textual pyte pexpect pillow
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
SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS$|CREDENTIAL|AUTH|SESSION|COOKIE|DSN|DATABASE_URL|PRIVATE", re.I)


def scrub_env(environ: dict, keep=(), inherit: bool = False):
    """(env without secret-looking variables, sorted dropped NAMES)."""
    if inherit:
        return dict(environ), []
    keep = set(keep)
    dropped = sorted(k for k in environ if SECRET_ENV.search(k) and k not in keep)
    return {k: v for k, v in environ.items() if k not in dropped}, dropped


def _is_frame(ch: str) -> bool:
    # box drawing (U+2500-257F) and block elements (U+2580-259F): borders and scrollbars, not text
    return len(ch) == 1 and 0x2500 <= ord(ch) <= 0x259F


def touches_edge(cells, slack: int = 1) -> bool:
    """cells: one row, exactly cols cells. True if text ends within one column of the right edge
    (index >= cols-1-slack; slack=1 because many curses apps write text[:width-1]; slack=0: the last
    column only). One border/scrollbar glyph at the end of the row is looked past, so text running
    into a right-hand frame counts too."""
    cols = len(cells)
    i = cols - 1
    while i >= 0 and cells[i] in ("", " "):
        i -= 1
    if i < cols - 1 - slack:
        return False
    if _is_frame(cells[i]):
        i -= 1  # the frame/scrollbar itself; the text must run right up to it
    return i >= 0 and cells[i] not in ("", " ") and not _is_frame(cells[i])


def line_cells(line: str, cols: int):
    """Split a rendered line into terminal cells (wide characters take two), padded/cut to exactly cols."""
    try:
        from rich.cells import get_character_cell_size
    except ImportError:  # pragma: no cover
        def get_character_cell_size(_c):
            return 1
    cells = []
    for ch in line:
        w = get_character_cell_size(ch)
        if w <= 0:
            continue
        cells.append(ch)
        cells.extend([""] * (w - 1))
    cells = cells[:cols]
    return cells + [" "] * (cols - len(cells))


def ellipsis_count(lines) -> int:
    return sum(1 for l in lines if "…" in l or "..." in l)


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
            txt.write_text(text if text is not None else svg_to_text(svg.read_text(encoding="utf-8")), encoding="utf-8")
            lines = txt.read_text(encoding="utf-8").splitlines()[:rows]
            entries.append({"mode": "textual", "cols": cols, "rows": rows, "state": state, "keys": keys,
                            "svg": svg.name, "txt": txt.name, "drew_anything": any(l.strip() for l in lines),
                            "edge_lines": sum(1 for l in lines if touches_edge(line_cells(l, cols), slack=0)),
                            "ellipsis_lines": ellipsis_count(lines),
                            "wide_widgets": wide_widgets(app, cols, rows),
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


def wide_widgets(app, cols: int, rows: int):
    """Widgets whose content is wider than the room they get (max 20). None if it could not be measured.

    Measured, not guessed: virtual_size alone misses a cut nowrap Static (its virtual size equals its
    size) and flags stock Header/Footer parts (padding), so three signals are used instead:
      nowrap    - text-wrap: nowrap and get_content_width() > content width (text cut or ellipsised)
      offscreen - the widget's region runs past the screen's right edge
      scroll    - a scrollable container whose content is wider than it (max_scroll_x > 0)"""
    try:
        from textual.geometry import Size
        screen = app.screen
        sw = screen.size.width or cols
        children = list(screen.walk_children())
    except Exception:  # noqa: BLE001
        return None
    out = []
    for w in children:
        try:
            if not w.display or w.size.width <= 0:
                continue
            item = None
            avail = w.content_size.width
            if str(w.styles.text_wrap) == "nowrap" and avail > 0:
                cw = w.get_content_width(Size(cols, rows), Size(cols, rows))
                if cw > avail:
                    item = {"content_width": int(cw), "width": int(avail), "why": "nowrap"}
            if item is None and w.region.width and w.region.right > sw:
                item = {"content_width": int(w.region.width), "width": int(max(0, sw - w.region.x)), "why": "offscreen"}
            if item is None and w.is_scrollable and w.max_scroll_x > 0:
                item = {"content_width": int(w.virtual_size.width), "width": int(w.virtual_size.width - w.max_scroll_x), "why": "scroll"}
            if item:
                out.append({"widget": type(w).__name__, "id": w.id, **item})
        except Exception:  # noqa: BLE001
            continue
    return out[:20]


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
def pty_mode(command: str, sizes, keys, state, out: pathlib.Path, settle: float, name: str | None, png: bool, env_extra: dict,
             base_env: dict | None = None):
    try:
        import pexpect
        import pyte
    except ImportError:
        sys.exit("pyte and pexpect are needed for pty mode: pip install pyte pexpect")
    import os

    base_env = dict(os.environ) if base_env is None else base_env
    argv = shlex.split(command)
    name = name or slug(pathlib.Path(argv[0]).name + ("-" + argv[1] if len(argv) > 1 and not argv[1].startswith("-") else ""))
    entries = []
    for cols, rows in sizes:
        screen = pyte.Screen(cols, rows)
        stream = pyte.ByteStream(screen)
        env = {**base_env, "TERM": "xterm-256color", "COLUMNS": str(cols), "LINES": str(rows), "PAGER": "cat", **env_extra}
        child = pexpect.spawn(argv[0], argv[1:], dimensions=(rows, cols), env=env, timeout=1, encoding=None)
        t_end = time.time() + settle
        feed(child, stream, t_end)
        for k in keys:
            child.send(key_to_bytes(k))
            feed(child, stream, time.time() + 0.3)
        feed(child, stream, time.time() + 0.3)
        raw = list(screen.display)  # each row is the full terminal width
        lines = [line.rstrip() for line in raw]
        edge = sum(1 for y in range(rows) if touches_edge([screen.buffer[y][x].data for x in range(cols)]))
        fname = f"{name}__{cols}x{rows}" + (f"__{slug(state)}" if state else "")
        txt = out / f"{fname}.txt"
        txt.write_text("\n".join(lines), encoding="utf-8")
        entry = {"mode": "pty", "cols": cols, "rows": rows, "state": state, "keys": keys, "txt": txt.name,
                 "drew_anything": any(l.strip() for l in lines),
                 "used_rows": sum(1 for l in lines if l.strip()),
                 "edge_lines": edge, "ellipsis_lines": ellipsis_count(raw),
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
    """Render the text dump to path; returns the file name (relative to the manifest folder) or None."""
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
    return path.name


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
    ap.add_argument("--keep-env", action="append", default=[], metavar="NAME",
                    help="pty mode: keep this secret-looking variable anyway (repeatable)")
    ap.add_argument("--inherit-env", action="store_true", help="pty mode: keep the whole environment, unscrubbed")
    a = ap.parse_args(argv)

    if a.png:
        try:
            import PIL  # noqa: F401
        except ImportError:
            sys.exit("--png needs Pillow in this interpreter: pip install pillow")

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sizes = parse_sizes(a.sizes)
    keys = parse_keys(a.keys)
    env_extra = dict(kv.split("=", 1) for kv in a.env)

    env_dropped = None
    if a.mode == "textual":
        entries = textual_mode(a.target, sizes, keys, a.state, out, a.settle, a.name)
    else:
        import os
        base_env, env_dropped = scrub_env(os.environ, keep=a.keep_env, inherit=a.inherit_env)
        if env_dropped:
            print(f"[capture_tui] not passed to the app: {', '.join(env_dropped)} (--keep-env NAME / --inherit-env)", file=sys.stderr)
        entries = pty_mode(a.target, sizes, keys, a.state, out, a.settle, a.name, a.png, env_extra, base_env)

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"captures": []}
    manifest["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    if env_dropped is not None:
        manifest["env_dropped"] = env_dropped
    manifest["captures"].extend(entries)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[capture_tui] {len(entries)} snapshots → {manifest_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
