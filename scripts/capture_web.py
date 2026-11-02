#!/usr/bin/env python3
"""Capture a running web app at several sizes, schemes and variants, with automated checks.

Writes PNGs plus captures/manifest.json (file paths in it are relative to that folder). A run merges
into an existing manifest in --out (a capture of the same file replaces the old entry); --fresh
starts a new one. Each manifest entry records:
  overflow (horizontal scroll), console/page errors, failed requests, axe-core violations,
  small interactive targets, and (light scheme only) a Tab-key walk with focus visibility.

Usage
  python capture_web.py --check                                                 # preflight: browser, axe, Pillow
  python capture_web.py --url http://localhost:5173 --routes / /orders /settings
  python capture_web.py --url http://localhost:8000 --routes / --quick          # 1440 light only
  python capture_web.py --url … --storage-state state.json --locale en-IN --timezone Asia/Kolkata
  python capture_web.py --url … --wait-for "#app [data-ready]" --block-third-party --mask ".email"

Safety defaults: URLs in the manifest lose their query string and fragment (--keep-query keeps them);
tokens/keys/passwords in console text and error messages are redacted (REDACT_PATTERNS); password,
card-number/CVC and [data-secret] fields are masked in screenshots (add more with --mask);
--header values are sent only to the app's own origin; requests to other origins are listed per
capture under "third_party" (and aborted with --block-third-party).

Reuse from your own walk script (for the state matrix):
  from capture_web import Capturer
  with Capturer(out="captures") as c:
      page = c.new_page(width=360, height=800, scheme="light")
      c.goto(page, "http://localhost:5173/orders/new"); page.fill("#qty", "10")
      c.snap(page, route="/orders/new", state="validation-error")
  (on exit the captures are merged into captures/manifest.json; Capturer(..., fresh=True) starts a new one.
   Console errors and failed requests are per snap(): the log is cleared by c.goto() and after each snap())

Browser: tries --executable-path, then --channel, then Playwright's bundled Chromium, then the
system Chrome/Chromium (channel chrome, channel chromium, /usr/bin/google-chrome, /usr/bin/chromium). Install with: pip install playwright axe-playwright-python pillow
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import importlib.util
import json
import pathlib
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

try:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("playwright is not installed: pip install playwright  (then: playwright install chromium)")

DEFAULT_VIEWPORTS = ["360x800", "768x1024", "1440x900", "1920x1080"]
DEFAULT_MASKS = ["input[type=password]", '[autocomplete="cc-number"]', '[autocomplete="cc-csc"]', "[data-secret]"]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
UNREACHABLE = ("ERR_CONNECTION_REFUSED", "ERR_NAME_NOT_RESOLVED", "ERR_ADDRESS_UNREACHABLE", "ERR_CONNECTION_RESET")

# Secrets that must not reach the manifest (console text, error messages, kept query strings).
REDACT_PATTERNS = [
    (re.compile(r"(Bearer\s+)\S+", re.I), r"\1[redacted]"),
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"), "[redacted-jwt]"),
    (re.compile(r"(sk|pk|rk)_(live|test)_\w+"), r"\1_\2_[redacted]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[redacted-aws-key]"),
    (re.compile(r"(token|key|secret|password|passwd|api_key|apikey|sig|signature|auth)=([\"']?)[^&\s\"']+", re.I), r"\1=\2[redacted]"),
]


def redact(text) -> str:
    text = "" if text is None else str(text)
    for pat, rep in REDACT_PATTERNS:
        text = pat.sub(rep, text)
    return text


def clean_url(url: str, keep_query: bool = False) -> str:
    """URL as written to the manifest: no user:password@, no query/fragment unless keep_query (then redacted)."""
    try:
        p = urlsplit(url)
    except ValueError:
        return redact(url)[:200]
    if p.scheme in ("data", "blob", "javascript"):
        return f"{p.scheme}:…"
    netloc = p.netloc.rpartition("@")[2]
    if keep_query:
        return redact(urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment)))
    return urlunsplit((p.scheme, netloc, p.path, "", ""))


def _origin_key(url: str):
    """(scheme, host, port) with default ports filled in; None for non-network URLs."""
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return None
    if p.scheme not in ("http", "https", "ws", "wss") or not p.hostname:
        return None
    if port is None:
        port = 443 if p.scheme in ("https", "wss") else 80
    return (p.scheme, p.hostname.lower(), port)


def origin_of(url: str) -> str | None:
    k = _origin_key(url)
    if not k:
        return None
    scheme, host, port = k
    host = f"[{host}]" if ":" in host else host
    default = 443 if scheme in ("https", "wss") else 80
    return f"{scheme}://{host}" + ("" if port == default else f":{port}")


_URL_IN_TEXT = re.compile(r"(?:https?|wss?)://[^\s'\"<>()]+")


def scrub(text, keep_query: bool = False) -> str:
    """Text for the manifest: embedded URLs cleaned like clean_url(), then REDACT_PATTERNS applied."""
    text = "" if text is None else str(text)
    if not keep_query:
        text = _URL_IN_TEXT.sub(lambda m: clean_url(m.group(0)), text)
    return redact(text)


class NavigationFailed(Exception):
    """page.goto failed for a reason other than 'the app is not running' (recorded as an error entry)."""
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
                 text: (el.getAttribute('aria-label') || el.innerText || (secret(el) ? '' : el.value) || '').trim().slice(0, 40),
                 below_24: r.width < 24 || r.height < 24 });
    }
  }
  return out.slice(0, 60);
  function secret(el) { return el.type === 'password' || /^cc-/.test(el.autocomplete || '') || el.hasAttribute('data-secret'); }
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
  const secret = el.type === 'password' || /^cc-/.test(el.autocomplete || '') || el.hasAttribute('data-secret');
  return { tag: el.tagName.toLowerCase(), role: el.getAttribute('role'),
           name: (el.getAttribute('aria-label') || el.innerText || (secret ? '' : el.value) || el.placeholder || el.id || '').trim().slice(0, 40),
           x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
           in_viewport: r.bottom > 0 && r.top < innerHeight,
           focus_visible: focused !== blurred };
}"""

JS_PAGE_INFO = """() => ({ title: document.title, h1: Array.from(document.querySelectorAll('h1')).map(h => h.innerText.trim()).slice(0, 3),
                         lang: document.documentElement.lang || null })"""


def slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip("/") or "home").strip("-")
    return s[:60] or "home"


def reset_log(page) -> None:
    """Empty the page's per-capture log (console/page errors, failed requests, third-party origins)."""
    log = getattr(page, "_uiux_log", None)
    if not log:
        return
    for k in ("console_errors", "page_errors", "failed_requests", "third_party", "blocked"):
        log[k].clear()
    # blocked_urls stays: it filters late 'requestfailed' events for requests we aborted ourselves


def find_axe(explicit: str | None) -> tuple[str | None, str | None]:
    """(path to axe.min.js, short source label). The label, not the local path, goes in the manifest."""
    if explicit:
        return explicit, "custom"
    spec = importlib.util.find_spec("axe_playwright_python")
    if spec and spec.origin:
        p = pathlib.Path(spec.origin).parent / "axe.min.js"
        if p.exists():
            return str(p), "axe-playwright-python"
    for p, label in ((pathlib.Path("node_modules/axe-core/axe.min.js"), "node_modules"),
                     (pathlib.Path.home() / "node_modules/axe-core/axe.min.js", "~/node_modules")):
        if p.exists():
            return str(p), label
    return None, None


def axe_version(src: str | None) -> str | None:
    m = re.search(r"axe v([\w.\-]+)", (src or "")[:400])
    return m.group(1) if m else None


def launch_browser(pw, channel: str | None = None, executable_path: str | None = None):
    """Try the launch ladder; returns (browser or None, description, [one-line errors])."""
    attempts = []
    if executable_path:
        attempts.append(("executable_path", dict(executable_path=executable_path)))
    if channel:
        attempts.append((f"channel={channel}", dict(channel=channel)))
    attempts += [("playwright chromium", {}), ("channel=chrome", dict(channel="chrome")),
                 ("channel=chromium", dict(channel="chromium")),
                 ("/usr/bin/google-chrome", dict(executable_path="/usr/bin/google-chrome")),
                 ("/usr/bin/chromium", dict(executable_path="/usr/bin/chromium"))]
    errors = []
    for desc, kw in attempts:
        try:
            return pw.chromium.launch(headless=True, args=["--no-sandbox"], **kw), desc, errors
        except Exception as e:  # noqa: BLE001
            errors.append(f"{desc}: {str(e).strip().splitlines()[0][:120] if str(e).strip() else type(e).__name__}")
    return None, None, errors


class Capturer:
    def __init__(self, out: str = "captures", channel: str | None = None, executable_path: str | None = None,
                 axe: str | None = None, storage_state: str | None = None, locale: str | None = None,
                 timezone: str | None = None, wait: str = "load", settle_ms: int = 500,
                 timeout_ms: int = 20000, tab_steps: int = 30, headers: dict | None = None, full_width: bool = False,
                 wait_for: str | None = None, base_origin: str | None = None, masks: list | None = None,
                 keep_query: bool = False, block_third_party: bool = False, allow_origins: list | None = None,
                 fresh: bool = False):
        self.full_width = full_width
        self.fresh = fresh  # False: merge into an existing <out>/manifest.json instead of replacing it
        self.out = pathlib.Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.channel, self.executable_path = channel, executable_path
        self.axe_path, self.axe_source = find_axe(axe)
        self.axe_src = pathlib.Path(self.axe_path).read_text(encoding="utf-8") if self.axe_path else None
        self.storage_state, self.locale, self.timezone = storage_state, locale, timezone
        self.wait, self.settle_ms, self.timeout_ms, self.tab_steps = wait, settle_ms, timeout_ms, tab_steps
        self.wait_for = wait_for
        self.headers = headers or {}
        self.base_origin = origin_of(base_origin) if base_origin else None
        self.masks = list(dict.fromkeys(DEFAULT_MASKS + list(masks or [])))
        self.keep_query, self.block_third_party = keep_query, block_third_party
        self.allow_origins = set()   # normalised origins, or bare hostnames (any scheme/port)
        for o in allow_origins or []:
            self.allow_origins.add(origin_of(o) if "://" in o else o.strip().lower().strip("/"))
        self.captures: list[dict] = []
        self._pw = None
        self.browser = None
        self.browser_desc = None
        self._contexts = []
        self._navigations = 0
        self._idle_fallback: set[str] = set()   # URLs that never went network-idle: use 'load' from then on

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
        browser, desc, errors = launch_browser(self._pw, self.channel, self.executable_path)
        if browser:
            self.browser_desc = desc
            print(f"[capture_web] browser: {desc}", file=sys.stderr)
            return browser
        sys.exit("[capture_web] no browser could be launched:\n  " + "\n  ".join(errors) +
                 "\nFallback: the desktop app's built-in browser (preview_start + resize_window) or Claude in Chrome; "
                 "otherwise list the sizes under Not checked.")

    # -- origins ---------------------------------------------------------------
    def clean_url(self, url: str) -> str:
        return clean_url(url, self.keep_query)

    def scrub(self, text) -> str:
        return scrub(text, self.keep_query)

    def is_app_url(self, url: str) -> bool:
        """True for the app's own origin, any localhost port, --allow-origin, and non-network URLs."""
        k = _origin_key(url)
        if k is None:
            return True
        if k[1] in LOCAL_HOSTS:
            return True
        o = origin_of(url)
        if self.base_origin and o == self.base_origin:
            return True
        return o in self.allow_origins or k[1] in self.allow_origins

    def _note_navigation(self, request):
        # Capturer reused without c.goto(): take the app origin from the first top-level navigation
        if self.base_origin is None:
            try:
                if request.is_navigation_request() and request.frame.parent_frame is None:
                    self.base_origin = origin_of(request.url)
            except PWError:
                pass

    # -- pages -----------------------------------------------------------------
    def new_page(self, width: int, height: int, scheme: str = "light", reduced_motion: bool = False):
        kw = dict(viewport={"width": width, "height": height}, color_scheme=scheme,
                  reduced_motion="reduce" if reduced_motion else "no-preference",
                  device_scale_factor=1)
        if self.storage_state:
            kw["storage_state"] = self.storage_state
        if self.locale:
            kw["locale"] = self.locale
        if self.timezone:
            kw["timezone_id"] = self.timezone
        ctx = self.browser.new_context(**{k: v for k, v in kw.items() if v is not None})
        self._contexts.append(ctx)
        log = {"console_errors": [], "page_errors": [], "failed_requests": [],
               "third_party": set(), "blocked": set(), "blocked_urls": set()}

        def on_request(r):
            self._note_navigation(r)
            if not self.is_app_url(r.url):
                log["third_party"].add(origin_of(r.url))

        def on_failed(r):
            if r.url in log["blocked_urls"]:
                return  # we aborted it ourselves (--block-third-party); listed under "blocked"
            log["failed_requests"].append({"url": self.clean_url(r.url)[:200], "error": self.scrub(r.failure or "")[:120]})

        if self.headers or self.block_third_party:
            def handle(route, request):
                self._note_navigation(request)
                url = request.url
                if not self.is_app_url(url):
                    if self.block_third_party:
                        log["blocked"].add(origin_of(url))
                        log["blocked_urls"].add(url)
                        route.abort("blockedbyclient")
                        return
                elif self.headers and self.base_origin and origin_of(url) == self.base_origin:
                    route.continue_(headers={**request.all_headers(), **self.headers})
                    return
                route.continue_()
            ctx.route("**/*", handle)

        page = ctx.new_page()
        page.set_default_timeout(self.timeout_ms)
        def on_console(m):
            if m.type != "error":
                return
            try:  # "Failed to load resource: ERR_BLOCKED_BY_CLIENT" for a request we aborted ourselves is not the app's error
                if (m.location or {}).get("url") in log["blocked_urls"]:
                    return
            except PWError:
                pass
            log["console_errors"].append(self.scrub(m.text)[:300])

        page.on("console", on_console)
        page.on("pageerror", lambda e: log["page_errors"].append(self.scrub(e)[:300]))
        page.on("request", on_request)
        page.on("websocket", lambda ws: log["third_party"].add(origin_of(ws.url)) if not self.is_app_url(ws.url) else None)
        page.on("requestfailed", on_failed)
        page.on("response", lambda r: log["failed_requests"].append({"url": self.clean_url(r.url)[:200], "status": r.status}) if r.status >= 400 else None)
        page._uiux_log = log  # type: ignore[attr-defined]
        page._uiux_meta = {"width": width, "height": height, "scheme": scheme, "reduced_motion": reduced_motion}  # type: ignore[attr-defined]
        page._uiux_wait_timed_out = False  # type: ignore[attr-defined]
        return page

    def goto(self, page, url: str) -> float | None:
        """Navigate and wait. Returns load ms, or None when the wait timed out (page._uiux_wait_timed_out).

        Exits the run if the very first navigation can't reach the app; raises NavigationFailed for any
        other navigation error so the caller can record it and carry on."""
        if self.base_origin is None:
            self.base_origin = origin_of(url)
        reset_log(page)  # errors from the previous page/state must not be reported against this one
        first = self._navigations == 0
        self._navigations += 1
        wait = "load" if (self.wait == "networkidle" and url in self._idle_fallback) else self.wait
        timed_out = False
        page._uiux_wait_timed_out = False  # type: ignore[attr-defined]
        t0 = time.time()
        try:
            page.goto(url, wait_until=wait, timeout=self.timeout_ms)
        except PWTimeout:
            timed_out = True
            if wait == "networkidle":
                self._idle_fallback.add(url)
                print(f"[capture_web] {self.clean_url(url)} never went network-idle (polling/websocket?); using 'load' "
                      f"for the rest of this route — use --wait load", file=sys.stderr)
            else:
                print(f"[capture_web] '{wait}' timed out for {self.clean_url(url)}; capturing what is there", file=sys.stderr)
            if wait != "load":
                try:
                    page.wait_for_load_state("load", timeout=self.timeout_ms)
                except PWError:
                    pass
        except PWError as e:
            msg = str(e).strip()
            line = self.scrub(msg.splitlines()[0] if msg else type(e).__name__)
            if first and any(code in msg for code in UNREACHABLE):
                sys.exit(f"[capture_web] cannot reach {self.clean_url(url)}: is the app running? ({line})")
            raise NavigationFailed(line[:200]) from None
        if self.wait_for:
            try:
                page.wait_for_selector(self.wait_for, timeout=self.timeout_ms)
            except PWError as e:
                timed_out = True
                print(f"[capture_web] --wait-for {self.wait_for!r} not found on {self.clean_url(url)} "
                      f"({self.scrub(str(e).strip().splitlines()[0] if str(e).strip() else 'timeout')[:120]}); capturing anyway",
                      file=sys.stderr)
        page._uiux_wait_timed_out = timed_out  # type: ignore[attr-defined]
        if self.settle_ms:
            page.wait_for_timeout(self.settle_ms)
        return None if timed_out else round((time.time() - t0) * 1000)

    # -- the capture itself ----------------------------------------------------
    def snap(self, page, route: str, state: str = "default", variant: str | None = None,
             run_axe: bool = True, tab_walk: bool | None = None, full_page: bool = True, load_ms: float | None = None) -> dict:
        meta = page._uiux_meta  # type: ignore[attr-defined]
        log = page._uiux_log  # type: ignore[attr-defined]
        wait_timed_out = bool(getattr(page, "_uiux_wait_timed_out", False))
        route = redact(route)  # a route may carry ?token=…; keep the query (it can name a state) but not the secret
        name = f"{slug(route)}__{meta['width']}__{meta['scheme']}"
        if state != "default":
            name += f"__{slug(state)}"
        if variant:
            name += f"__{variant}"
        file = self.out / f"{name}.png"
        entry = {"route": route, "state": state, "variant": variant, "file": file.name,  # relative to the manifest
                 "width": meta["width"], "height": meta["height"], "scheme": meta["scheme"],
                 "reduced_motion": meta["reduced_motion"], "url": self.clean_url(page.url),
                 "load_ms": None if wait_timed_out else load_ms, "wait_timed_out": wait_timed_out}
        masked, mask_locators = [], []
        for sel in self.masks:
            try:
                if page.locator(sel).count() > 0:
                    masked.append(sel)
                    mask_locators.append(page.locator(sel))
            except PWError as e:
                print(f"[capture_web] --mask {sel!r} ignored: {str(e).strip().splitlines()[0][:100]}", file=sys.stderr)
        entry["masked"] = masked
        mask_kw = {"mask": mask_locators} if mask_locators else {}
        try:
            if full_page and not self.full_width:
                # full height, but clipped to the viewport width: what the user actually sees (overflow is cut off,
                # which is the point). --full-width keeps Playwright's default of widening to the scroll width.
                sh = page.evaluate("Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)")
                page.screenshot(path=str(file), full_page=True,
                                clip={"x": 0, "y": 0, "width": meta["width"], "height": min(max(sh, meta["height"]), 8000)},
                                **mask_kw)
            else:
                page.screenshot(path=str(file), full_page=full_page, **mask_kw)
        except PWError as e:
            entry["error"] = f"screenshot: {self.scrub(e)[:200]}"
            self._add_log(entry, log)
            reset_log(page)
            self.captures.append(entry)
            return entry
        try:
            entry.update(page.evaluate(JS_PAGE_INFO))
            entry["overflow"] = page.evaluate(JS_OVERFLOW)
            entry["small_targets"] = page.evaluate(JS_SMALL_TARGETS, INTERACTIVE)
        except PWError as e:
            entry["error"] = f"evaluate: {self.scrub(e)[:200]}"
        self._add_log(entry, log)
        if run_axe:
            entry["axe"] = self.run_axe(page)
        if tab_walk is None:
            tab_walk = meta["scheme"] == "light" and not meta["reduced_motion"] and variant is None
        if tab_walk:
            entry["tab_walk"] = self.walk_tabs(page)
        reset_log(page)  # copied into the entry above; the next snap() on this page starts clean
        self.captures.append(entry)
        return entry

    def _add_log(self, entry: dict, log: dict):
        entry["console_errors"] = list(dict.fromkeys(log["console_errors"]))[:20]
        entry["page_errors"] = list(dict.fromkeys(log["page_errors"]))[:20]
        seen, failed = set(), []
        for r in log["failed_requests"]:
            key = (r.get("url"), r.get("status"), r.get("error"))
            if key not in seen:
                seen.add(key)
                failed.append(r)
        entry["failed_requests"] = failed[:30]
        entry["third_party"] = sorted(o for o in log["third_party"] if o)
        if self.block_third_party:
            entry["blocked"] = sorted(o for o in log["blocked"] if o)

    def run_axe(self, page) -> dict:
        if not self.axe_src:
            return {"skipped": "axe.min.js not found (pip install axe-playwright-python, or npm i axe-core)"}
        try:
            page.evaluate(self.axe_src)
            res = page.evaluate("async () => await axe.run(document, { resultTypes: ['violations'] })")
        except PWError as e:
            return {"error": self.scrub(e)[:200]}
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
            return {"error": self.scrub(e)[:200], "steps": steps}
        focusable = [s for s in steps if s.get("tag") != "body"]
        return {"steps": steps,
                "reached": len(focusable),
                "invisible_focus": [s["step"] for s in focusable if s.get("focus_visible") is False],
                "offscreen": [s["step"] for s in focusable if s.get("in_viewport") is False]}

    # -- manifest -------------------------------------------------------------
    def merged_captures(self, path: pathlib.Path) -> tuple[list, int]:
        """This run's captures merged into the existing manifest at path: (captures, earlier ones kept or replaced).

        Entries are keyed by "file" (a new capture of the same file replaces the old entry in place);
        entries without a file (navigation errors) by (route, width, scheme, variant, state), and an old
        file-less error is also dropped when this run captured that same route/size/scheme/variant/state."""
        if self.fresh or not path.exists():
            return list(self.captures), 0
        try:
            old = json.loads(path.read_text(encoding="utf-8")).get("captures") or []
            if not isinstance(old, list):
                raise ValueError("'captures' is not a list")
        except (OSError, ValueError, AttributeError) as e:
            bak = path.with_name("manifest.json.bak")
            try:
                path.replace(bak)
            except OSError:
                bak = None
            print(f"[capture_web] existing manifest unreadable ({str(e)[:120]}); starting a new one"
                  + (f" (old file kept as {bak})" if bak else ""), file=sys.stderr)
            return list(self.captures), 0

        def file_key(c):
            f = c.get("file")
            # older manifests stored cwd-relative or absolute paths: compare by file name in this folder
            return ("file", pathlib.PurePath(str(f).replace("\\", "/")).name) if f else None

        def combo(c):
            return ("combo", c.get("route"), c.get("width"), c.get("scheme"), c.get("variant"), c.get("state"))

        new_by_key = {}
        for c in self.captures:
            new_by_key[file_key(c) or combo(c)] = c
        new_combos = {combo(c) for c in self.captures}
        merged, used = [], set()
        for c in old:
            if not isinstance(c, dict):
                continue
            k = file_key(c) or combo(c)
            if k in new_by_key:
                if k not in used:
                    merged.append(new_by_key[k])
                    used.add(k)
                continue
            if k[0] == "combo" and k in new_combos:
                continue  # stale navigation error for something this run captured
            if k[0] == "file" and c["file"] != k[1] and (self.out / k[1]).exists():
                c = {**c, "file": k[1]}
            merged.append(c)
        merged += [c for k, c in new_by_key.items() if k not in used]
        return merged, len(old)

    def write_manifest(self):
        path = self.out / "manifest.json"
        cs, earlier = self.merged_captures(path)
        if earlier:
            print(f"[capture_web] merged with existing manifest ({earlier} earlier captures)", file=sys.stderr)
        axe = [c.get("axe") or {} for c in cs]
        summary = {
            "captures": len(cs),
            "with_overflow": sum(1 for c in cs if (c.get("overflow") or {}).get("overflow")),
            "with_console_errors": sum(1 for c in cs if c.get("console_errors") or c.get("page_errors")),
            "with_failed_requests": sum(1 for c in cs if c.get("failed_requests")),
            "axe_serious_or_critical": sum(a.get("counts", {}).get("critical", 0) + a.get("counts", {}).get("serious", 0) for a in axe),
            "axe_skipped": sum(1 for a in axe if "skipped" in a),
            "axe_errors": sum(1 for a in axe if "error" in a),
            "with_targets_below_24": sum(1 for c in cs if any(t.get("below_24") for t in c.get("small_targets") or [])),
            "with_invisible_focus": sum(1 for c in cs if (c.get("tab_walk") or {}).get("invisible_focus")),
            "tab_walk_errors": sum(1 for c in cs if "error" in (c.get("tab_walk") or {})),
            "with_wait_timeouts": sum(1 for c in cs if c.get("wait_timed_out")),
            "third_party_origins": len({o for c in cs for o in c.get("third_party") or []}),
            "with_blocked": sum(1 for c in cs if c.get("blocked")),
            "evaluate_errors": sum(1 for c in cs if str(c.get("error") or "").startswith("evaluate:")),
            "screenshot_errors": sum(1 for c in cs if str(c.get("error") or "").startswith("screenshot:")),
            "errors": sum(1 for c in cs if c.get("error")),
        }
        manifest = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), "browser": self.browser_desc,
                    "axe": self.axe_source, "axe_version": axe_version(self.axe_src),
                    "summary": summary, "captures": cs}
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        s = summary
        print(f"[capture_web] {s['captures']} captures → {self.out}/manifest.json  "
              f"overflow:{s['with_overflow']} console:{s['with_console_errors']} "
              f"failed-req:{s['with_failed_requests']} axe-serious+:{s['axe_serious_or_critical']} "
              f"targets<24:{s['with_targets_below_24']} invisible-focus:{s['with_invisible_focus']} "
              f"wait-timeouts:{s['with_wait_timeouts']} third-party-origins:{s['third_party_origins']} errors:{s['errors']}"
              + (f"  NOT RUN: axe skipped on {s['axe_skipped']}" if s["axe_skipped"] else "")
              + (f"  axe errors:{s['axe_errors']}" if s["axe_errors"] else "")
              + (f"  tab-walk errors:{s['tab_walk_errors']}" if s["tab_walk_errors"] else ""), file=sys.stderr)


def run_check(a) -> int:
    """Preflight: what this interpreter can capture with. Exit 0 if a browser launches, else 1."""
    print(f"python      {sys.version.split()[0]} ({sys.executable})")
    try:
        pv = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        pv = "?"
    print(f"playwright  {pv}")
    ok = False
    try:
        with sync_playwright() as pw:
            browser, desc, errors = launch_browser(pw, a.channel, a.executable_path)
            for e in errors:
                print(f"  tried     {e}")
            if browser:
                print(f"browser     {desc} (version {browser.version})")
                browser.close()
                ok = True
            else:
                print("browser     NONE could be launched (playwright install chromium, or --channel chrome / --executable-path)")
    except Exception as e:  # noqa: BLE001
        print(f"browser     NONE: {str(e).strip().splitlines()[0][:160] if str(e).strip() else type(e).__name__}")
    path, label = find_axe(a.axe)
    if path:
        try:
            ver = axe_version(pathlib.Path(path).read_text(encoding="utf-8")[:400])
        except OSError as e:
            ver = f"unreadable: {e}"
        print(f"axe         {label} (axe-core {ver or 'version unknown'})")
    else:
        print("axe         NOT FOUND: accessibility checks will be skipped (pip install axe-playwright-python, or npm i axe-core)")
    print(f"pillow      {'yes' if importlib.util.find_spec('PIL') else 'no (annotate.py needs it: pip install pillow)'}")
    print("result      " + ("ok" if ok else "no browser: web capture is not possible here"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="preflight: report python, playwright, browser, axe and Pillow, then exit")
    ap.add_argument("--url", help="base URL, e.g. http://localhost:5173 (required unless --check)")
    ap.add_argument("--routes", nargs="*", default=["/"], help="paths to capture (default: /)")
    ap.add_argument("--out", default="captures", help="output folder (default: %(default)s)")
    ap.add_argument("--fresh", action="store_true",
                    help="start a new manifest in --out instead of merging into an existing one")
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
    ap.add_argument("--header", action="append", default=[],
                    help="extra request header 'Name: value' (repeatable); sent only to the --url origin")
    ap.add_argument("--locale", help="e.g. en-IN")
    ap.add_argument("--timezone", help="e.g. Asia/Kolkata")
    ap.add_argument("--wait", default="load", choices=["load", "domcontentloaded", "networkidle"],
                    help="navigation wait (default load; networkidle only for static sites: it stalls on polling apps)")
    ap.add_argument("--wait-for", metavar="SELECTOR", help="after navigation, wait until this selector appears")
    ap.add_argument("--settle", type=int, default=500, help="ms to wait after load before capturing (default: %(default)s)")
    ap.add_argument("--timeout", type=int, default=20000, help="navigation timeout ms (default: %(default)s)")
    ap.add_argument("--tab-steps", type=int, default=30, help="max Tab presses in the focus walk (default: %(default)s)")
    ap.add_argument("--viewport-only", action="store_true", help="capture the viewport instead of the full page")
    ap.add_argument("--full-width", action="store_true", help="let full-page captures widen to the scroll width (shows the overflow instead of cutting it)")
    ap.add_argument("--mask", action="append", default=[], metavar="SELECTOR",
                    help="also black out these elements in screenshots (repeatable; password/card/[data-secret] are always masked)")
    ap.add_argument("--keep-query", action="store_true", help="keep query strings and fragments in manifest URLs (redacted)")
    ap.add_argument("--block-third-party", action="store_true",
                    help="abort requests to origins other than the app/localhost/--allow-origin (recorded as 'blocked')")
    ap.add_argument("--allow-origin", action="append", default=[], metavar="ORIGIN",
                    help="treat this origin (https://cdn.example.com) or host as part of the app (repeatable)")
    a = ap.parse_args(argv)

    if a.check:
        sys.exit(run_check(a))
    if not a.url:
        ap.error("--url is required (or use --check)")

    headers = {}
    for h in a.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()
    viewports = ["1440x900"] if a.quick else a.viewports
    schemes = ["light"] if a.quick else a.schemes
    base = a.url.rstrip("/")

    with Capturer(out=a.out, channel=a.channel, executable_path=a.executable_path, axe=a.axe,
                  storage_state=a.storage_state, locale=a.locale, timezone=a.timezone, wait=a.wait,
                  settle_ms=a.settle, timeout_ms=a.timeout, tab_steps=a.tab_steps, headers=headers,
                  full_width=a.full_width, wait_for=a.wait_for, base_origin=a.url, masks=a.mask,
                  keep_query=a.keep_query, block_third_party=a.block_third_party, allow_origins=a.allow_origin,
                  fresh=a.fresh) as c:

        def shoot(route, url, w, h, scheme, variant=None, reduced_motion=False, run_axe=True, full_page=True):
            page = c.new_page(w, h, scheme, reduced_motion=reduced_motion)
            try:
                try:
                    ms = c.goto(page, url)
                except NavigationFailed as nf:
                    e = {"route": redact(route), "state": "default", "variant": variant, "width": w, "height": h,
                         "scheme": scheme, "reduced_motion": reduced_motion, "url": c.clean_url(url),
                         "error": f"navigation: {nf}"}
                    c.captures.append(e)
                    print(f"  {route} {w}x{h} {scheme}{' ' + variant if variant else ''}  [{e['error']}]", file=sys.stderr)
                    return
                e = c.snap(page, route, variant=variant, run_axe=run_axe and not a.no_axe, full_page=full_page, load_ms=ms)
                if a.no_axe and run_axe:
                    e["axe"] = {"skipped": "--no-axe"}
                print(f"  {c.out / e['file']}" + (f"  [{e['error']}]" if e.get("error") else ""), file=sys.stderr)
            finally:
                try:
                    page.context.close()
                except PWError:
                    pass

        for route in a.routes:
            url = base + (route if route.startswith("/") else "/" + route)
            for vp in viewports:
                w, h = (int(x) for x in vp.lower().split("x"))
                for scheme in schemes:
                    shoot(route, url, w, h, scheme, full_page=not a.viewport_only)
            if not a.quick and not a.no_reduced_motion:
                shoot(route, url, 1440, 900, "light", variant="reduced-motion", reduced_motion=True, run_axe=False)
            if not a.quick and not a.no_zoom:
                # 1280 px window at 200% zoom = 640 CSS px (WCAG 1.4.4 resize text; 1.4.10 reflow needs 320 CSS px)
                shoot(route, url, 640, 720, "light", variant="zoom200", run_axe=False)


if __name__ == "__main__":
    main()
