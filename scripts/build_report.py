#!/usr/bin/env python3
"""Build report.html and findings.json from review.json (schema: references/review-schema.md).

  python build_report.py review.json                 # writes report.html + findings.json beside it
  python build_report.py review.json --inline        # embed images as data URIs (single-file page, < 16 MB)
  python build_report.py review.json --out docs/reviews/uiux/2026-11-02/report.html

Validates the review (IDs unique and not colliding with prior ones, priorities P0–P3, evidence tags,
required fields), warns about calibration (more than five P0/P1, quick wins that aren't S effort,
capture paths that don't exist), renders the page, and prints the image files the page references so
they can be passed to the Artifact tool's `files` map.
"""
from __future__ import annotations

import argparse
import base64
import collections
import html
import json
import mimetypes
import pathlib
import sys

PRIORITIES = ["P0", "P1", "P2", "P3"]
EVIDENCE = {"automated", "judged", "inferred"}
SCORES = {"good": ("Good", "ok"), "needs-work": ("Needs work", "warn"), "broken": ("Broken", "bad")}
REQUIRED = ["id", "title", "priority", "evidence", "where", "seen", "why", "do", "effort"]
PRIORITY_HELP = {"P0": "crash, data loss, money, or a state that misleads", "P1": "blocks or slows a main job",
                 "P2": "friction", "P3": "polish"}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def validate(review: dict, base: pathlib.Path) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    seen_ids = set()
    prior_ids = {p.get("id") for p in review.get("prior", [])}
    for f in review.get("findings", []):
        fid = f.get("id", "?")
        for k in REQUIRED:
            if k not in f or f[k] in (None, "", {}):
                errors.append(f"{fid}: missing '{k}'")
        if f.get("priority") not in PRIORITIES:
            errors.append(f"{fid}: priority must be one of {PRIORITIES}")
        if f.get("evidence") not in EVIDENCE:
            errors.append(f"{fid}: evidence must be one of {sorted(EVIDENCE)}")
        if f.get("effort") not in ("S", "M", "L"):
            errors.append(f"{fid}: effort must be S, M or L")
        if fid in seen_ids:
            errors.append(f"{fid}: duplicate id")
        if fid in prior_ids:
            errors.append(f"{fid}: id collides with a prior-review item; IDs are never reused")
        seen_ids.add(fid)
        if f.get("quick_win") and f.get("effort") != "S":
            warnings.append(f"{fid}: marked quick_win but effort is {f.get('effort')}")
        for sec in ("seen", "do"):
            for c in (f.get(sec) or {}).get("captures", []) or []:
                if not (base / c).exists():
                    warnings.append(f"{fid}: {sec} capture not found: {c}")
    top = sum(1 for f in review.get("findings", []) if f.get("priority") in ("P0", "P1"))
    if top > 5:
        warnings.append(f"{top} findings at P0/P1 — re-check calibration (type × reach) before publishing")
    for p in review.get("prior", []):
        if p.get("status") not in ("fixed", "open", "regressed"):
            errors.append(f"prior {p.get('id')}: status must be fixed, open or regressed")
    for dim, v in (review.get("scorecard") or {}).items():
        if v not in SCORES:
            errors.append(f"scorecard '{dim}': value must be good, needs-work or broken")
    if not review.get("verdict"):
        errors.append("verdict is missing")
    return errors, warnings


class Images:
    def __init__(self, base: pathlib.Path, inline: bool):
        self.base, self.inline, self.used = base, inline, []

    def src(self, rel: str) -> str:
        p = self.base / rel
        if rel not in self.used:
            self.used.append(rel)
        if self.inline and p.exists():
            mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return f"data:{mt};base64,{base64.b64encode(p.read_bytes()).decode()}"
        return rel

    def tag(self, rel: str, alt: str = "", cls: str = "") -> str:
        p = self.base / rel
        missing = "" if p.exists() else " missing"
        if rel.endswith(".txt"):
            body = esc(p.read_text()) if p.exists() else f"(missing: {esc(rel)})"
            return f'<pre class="tui{missing}" title="{esc(rel)}">{body}</pre>'
        return (f'<a href="{esc(self.src(rel))}" target="_blank" rel="noopener"><img class="{cls}{missing}" loading="lazy" '
                f'src="{esc(self.src(rel))}" alt="{esc(alt or rel)}" title="{esc(rel)}"></a>')


CSS = """
:root{--bg:#fbfbfc;--fg:#1b1f24;--muted:#5c6470;--card:#fff;--line:#e3e6ea;--accent:#2563eb;
 --p0:#b91c1c;--p1:#c2410c;--p2:#a16207;--p3:#4b5563;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--ok-bg:#dcfce7;--warn-bg:#fef3c7;--bad-bg:#fee2e2;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1115;--fg:#e6e8ec;--muted:#9aa3ad;--card:#171a20;--line:#2a2f38;--accent:#60a5fa;
 --p0:#f87171;--p1:#fb923c;--p2:#facc15;--p3:#9ca3af;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--ok-bg:#052e16;--warn-bg:#3b2a05;--bad-bg:#3f0d0d;--chip:#222733}}
:root[data-theme=dark]{--bg:#0f1115;--fg:#e6e8ec;--muted:#9aa3ad;--card:#171a20;--line:#2a2f38;--accent:#60a5fa;
 --p0:#f87171;--p1:#fb923c;--p2:#facc15;--p3:#9ca3af;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--ok-bg:#052e16;--warn-bg:#3b2a05;--bad-bg:#3f0d0d;--chip:#222733}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.7rem;margin:0 0 4px}h2{font-size:1.25rem;margin:36px 0 12px;padding-top:12px;border-top:1px solid var(--line)}h3{font-size:1.05rem;margin:0 0 6px}
.meta{color:var(--muted);font-size:.9rem}.verdict{font-size:1.05rem;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-top:16px}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:var(--chip);border-radius:999px;padding:3px 10px;font-size:.85rem}
.chip.ok{background:var(--ok-bg);color:var(--ok)}.chip.warn{background:var(--warn-bg);color:var(--warn)}.chip.bad{background:var(--bad-bg);color:var(--bad)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.finding{border-left:4px solid var(--p2)}.finding.P0{border-left-color:var(--p0)}.finding.P1{border-left-color:var(--p1)}.finding.P3{border-left-color:var(--p3)}
.fhead{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}.fid{font-family:ui-monospace,Menlo,monospace;color:var(--muted)}
.pri{font-weight:700}.pri.P0{color:var(--p0)}.pri.P1{color:var(--p1)}.pri.P2{color:var(--p2)}.pri.P3{color:var(--p3)}
.badge{font-size:.75rem;border:1px solid var(--line);border-radius:4px;padding:1px 6px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
.badge.automated{color:var(--ok)}.badge.inferred{color:var(--warn)}
.sec{margin-top:10px}.sec b{display:block;color:var(--muted);font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px}
.shots{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}.shots img{max-width:min(100%,420px);max-height:360px;border:1px solid var(--line);border-radius:6px;background:#fff}
img.missing{outline:2px dashed var(--bad)}
.sheet{display:grid;gap:10px}.sheet .row{display:grid;grid-template-columns:140px repeat(auto-fit,minmax(120px,1fr));gap:10px;align-items:start}
.sheet .row>div:first-child{font-family:ui-monospace,Menlo,monospace;font-size:.85rem;color:var(--muted);word-break:break-all;padding-top:6px}
.sheet img{width:100%;max-height:220px;object-fit:cover;object-position:top;border:1px solid var(--line);border-radius:6px;background:#fff}
.sheet .lbl{font-size:.75rem;color:var(--muted)}
.tw{overflow-x:auto;max-width:100%}table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--chip)}
td.ok{background:var(--ok-bg);color:var(--ok)}td.nc{color:var(--muted);font-style:italic}td.f a{font-family:ui-monospace,Menlo,monospace;color:var(--accent)}
pre.tui{background:#0b0d12;color:#dde3ea;padding:10px;border-radius:6px;font:12px/1.25 ui-monospace,Menlo,monospace;overflow:auto;max-height:420px}
ul{padding-left:20px}a{color:var(--accent)}details summary{cursor:pointer;color:var(--muted)}
.two{display:grid;grid-template-columns:1fr;gap:12px}@media(min-width:760px){.two{grid-template-columns:1fr 1fr}}
.nav{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);font-size:.9rem;display:flex;gap:14px;flex-wrap:wrap;z-index:1}
"""


def render(review: dict, base: pathlib.Path, images: Images) -> str:
    findings = sorted(review.get("findings", []), key=lambda f: PRIORITIES.index(f.get("priority", "P3")))
    by_pri = collections.Counter(f["priority"] for f in findings)
    quick = [f for f in findings if f.get("quick_win")]
    surface = review.get("surface", [])
    surface = [surface] if isinstance(surface, str) else surface
    out = []
    A = out.append

    A(f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
      f"<title>{esc(review.get('project', 'UI/UX review'))} UX review</title><style>{CSS}</style></head><body><main>")
    A("<nav class='nav'><a href='#verdict'>Verdict</a><a href='#screens'>Screens</a><a href='#scorecard'>Scorecard</a>"
      "<a href='#quick'>Quick wins</a><a href='#findings'>Findings</a><a href='#jobs'>Jobs</a><a href='#keep'>Keep</a>"
      "<a href='#prior'>Prior</a><a href='#notchecked'>Not checked</a></nav>")
    A(f"<h1>UI/UX review — {esc(review.get('project', ''))}</h1>")
    A(f"<div class='meta'>{esc(review.get('date', ''))} · {esc(', '.join(surface))} · depth: {esc(review.get('depth', ''))} · "
      f"{len(findings)} findings ({', '.join(f'{p} {by_pri[p]}' for p in PRIORITIES if by_pri[p]) or 'none'})</div>")

    # verdict
    A("<section id='verdict'><div class='verdict'>" + esc(review.get("verdict", "")) + "</div>")
    users = review.get("users") or []
    if users or review.get("assumptions"):
        A("<div class='two'>")
        if users:
            A("<div class='card'><h3>Who and where</h3><ul>" + "".join(
                f"<li><b>{esc(u.get('name'))}</b> — {esc(u.get('expertise', ''))}. {esc(u.get('context', ''))}</li>" for u in users) + "</ul></div>")
        if review.get("assumptions"):
            A("<div class='card'><h3>Assumptions</h3><ul>" + "".join(f"<li>{esc(a)}</li>" for a in review["assumptions"]) + "</ul></div>")
        A("</div>")
    sc = review.get("scope") or {}
    if sc:
        A("<div class='meta'>Run: " + esc(sc.get("run_command", "—")) + " · Jobs in scope: " + esc("; ".join(sc.get("jobs", []))) +
          (" · Prior review: " + esc(sc["prior_review"]) if sc.get("prior_review") else "") + "</div>")
    A("</section>")

    # screens overview from manifest
    A("<section id='screens'><h2>Screens overview</h2>")
    man_path = review.get("captures_manifest")
    manifest = None
    if man_path and (base / man_path).exists():
        try:
            manifest = json.loads((base / man_path).read_text())
        except json.JSONDecodeError as e:
            A(f"<p class='meta'>manifest unreadable: {esc(e)}</p>")
    if manifest:
        s = manifest.get("summary") or {}
        A("<div class='chips'>" + "".join(
            f"<span class='chip {'ok' if not v else 'warn'}'>{esc(k.replace('_', ' '))}: {esc(v)}</span>"
            for k, v in s.items() if k != "captures") + f"<span class='chip'>{esc(s.get('captures', len(manifest.get('captures', []))))} captures</span></div>")
        caps = manifest.get("captures", [])
        man_dir = (pathlib.Path(man_path).parent)

        def rel(c):  # manifest file paths are relative to where capture ran; normalise to the review dir
            f = c.get("file") or c.get("svg") or c.get("txt") or ""
            p = pathlib.Path(f)
            if (base / p).exists():
                return str(p)
            cand = man_dir / p.name
            return str(cand) if (base / cand).exists() else str(p)

        web = [c for c in caps if "width" in c]
        tui = [c for c in caps if "cols" in c]
        if web:
            widths = sorted({c["width"] for c in web if not c.get("variant")})
            routes = list(dict.fromkeys(c["route"] for c in web))
            for scheme in ("light", "dark"):
                rows = [c for c in web if c.get("scheme") == scheme and not c.get("variant") and c.get("state", "default") == "default"]
                if not rows:
                    continue
                A(f"<details {'open' if scheme == 'light' else ''}><summary>{esc(scheme)} scheme — {len(rows)} captures</summary><div class='sheet'>")
                A("<div class='row'><div></div>" + "".join(f"<div class='lbl'>{w}px</div>" for w in widths) + "</div>")
                for r in routes:
                    cells = []
                    for w in widths:
                        c = next((x for x in rows if x["route"] == r and x["width"] == w), None)
                        if not c:
                            cells.append("<div class='lbl'>—</div>")
                            continue
                        flags = []
                        if (c.get("overflow") or {}).get("overflow"):
                            flags.append("overflow")
                        if c.get("console_errors") or c.get("page_errors"):
                            flags.append("console")
                        if c.get("failed_requests"):
                            flags.append("req")
                        ax = (c.get("axe") or {}).get("counts") or {}
                        if ax.get("critical") or ax.get("serious"):
                            flags.append(f"axe {ax.get('critical', 0) + ax.get('serious', 0)}")
                        if (c.get("tab_walk") or {}).get("invisible_focus"):
                            flags.append("focus")
                        cells.append("<div>" + images.tag(rel(c), f"{r} at {w}px {scheme}") +
                                     (f"<div class='lbl'>{esc(', '.join(flags))}</div>" if flags else "") + "</div>")
                    A(f"<div class='row'><div>{esc(r)}</div>" + "".join(cells) + "</div>")
                A("</div></details>")
            variants = [c for c in web if c.get("variant") or c.get("state", "default") != "default"]
            if variants:
                A(f"<details><summary>states and variants — {len(variants)} captures</summary><div class='shots'>")
                for c in variants:
                    A("<div>" + images.tag(rel(c), c.get("route", "")) + f"<div class='lbl'>{esc(c.get('route'))} · {esc(c.get('state'))} · {esc(c.get('variant') or '')} · {esc(c.get('width'))}px</div></div>")
                A("</div></details>")
        if tui:
            A(f"<details open><summary>terminal — {len(tui)} snapshots</summary><div class='shots'>")
            for c in tui:
                f = c.get("svg") or c.get("png") or c.get("txt")
                A("<div>" + (images.tag(rel({'file': f}), f) if f else "") +
                  f"<div class='lbl'>{esc(c.get('cols'))}×{esc(c.get('rows'))} {esc(c.get('state') or '')}" +
                  (f" · {c['clipped_lines']} clipped lines" if c.get("clipped_lines") else "") + "</div></div>")
            A("</div></details>")
    else:
        A("<p class='meta'>No captures manifest given (set <code>captures_manifest</code> in review.json to build the contact sheet).</p>")
    A("</section>")

    # scorecard
    A("<section id='scorecard'><h2>Scorecard</h2><div class='chips'>")
    for dim, v in (review.get("scorecard") or {}).items():
        label, cls = SCORES.get(v, (v, ""))
        A(f"<span class='chip {cls}'>{esc(dim)}: {esc(label)}</span>")
    A("</div><p class='meta'>Coarse on purpose — for the trend across reviews, not for scoring.</p></section>")

    # quick wins
    A("<section id='quick'><h2>Quick wins</h2>")
    if quick:
        A("<ul>" + "".join(f"<li><a href='#{esc(f['id'])}'>{esc(f['id'])}</a> <span class='pri {esc(f['priority'])}'>{esc(f['priority'])}</span> {esc(f['title'])} <span class='meta'>· {esc(f['where'])}</span></li>" for f in quick) + "</ul>")
    else:
        A("<p class='meta'>None marked. Any S-effort P1/P2 item belongs here.</p>")
    A("</section>")

    # findings
    A("<section id='findings'><h2>Findings</h2><p class='meta'>" + " · ".join(f"<b>{p}</b> {h}" for p, h in PRIORITY_HELP.items()) + "</p>")
    for f in findings:
        p = f.get("priority", "P3")
        A(f"<article class='card finding {esc(p)}' id='{esc(f['id'])}'><div class='fhead'><span class='pri {esc(p)}'>{esc(p)}</span>"
          f"<span class='fid'>{esc(f['id'])}</span><h3 style='margin:0'>{esc(f['title'])}</h3>"
          f"<span class='badge {esc(f.get('evidence'))}'>{esc(f.get('evidence'))}</span>"
          + (f"<span class='badge'>{esc(f['dimension'])}</span>" if f.get("dimension") else "")
          + (f"<span class='badge'>{esc(f['tag'])}</span>" if f.get("tag") else "")
          + f"<span class='badge'>effort {esc(f.get('effort'))}</span>"
          + ("<span class='badge'>quick win</span>" if f.get("quick_win") else "")
          + (f"<span class='badge'>see {esc(', '.join(f['related']))}</span>" if f.get("related") else "") + "</div>")
        A(f"<div class='sec'><b>Where</b>{esc(f.get('where'))}</div>")
        seen = f.get("seen") or {}
        A("<div class='sec'><b>Seen</b>" + esc(seen.get("text", "")) + "<div class='shots'>" + "".join(images.tag(c, f"{f['id']} seen") for c in seen.get("captures", []) or []) + "</div></div>")
        if f.get("repro"):
            A("<div class='sec'><b>Repro</b><ol>" + "".join(f"<li>{esc(s)}</li>" for s in f["repro"]) + "</ol></div>")
        A(f"<div class='sec'><b>Why it matters</b>{esc(f.get('why'))}" + (f" <span class='meta'>(reach: {esc(f['reach'])})</span>" if f.get("reach") else "") + "</div>")
        do = f.get("do") or {}
        if isinstance(do, str):
            do = {"text": do}
        A("<div class='sec'><b>Do</b>" + esc(do.get("text", "")) + "<div class='shots'>" + "".join(images.tag(c, f"{f['id']} proposal") for c in do.get("captures", []) or []) + "</div>"
          + ("<div class='meta'>Images under Do are proposals, not the current product.</div>" if do.get("captures") else "") + "</div></article>")
    if not findings:
        A("<p class='meta'>No findings recorded.</p>")
    A("</section>")

    # jobs + matrices
    A("<section id='jobs'><h2>Jobs and state matrix</h2>")
    for j in review.get("jobs") or []:
        A(f"<div class='card'><h3>{esc(j.get('name'))}</h3><div class='meta'>Steps: {esc(j.get('steps', '?'))} (minimum {esc(j.get('min_steps', '?'))}) · "
          f"done without docs: {esc(j.get('without_docs', '?'))}</div>")
        m = j.get("matrix") or {}
        if m.get("states"):
            sizes = m.get("sizes", [])
            A("<div class='tw'><table><tr><th>State</th>" + "".join(f"<th>{esc(s)}</th>" for s in sizes) + "</tr>")
            for state, cells in m["states"].items():
                row = f"<tr><td>{esc(state)}</td>"
                for c in cells:
                    c = str(c)
                    if c.lower() == "ok":
                        row += "<td class='ok'>ok</td>"
                    elif c.upper().startswith("UX-"):
                        row += "<td class='f'>" + ", ".join(f"<a href='#{esc(x.strip())}'>{esc(x.strip())}</a>" for x in c.split(",")) + "</td>"
                    elif c.lower() in ("n/a", "na", "-"):
                        row += "<td class='nc'>n/a</td>"
                    else:
                        row += f"<td class='nc'>{esc(c)}</td>"
                A(row + "</tr>")
            A("</table></div>")
        A("</div>")
    if not review.get("jobs"):
        A("<p class='meta'>No jobs recorded.</p>")
    A("</section>")

    # keep / prior / not checked
    A("<section id='keep'><h2>Keep</h2>" + ("<ul>" + "".join(f"<li>{esc(k)}</li>" for k in review.get("keep", [])) + "</ul>" if review.get("keep") else "<p class='meta'>—</p>") + "</section>")
    A("<section id='prior'><h2>Prior reviews</h2>")
    prior = review.get("prior") or []
    if prior:
        A("<div class='tw'><table><tr><th>ID</th><th>Title</th><th>Status</th><th>Evidence</th></tr>")
        for p in prior:
            st = p.get("status")
            cls = {"fixed": "ok", "open": "nc", "regressed": "f"}.get(st, "")
            A(f"<tr><td class='fid'>{esc(p.get('id'))}</td><td>{esc(p.get('title', ''))}</td><td class='{cls}'>{esc(st)}</td><td><div class='shots'>"
              + "".join(images.tag(c, f"{p.get('id')} {st}") for c in p.get("captures", []) or []) + "</div></td></tr>")
        A("</table></div>")
    else:
        A("<p class='meta'>First review, or no prior findings.json given.</p>")
    A("</section>")
    A("<section id='notchecked'><h2>Not checked</h2>")
    nc = review.get("not_checked") or []
    A("<ul>" + "".join(f"<li><b>{esc(n.get('what'))}</b> — {esc(n.get('why', ''))}</li>" if isinstance(n, dict) else f"<li>{esc(n)}</li>" for n in nc) + "</ul>" if nc else "<p class='meta'>Everything in scope was checked.</p>")
    A("</section>")
    A(f"<p class='meta' style='margin-top:40px'>Built from review.json by the uiux-review skill · {esc(review.get('date', ''))}</p></main></body></html>")
    return "".join(out)


def write_findings(review: dict, report_name: str, path: pathlib.Path):
    ids = [int(f["id"].split("-")[-1]) for f in review.get("findings", []) if f.get("id", "").split("-")[-1].isdigit()]
    ids += [int(p["id"].split("-")[-1]) for p in review.get("prior", []) if p.get("id", "").split("-")[-1].isdigit()]
    ledger = {
        "project": review.get("project"), "date": review.get("date"), "report": report_name,
        "next_id": (max(ids) + 1) if ids else 1, "scorecard": review.get("scorecard", {}),
        "findings": [{"id": f["id"], "title": f["title"], "priority": f["priority"], "dimension": f.get("dimension"),
                      "evidence": f.get("evidence"), "where": f.get("where"), "effort": f.get("effort"),
                      "quick_win": bool(f.get("quick_win")), "status": "open",
                      "captures": (f.get("seen") or {}).get("captures", [])} for f in review.get("findings", [])],
        "prior": [{"id": p.get("id"), "title": p.get("title"), "status": p.get("status")} for p in review.get("prior", [])],
    }
    path.write_text(json.dumps(ledger, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review", help="path to review.json")
    ap.add_argument("--out", help="report path (default: report.html beside review.json)")
    ap.add_argument("--findings-out", help="ledger path (default: findings.json beside review.json)")
    ap.add_argument("--inline", action="store_true", help="embed images as data URIs")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    a = ap.parse_args(argv)

    rpath = pathlib.Path(a.review)
    base = rpath.parent
    review = json.loads(rpath.read_text())
    errors, warnings = validate(review, base)
    for w in warnings:
        print(f"[build_report] warning: {w}", file=sys.stderr)
    for e in errors:
        print(f"[build_report] error: {e}", file=sys.stderr)
    if errors or (a.strict and warnings):
        sys.exit(1)

    images = Images(base, a.inline)
    html_out = render(review, base, images)
    out = pathlib.Path(a.out) if a.out else base / "report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_out)
    fpath = pathlib.Path(a.findings_out) if a.findings_out else base / "findings.json"
    write_findings(review, out.name, fpath)
    size_mb = out.stat().st_size / 1e6
    print(f"[build_report] {out} ({size_mb:.1f} MB) and {fpath}", file=sys.stderr)
    if size_mb > 16:
        print("[build_report] warning: page over 16 MB — publish without --inline and pass images as files", file=sys.stderr)
    if not a.inline and images.used:
        print("[build_report] files to publish alongside the page (Artifact `files` map, keys as listed):", file=sys.stderr)
        for u in images.used:
            print(f"  {u}", file=sys.stderr)


if __name__ == "__main__":
    main()
