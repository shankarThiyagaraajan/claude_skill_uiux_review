#!/usr/bin/env python3
"""Capture a running web app at several sizes, schemes and variants, with automated checks.

Writes PNGs plus captures/manifest.json. Each manifest entry records:
  overflow (horizontal scroll), console/page errors, failed requests, axe-core violations,
  small interactive targets, and (light scheme only) a Tab-key walk with focus visibility.

Usage
  python capture_web.py --url http://localhost:5173 --routes / /orders /settings
  python capture_web.py --url http://localhost:8000 --routes / --quick          # 1440 light only
  python capture_web.py --url … --storage-state state.json --locale en-IN --timezone Asia/Kolkata

Reuse from your own walk script (for the state matrix):
  from capture_web import Capturer
  with Capturer(out="captures") as c:
      page = c.new_page(width=360, height=800, scheme="light")
      page.goto("http://localhost:5173/orders/new"); page.fill("#qty", "10")
      c.snap(page, route="/orders/new", state="validation-error")
  (manifest is written on exit)

Browser: tries Playwright's Chromium, then the system Chrome (--channel chrome), then
--executable-path. Install with: pip install playwright axe-playwright-python
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import re
import sys
import time

try:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("playwright is not installed: pip install playwright  (then: playwright install chromium)")

DEFAULT_VIEWPORTS = ["360x800", "768x1024", "1440x900", "1920x1080"]
INTERACTIVE = "a[href], button, input, select, textarea, summary, [role=button], [role=link], [role=tab], [role=menuitem], [tabindex]:not([tabindex='-1'])"

JS_OVERFLOW = """() => {
  const d = document.documentElement, b = document.body;
  return { scroll_width: Math.max(d.scrollWidth, b ? b.scrollWidth : 0), client_width: d.clientWidth,
           overflow: Math.max(d.scrollWidth, b ? b.scrollWidth : 0) > d.clientWidth + 1 };
}"""

JS_SMALL_TARGETS = """(sel) => {
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (r.width < 44 || r.height < 44) {
      out.push({ selector: cssPath(el), w: Math.round(r.width), h: Math.round(r.height),
                 text: (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 40),
                 below_24: r.width < 24 || r.height < 24 });
    }
  }
  return out.slice(0, 60);
  function cssPath(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 4) {
      let s = el.tagName.toLowerCase();
      if (el.className && typeof el.className === 'string') s += '.' + el.className.trim().split(/\\s+/).slice(0, 2).map(CSS.escape).join('.');
      parts.unshift(s); el = el.parentElement;
    }
    return parts.join(' > ');
  }
}"""

JS_FOCUS_INFO = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return { tag: 'body' };
  const pick = (cs) => [cs.outlineStyle, cs.outlineWidth, cs.outlineColor, cs.boxShadow, cs.borderColor, cs.backgroundColor, cs.textDecorationLine].join('|');
  const focused = pick(getComputedStyle(el));
  el.blur(); const blurred = pick(getComputedStyle(el)); el.focus();
  const r = el.getBoundingClientRect();
  return { tag: el.tagName.toLowerCase(), role: el.getAttribute('role'),
           name: (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.id || '').trim().slice(0, 40),
           x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
           in_viewport: r.bottom > 0 && r.top < innerHeight,
           focus_visible: focused !== blurred };
}"""

JS_PAGE_INFO = """() => ({ title: document.title, h1: Array.from(document.querySelectorAll('h1')).map(h => h.innerText.trim()).slice(0, 3),
                         lang: document.documentElement.lang || null })"""


def slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip("/") or "home").strip("-")
    return s[:60] or "home"


def find_axe(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    spec = importlib.util.find_spec("axe_playwright_python")
    if spec and spec.origin:
        p = pathlib.Path(spec.origin).parent / "axe.min.js"
        if p.exists():
            return str(p)
    for p in (pathlib.Path("node_modules/axe-core/axe.min.js"), pathlib.Path.home() / "node_modules/axe-core/axe.min.js"):
        if p.exists():
            return str(p)
    return None


class Capturer:
    def __init__(self, out: str = "captures", channel: str | None = None, executable_path: str | None = None,
                 axe: str | None = None, storage_state: str | None = None, locale: str | None = None,
                 timezone: str | None = None, wait: str = "networkidle", settle_ms: int = 500,
                 timeout_ms: int = 20000, tab_steps: int = 30, headers: dict | None = None, full_width: bool = False):
        self.full_width = full_width
        self.out = pathlib.Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.channel, self.executable_path = channel, executable_path
        self.axe_path = find_axe(axe)
        self.axe_src = pathlib.Path(self.axe_path).read_text() if self.axe_path else None
        self.storage_state, self.locale, self.timezone = storage_state, locale, timezone
        self.wait, self.settle_ms, self.timeout_ms, self.tab_steps = wait, settle_ms, timeout_ms, tab_steps
        self.headers = headers or {}
        self.captures: list[dict] = []
        self._pw = None
        self.browser = None
        self.browser_desc = None
        self._contexts = []

    # -- lifecycle -------------------------------------------------------------
    def __enter__(self):
        self._pw = sync_playwright().start()
        self.browser = self._launch()
        return self

    def __exit__(self, *exc):
        for c in self._contexts:
            try:
                c.close()
            except Exception:
                pass
        if self.browser:
            self.browser.close()
        if self._pw:
            self._pw.stop()
        self.write_manifest()

    def _launch(self):
        attempts = []
        if self.executable_path:
            attempts.append(("executable_path", dict(executable_path=self.executable_path)))
        if self.channel:
            attempts.append((f"channel={self.channel}", dict(channel=self.channel)))
        attempts += [("playwright chromium", {}), ("channel=chrome", dict(channel="chrome")),
                     ("channel=chromium", dict(channel="chromium")),
                     ("/usr/bin/google-chrome", dict(executable_path="/usr/bin/google-chrome")),
                     ("/usr/bin/chromium", dict(executable_path="/usr/bin/chromium"))]
        errors = []
        for desc, kw in attempts:
            try:
                b = self._pw.chromium.launch(headless=True, args=["--no-sandbox"], **kw)
                self.browser_desc = desc
                print(f"[capture_web] browser: {desc}", file=sys.stderr)
                return b
            except Exception as e:  # noqa: BLE001
                errors.append(f"{desc}: {str(e).splitlines()[0][:120]}")
        sys.exit("[capture_web] no browser could be launched:\n  " + "\n  ".join(errors) +
                 "\nFallback: the desktop app's built-in browser (preview_start + resize_window) or Claude in Chrome; "
                 "otherwise list the sizes under Not checked.")

    # -- pages -----------------------------------------------------------------
    def new_page(self, width: int, height: int, scheme: str = "light", reduced_motion: bool = False):
        kw = dict(viewport={"width": width, "height": height}, color_scheme=scheme,
                  reduced_motion="reduce" if reduced_motion else "no-preference",
                  device_scale_factor=1, extra_http_headers=self.headers or None)
        if self.storage_state:
            kw["storage_state"] = self.storage_state
        if self.locale:
            kw["locale"] = self.locale
        if self.timezone:
            kw["timezone_id"] = self.timezone
        ctx = self.browser.new_context(**{k: v for k, v in kw.items() if v is not None})
        self._contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(self.timeout_ms)
        log = {"console_errors": [], "page_errors": [], "failed_requests": []}
        page.on("console", lambda m: log["console_errors"].append(m.text[:300]) if m.type in ("error",) else None)
        page.on("pageerror", lambda e: log["page_errors"].append(str(e)[:300]))
        page.on("requestfailed", lambda r: log["failed_requests"].append({"url": r.url[:200], "error": (r.failure or "")[:120]}))
        page.on("response", lambda r: log["failed_requests"].append({"url": r.url[:200], "status": r.status}) if r.status >= 400 else None)
        page._uiux_log = log  # type: ignore[attr-defined]
        page._uiux_meta = {"width": width, "height": height, "scheme": scheme, "reduced_motion": reduced_motion}  # type: ignore[attr-defined]
        return page

    def goto(self, page, url: str) -> float:
        t0 = time.time()
        try:
            page.goto(url, wait_until=self.wait)
        except PWTimeout:
            print(f"[capture_web] {self.wait} timed out for {url}; continuing after 'load'", file=sys.stderr)
            try:
                page.wait_for_load_state("load")
            except PWError:
                pass
        if self.settle_ms:
            page.wait_for_timeout(self.settle_ms)
        return round((time.time() - t0) * 1000)

    # -- the capture itself ----------------------------------------------------
    def snap(self, page, route: str, state: str = "default", variant: str | None = None,
             run_axe: bool = True, tab_walk: bool | None = None, full_page: bool = True, load_ms: float | None = None) -> dict:
        meta = page._uiux_meta  # type: ignore[attr-defined]
        log = page._uiux_log  # type: ignore[attr-defined]
        name = f"{slug(route)}__{meta['width']}__{meta['scheme']}"
        if state != "default":
            name += f"__{slug(state)}"
        if variant:
            name += f"__{variant}"
        file = self.out / f"{name}.png"
        entry = {"route": route, "state": state, "variant": variant, "file": str(file),
                 "width": meta["width"], "height": meta["height"], "scheme": meta["scheme"],
                 "reduced_motion": meta["reduced_motion"], "url": page.url, "load_ms": load_ms}
        try:
            if full_page and not self.full_width:
                # full height, but clipped to the viewport width: what the user actually sees (overflow is cut off,
                # which is the point). --full-width keeps Playwright's default of widening to the scroll width.
                sh = page.evaluate("Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)")
                page.screenshot(path=str(file), full_page=True,
                                clip={"x": 0, "y": 0, "width": meta["width"], "height": min(max(sh, meta["height"]), 8000)})
            else:
                page.screenshot(path=str(file), full_page=full_page)
        except PWError as e:
            entry["error"] = f"screenshot: {str(e)[:200]}"
            self.captures.append(entry)
            return entry
        try:
            entry.update(page.evaluate(JS_PAGE_INFO))
            entry["overflow"] = page.evaluate(JS_OVERFLOW)
            entry["small_targets"] = page.evaluate(JS_SMALL_TARGETS, INTERACTIVE)
        except PWError as e:
            entry["error"] = f"evaluate: {str(e)[:200]}"
        entry["console_errors"] = list(dict.fromkeys(log["console_errors"]))[:20]
        entry["page_errors"] = list(dict.fromkeys(log["page_errors"]))[:20]
        seen, failed = set(), []
        for r in log["failed_requests"]:
            key = (r.get("url"), r.get("status"), r.get("error"))
            if key not in seen:
                seen.add(key)
                failed.append(r)
        entry["failed_requests"] = failed[:30]
        if run_axe:
            entry["axe"] = self.run_axe(page)
        if tab_walk is None:
            tab_walk = meta["scheme"] == "light" and not meta["reduced_motion"] and variant is None
        if tab_walk:
            entry["tab_walk"] = self.walk_tabs(page)
        self.captures.append(entry)
        return entry

    def run_axe(self, page) -> dict:
        if not self.axe_src:
            return {"skipped": "axe.min.js not found (pip install axe-playwright-python, or npm i axe-core)"}
        try:
            page.evaluate(self.axe_src)
            res = page.evaluate("async () => await axe.run(document, { resultTypes: ['violations'] })")
        except PWError as e:
            return {"error": str(e)[:200]}
        viol = []
        for v in res.get("violations", []):
            viol.append({"id": v["id"], "impact": v.get("impact"), "help": v.get("help"), "help_url": v.get("helpUrl"),
                         "count": len(v.get("nodes", [])),
                         "nodes": [n["target"][0] if n.get("target") else "?" for n in v.get("nodes", [])][:5]})
        order = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3, None: 4}
        viol.sort(key=lambda v: order.get(v["impact"], 4))
        return {"violations": viol, "counts": {k: sum(1 for v in viol if v["impact"] == k) for k in ("critical", "serious", "moderate", "minor")}}

    def walk_tabs(self, page) -> dict:
        steps = []
        try:
            page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); }")
            seen = set()
            for i in range(self.tab_steps):
                page.keyboard.press("Tab")
                info = page.evaluate(JS_FOCUS_INFO)
                info["step"] = i + 1
                steps.append(info)
                key = (info.get("tag"), info.get("name"), info.get("x"), info.get("y"))
                if info.get("tag") == "body" and i > 0:
                    break  # focus left the document (cycled to browser chrome)
                if key in seen and i > 3:
                    steps[-1]["note"] = "cycle"  # focus returned to an earlier element: a loop or trap
                    break
                seen.add(key)
        except PWError as e:
            return {"error": str(e)[:200], "steps": steps}
        focusable = [s for s in steps if s.get("tag") != "body"]
        return {"steps": steps,
                "reached": len(focusable),
                "invisible_focus": [s["step"] for s in focusable if s.get("focus_visible") is False],
                "offscreen": [s["step"] for s in focusable if s.get("in_viewport") is False]}

    # -- manifest -------------------------------------------------------------
    def write_manifest(self):
        summary = {
            "captures": len(self.captures),
            "with_overflow": sum(1 for c in self.captures if (c.get("overflow") or {}).get("overflow")),
            "with_console_errors": sum(1 for c in self.captures if c.get("console_errors") or c.get("page_errors")),
            "with_failed_requests": sum(1 for c in self.captures if c.get("failed_requests")),
            "axe_serious_or_critical": sum((c.get("axe") or {}).get("counts", {}).get("critical", 0) + (c.get("axe") or {}).get("counts", {}).get("serious", 0) for c in self.captures),
            "with_targets_below_24": sum(1 for c in self.captures if any(t.get("below_24") for t in c.get("small_targets") or [])),
            "with_invisible_focus": sum(1 for c in self.captures if (c.get("tab_walk") or {}).get("invisible_focus")),
            "errors": sum(1 for c in self.captures if c.get("error")),
        }
        manifest = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), "browser": self.browser_desc,
                    "axe": self.axe_path, "summary": summary, "captures": self.captures}
        (self.out / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"[capture_web] {summary['captures']} captures → {self.out}/manifest.json  "
              f"overflow:{summary['with_overflow']} console:{summary['with_console_errors']} "
              f"failed-req:{summary['with_failed_requests']} axe-serious+:{summary['axe_serious_or_critical']} "
              f"targets<24:{summary['with_targets_below_24']} invisible-focus:{summary['with_invisible_focus']}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="base URL, e.g. http://localhost:5173")
    ap.add_argument("--routes", nargs="*", default=["/"], help="paths to capture (default: /)")
    ap.add_argument("--out", default="captures")
    ap.add_argument("--viewports", nargs="*", default=DEFAULT_VIEWPORTS, help="WxH list")
    ap.add_argument("--schemes", nargs="*", default=["light", "dark"], choices=["light", "dark"])
    ap.add_argument("--quick", action="store_true", help="1440x900 light only, no variants")
    ap.add_argument("--no-reduced-motion", action="store_true", help="skip the reduced-motion variant")
    ap.add_argument("--no-zoom", action="store_true", help="skip the 200%% zoom (640 px wide) variant")
    ap.add_argument("--no-axe", action="store_true")
    ap.add_argument("--axe", help="path to axe.min.js (auto-detected from axe-playwright-python or node_modules)")
    ap.add_argument("--channel", help="Playwright channel, e.g. chrome")
    ap.add_argument("--executable-path", help="browser binary to use")
    ap.add_argument("--storage-state", help="Playwright storageState JSON for a local test account")
    ap.add_argument("--header", action="append", default=[], help="extra request header 'Name: value' (repeatable)")
    ap.add_argument("--locale", help="e.g. en-IN")
    ap.add_argument("--timezone", help="e.g. Asia/Kolkata")
    ap.add_argument("--wait", default="networkidle", choices=["load", "domcontentloaded", "networkidle"])
    ap.add_argument("--settle", type=int, default=500, help="ms to wait after load before capturing")
    ap.add_argument("--timeout", type=int, default=20000, help="navigation timeout ms")
    ap.add_argument("--tab-steps", type=int, default=30)
    ap.add_argument("--viewport-only", action="store_true", help="capture the viewport instead of the full page")
    ap.add_argument("--full-width", action="store_true", help="let full-page captures widen to the scroll width (shows the overflow instead of cutting it)")
    a = ap.parse_args(argv)

    headers = {}
    for h in a.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()
    viewports = ["1440x900"] if a.quick else a.viewports
    schemes = ["light"] if a.quick else a.schemes
    base = a.url.rstrip("/")

    with Capturer(out=a.out, channel=a.channel, executable_path=a.executable_path, axe=a.axe,
                  storage_state=a.storage_state, locale=a.locale, timezone=a.timezone, wait=a.wait,
                  settle_ms=a.settle, timeout_ms=a.timeout, tab_steps=a.tab_steps, headers=headers, full_width=a.full_width) as c:
        for route in a.routes:
            url = base + (route if route.startswith("/") else "/" + route)
            for vp in viewports:
                w, h = (int(x) for x in vp.lower().split("x"))
                for scheme in schemes:
                    page = c.new_page(w, h, scheme)
                    ms = c.goto(page, url)
                    e = c.snap(page, route, run_axe=not a.no_axe, full_page=not a.viewport_only, load_ms=ms)
                    print(f"  {e['file']}" + (f"  [{e['error']}]" if e.get("error") else ""), file=sys.stderr)
                    page.context.close()
            if not a.quick and not a.no_reduced_motion:
                page = c.new_page(1440, 900, "light", reduced_motion=True)
                ms = c.goto(page, url)
                c.snap(page, route, variant="reduced-motion", run_axe=False, load_ms=ms)
                page.context.close()
            if not a.quick and not a.no_zoom:
                page = c.new_page(640, 720, "light")  # 1280 px window at 200% zoom = 640 CSS px (WCAG 1.4.10 reflow)
                ms = c.goto(page, url)
                c.snap(page, route, variant="zoom200", run_axe=False, load_ms=ms)
                page.context.close()


if __name__ == "__main__":
    main()
