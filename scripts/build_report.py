#!/usr/bin/env python3
"""Build report.html, findings.json and fixes.md from review.json (schema: references/review-schema.md).

  python build_report.py review.json                 # writes report.html, findings.json, fixes.md beside it
  python build_report.py review.json --prior ../2026-10-12/findings.json
  python build_report.py review.json --inline        # embed images as data URIs (single-file page, < 16 MB)
  python build_report.py review.json --out docs/reviews/uiux/2026-11-02/report.html

Inputs
  review.json          plain JSON (no // comments), UTF-8. Findings need id, title, priority, evidence,
                       where, seen, why, do, verify, effort; optional level, instances[], do.mockups[].
  captures_manifest    one path or a list (capture_web.py / capture_tui.py manifests) -> contact sheets.
  cli_summary          one path or a list (run_cli summary.json) -> "CLI probes" tables.
  prior ledger         --prior PATH, else scope.prior_review (relative to review.json); either one missing
                       is an error (set prior_review to null for a first review).
                       Old-format (findings[]/prior[]) and schema-2 (items[]) ledgers are both accepted.

Outputs
  report.html          the page to publish; image paths are relative to the report's folder.
  findings.json        schema 2: the cumulative register of every ID ever issued (items[] with full
                       finding bodies, status, first_seen, last_seen, history[]) and next_id.
  fixes.md             hand-off list of every open / regressed / partly / not-retested item.

Checks before anything is written
  errors    required fields, enums (priority, evidence, level, scorecard, prior status), ID reuse and
            ID below the prior next_id, prior open items not given a status, fixed/partly/regressed
            without a capture, wontfix/obsolete/not-retested without a note, scope.prior_review set
            but not found, prior[] entries with no prior ledger loaded, matrix cells that are empty,
            "ok" without ":<evidence>" (ok, ok:, ok - fine), or n/a / not checked without a reason.
  warnings  calibration (> 5 P0/P1), quick_win on non-S effort, missing captures/manifests, scorecard
            better than the open findings suggest, scorecard dimensions missing or given twice, unknown
            dimensions, matrix row length, free-text matrix cells, dead matrix IDs, a matrix that is
            mostly "ok" with little distinct evidence, regressed P0/P1 not in the verdict, "regressed"
            on an item that was never fixed, a Playwright storage-state file inside the review folder.
  sensitive bearer/JWT/Stripe-style/GitHub/GitLab/Anthropic/OpenAI/Slack/Google/AWS keys, private keys,
            secret=… parameters and real-looking emails in review.json, manifests, .txt captures and
            CLI probe output -> exit 1 unless --allow-sensitive.
  quick wins  one definition for report and fixes.md: every still-open item (new or carried over) with
            effort S at P0–P2, unless quick_win is false (true forces it in).
  --strict  turns warnings into a failure (exit 1, nothing written).

It prints the files the page references so they can be passed to the Artifact tool's `files` map.
"""
from __future__ import annotations

import argparse
import base64
import collections
import copy
import datetime
import html
import json
import mimetypes
import os
import pathlib
import re
import sys

PRIORITIES = ["P0", "P1", "P2", "P3"]
EVIDENCE = {"automated", "judged", "inferred"}
LEVELS = ["token", "component", "pattern", "screen", "copy", "flow"]
SCORES = {"good": ("Good", "ok"), "needs-work": ("Needs work", "warn"), "broken": ("Broken", "bad"),
          "n/a": ("n/a", "neutral"), "not-checked": ("Not checked", "neutral")}
SCORE_RANK = {"good": 0, "needs-work": 1, "broken": 2}
REQUIRED = ["id", "title", "priority", "evidence", "where", "seen", "why", "do", "verify", "effort"]
PRIORITY_HELP = {"P0": "crash, data loss, money, or a state that misleads", "P1": "blocks or slows a main job",
                 "P2": "friction", "P3": "polish"}
STATUSES = ["fixed", "partly", "open", "regressed", "wontfix", "obsolete", "not-retested"]
NEEDS_CAPTURE = {"fixed", "partly", "regressed"}
NEEDS_NOTE = {"wontfix", "obsolete", "not-retested"}
STILL_OPEN = {"open", "regressed", "partly", "not-retested"}       # must be re-statused next time
COUNTS_AGAINST_SCORE = {"open", "regressed", "partly"}
KNOWN_DIMENSIONS = ["Crashes and dead ends", "Information architecture", "Navigation and keys", "Layout and fit",
                    "Visual hierarchy and colour", "States and feedback", "Copy and meaning",
                    "Money, data and destructive actions", "Forms and input", "Touch and small screens",
                    "Onboarding", "Accessibility baseline", "Performance feel", "CLI ergonomics"]
ITEM_KEYS = ["id", "title", "priority", "dimension", "level", "tag", "evidence", "where", "instances", "seen",
             "repro", "why", "reach", "do", "verify", "effort", "quick_win", "related"]
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".bmp"}
UX_RE = re.compile(r"UX-\d+", re.I)


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def die(msg: str):
    print(f"[build_report] {msg}", file=sys.stderr)
    sys.exit(1)


def canon_dim(s) -> str:
    return re.sub(r"\s+", " ", str(s).replace("&", " and ")).strip().lower()


KNOWN_CANON = {canon_dim(d): d for d in KNOWN_DIMENSIONS}


def id_num(i) -> int | None:
    m = re.search(r"(\d+)$", str(i or ""))
    return int(m.group(1)) if m else None


def id_sort_key(i):
    n = id_num(i)
    return (n if n is not None else 10 ** 9, str(i))


def as_list(x) -> list:
    if x is None or x == "":
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def load_json_file(path: pathlib.Path, what: str):
    """Read UTF-8 JSON or exit with a one-line error."""
    if not path.exists():
        die(f"error: {what} not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        die(f"error: {path.name} is not UTF-8 text ({e.reason} at byte {e.start})")
    except OSError as e:
        die(f"error: cannot read {path}: {e.strerror}")
    try:
        return json.loads(text), text
    except json.JSONDecodeError as e:
        die(f"error: {path.name} is not valid JSON (line {e.lineno} col {e.colno}): {e.msg} — plain JSON only, no // comments")


def is_quick_win(f: dict) -> bool:
    """quick_win true/false wins; otherwise S effort at P0–P2. Status is not considered — see quick_wins()."""
    if f.get("quick_win") is not None:
        return bool(f.get("quick_win"))
    return f.get("effort") == "S" and f.get("priority") in ("P0", "P1", "P2")


def quick_wins(ledger: dict) -> list:
    """The one definition used by the report and fixes.md: every merged-ledger item that is still open
    (open / regressed / partly / not-retested; new findings are open) and is_quick_win()."""
    qs = [it for it in ledger.get("items", []) if it.get("status") in STILL_OPEN and is_quick_win(it)]
    return sorted(qs, key=lambda it: (PRIORITIES.index(it["priority"]) if it.get("priority") in PRIORITIES else 9,
                                      id_sort_key(it.get("id"))))


# ---------------------------------------------------------------- normalise

def _norm_section(f: dict, key: str, label: str, errors: list):
    v = f.get(key)
    if isinstance(v, str):
        f[key] = {"text": v}
    elif v is not None and not isinstance(v, dict):
        errors.append(f"{label}: '{key}' must be an object or a string")
        f[key] = {"text": str(v)}
    sec = f.get(key)
    if isinstance(sec, dict):
        for k in ("captures", "mockups"):
            if k in sec:
                sec[k] = [str(c) for c in as_list(sec[k])]


def normalise(review: dict) -> list:
    """Make the review safe to walk (in place). Returns structural errors. Idempotent."""
    errors = []
    for k in ("findings", "prior", "jobs", "keep", "not_checked", "users", "assumptions"):
        v = review.get(k)
        if v is None:
            review[k] = []
        elif not isinstance(v, list):
            errors.append(f"'{k}' must be a list")
            review[k] = []
    for k in ("scorecard", "scope"):
        if review.get(k) is None:
            review[k] = {}
        elif not isinstance(review[k], dict):
            errors.append(f"'{k}' must be an object")
            review[k] = {}
    fs = []
    for n, f in enumerate(review["findings"], 1):
        if not isinstance(f, dict):
            errors.append(f"findings[{n}] must be an object")
            continue
        if "id" in f and not isinstance(f["id"], str):
            errors.append(f"findings[{n}]: id must be a string like \"UX-012\" (got {json.dumps(f['id'])})")
            f["id"] = str(f["id"])
            review["_nonstring_id"] = True  # validate() skips follow-on matrix-ID checks
        label = f.get("id") or f"findings[{n}]"
        _norm_section(f, "seen", label, errors)
        _norm_section(f, "do", label, errors)
        for k in ("repro", "related"):
            if k in f and not isinstance(f[k], list):
                f[k] = as_list(f[k])
        if "instances" in f:
            inst = []
            for i in as_list(f["instances"]):
                if isinstance(i, str):
                    inst.append({"where": i})
                elif isinstance(i, dict) and i.get("where"):
                    if "capture" in i and i["capture"] is not None:
                        i["capture"] = str(i["capture"])
                    inst.append(i)
                else:
                    errors.append(f"{label}: each instance needs a 'where' (string or {{\"where\", \"capture\"}})")
            f["instances"] = inst
        fs.append(f)
    review["findings"] = fs
    ps = []
    for n, p in enumerate(review["prior"], 1):
        if not isinstance(p, dict):
            errors.append(f"prior[{n}] must be an object")
            continue
        if "id" in p and not isinstance(p["id"], str):
            errors.append(f"prior[{n}]: id must be a string (got {json.dumps(p['id'])})")
            p["id"] = str(p["id"])
            review["_nonstring_id"] = True
        if "captures" in p:
            p["captures"] = [str(c) for c in as_list(p["captures"])]
        ps.append(p)
    review["prior"] = ps
    js = []
    for n, j in enumerate(review["jobs"], 1):
        if not isinstance(j, dict):
            errors.append(f"jobs[{n}] must be an object")
            continue
        if j.get("matrix") is not None and not isinstance(j["matrix"], dict):
            errors.append(f"job '{j.get('name')}': matrix must be an object")
            j["matrix"] = {}
        js.append(j)
    review["jobs"] = js
    if isinstance(review.get("surface"), str):
        review["surface"] = [review["surface"]]
    return errors


# ---------------------------------------------------------------- paths

def resolve(rel, base: pathlib.Path, extra_dirs=()) -> pathlib.Path:
    """Find a capture path: absolute, relative to the review folder, or relative to a manifest folder."""
    p = pathlib.Path(str(rel))
    if p.is_absolute():
        return p
    cands = [base / p]
    for d in extra_dirs:
        cands += [d / p, d / p.name]
    for c in cands:
        if c.exists():
            return c
    return base / p


def relpath(target: pathlib.Path, start: pathlib.Path) -> str:
    try:
        return os.path.relpath(str(target), str(start)).replace(os.sep, "/")
    except ValueError:  # different drives on Windows
        return target.as_posix()


def rebase_item(item: dict, fn):
    """Apply fn to every capture path inside a ledger item."""
    for sec in ("seen", "do"):
        s = item.get(sec)
        if isinstance(s, dict):
            for k in ("captures", "mockups"):
                if isinstance(s.get(k), list):
                    s[k] = [fn(c) for c in s[k]]
    for i in item.get("instances") or []:
        if isinstance(i, dict) and i.get("capture"):
            i["capture"] = fn(i["capture"])
    for h in item.get("history") or []:
        if isinstance(h, dict) and isinstance(h.get("captures"), list):
            h["captures"] = [fn(c) for c in h["captures"]]


def rebaser(src_dir: pathlib.Path, dst_dir: pathlib.Path):
    def fn(c):
        c = str(c)
        if pathlib.Path(c).is_absolute():
            return c
        return relpath(src_dir / c, dst_dir)
    return fn


def manifest_paths(review: dict, key: str) -> list:
    return [str(m) for m in as_list(review.get(key)) if m]


# ---------------------------------------------------------------- prior ledger

def load_prior(path: pathlib.Path, base: pathlib.Path) -> tuple:
    """Return (ledger, warnings). ledger = {"next_id", "items": [...], "path"}; paths rebased to base."""
    data, _ = load_json_file(path, "prior ledger")
    warns = []
    if not isinstance(data, dict):
        die(f"error: prior ledger {path} must be a JSON object")
    items = []
    if isinstance(data.get("items"), list):
        for it in data["items"]:
            if isinstance(it, dict) and isinstance(it.get("id"), str):
                it = copy.deepcopy(it)
                it.setdefault("status", "open")
                items.append(it)
            else:
                warns.append(f"prior ledger {path.name}: skipped an item without a string id")
    else:  # old format: findings[] (all open) + prior[] (minimal)
        d = data.get("date")
        seen_ids = set()
        for f in as_list(data.get("findings")):
            if not (isinstance(f, dict) and isinstance(f.get("id"), str)):
                continue
            it = copy.deepcopy(f)
            caps = it.pop("captures", None)
            if caps and not it.get("seen"):
                it["seen"] = {"captures": as_list(caps)}
            it["status"] = it.get("status") or "open"
            it.setdefault("first_seen", d)
            it.setdefault("last_seen", d)
            it.setdefault("history", [{"date": d, "status": it["status"]}])
            items.append(it)
            seen_ids.add(it["id"])
        for p in as_list(data.get("prior")):
            if isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"] not in seen_ids:
                it = {"id": p["id"], "title": p.get("title"), "status": p.get("status") or "open", "last_seen": d}
                items.append({k: v for k, v in it.items() if v is not None})
                seen_ids.add(p["id"])
    fn = rebaser(path.parent, base)
    for it in items:
        rebase_item(it, fn)
    try:
        nid = int(data.get("next_id") or 0)
    except (TypeError, ValueError):
        nid = 0
    return {"next_id": nid, "items": items, "path": path, "schema": data.get("schema", 1)}, warns


def prior_by_id(prior_ledger) -> dict:
    return {it["id"]: it for it in (prior_ledger or {}).get("items", [])}


# ---------------------------------------------------------------- matrix cells

CELL_GRAMMAR = "ok:<evidence>, UX-###, n/a:<reason> or not checked:<reason>"


def parse_cell(c) -> tuple:
    """-> (kind, payload): ok/evidence ("" = ok: with no evidence), badok/text (starts with "ok" but is not
    ok:<evidence>), na/reason, unchecked/reason, ux/[ids], empty, other/text."""
    s = str(c if c is not None else "").strip()
    if not s:
        return ("empty", "")
    m = re.match(r"ok:(.*)$", s, re.I | re.S)
    if m:
        return ("ok", m.group(1).strip())
    if s[:2].lower() == "ok":
        return ("badok", s)
    m = re.match(r"(n/a|na|not[ _-]?checked)\b\s*:?\s*(.*)$", s, re.I | re.S)
    if m:
        kind = "na" if m.group(1).lower() in ("n/a", "na") else "unchecked"
        return (kind, m.group(2).strip())
    ids = UX_RE.findall(s)
    if ids:
        return ("ux", [i.upper() for i in ids])
    return ("other", s)


def matrix_rows(m: dict) -> list:
    states = m.get("states") or {}
    if not isinstance(states, dict):
        return []
    return [(str(state), as_list(cells)) for state, cells in states.items()]


# ---------------------------------------------------------------- validation

def open_findings_by_dim(review: dict, prior_ledger) -> dict:
    """Canonical dimension -> list of (id, priority) still open after this review."""
    out = collections.defaultdict(list)
    for f in review.get("findings", []):
        if f.get("dimension") and f.get("priority") in PRIORITIES:
            out[canon_dim(f["dimension"])].append((f.get("id"), f["priority"]))
    ledger = prior_by_id(prior_ledger)
    stat = {p.get("id"): p.get("status") for p in review.get("prior", [])}
    for pid, it in ledger.items():
        st = stat.get(pid, it.get("status"))
        if st in COUNTS_AGAINST_SCORE and it.get("dimension") and it.get("priority") in PRIORITIES:
            out[canon_dim(it["dimension"])].append((pid, it["priority"]))
    for p in review.get("prior", []):  # minimal items that carry their own dimension
        if p.get("id") not in ledger and p.get("status") in COUNTS_AGAINST_SCORE and p.get("dimension") \
                and p.get("priority") in PRIORITIES:
            out[canon_dim(p["dimension"])].append((p.get("id"), p["priority"]))
    return out


def suggest_score(items: list):
    """-> (score, worst (id, priority)) or (None, None) when there is nothing open."""
    if not items:
        return None, None
    worst = sorted(items, key=lambda t: PRIORITIES.index(t[1]))[0]
    if worst[1] in ("P0", "P1"):
        return "broken", worst
    if worst[1] == "P2":
        return "needs-work", worst
    return "good", worst


def validate(review: dict, base: pathlib.Path, prior_ledger=None) -> tuple:
    errors = normalise(review)
    warnings = []
    date = review.get("date")
    ledger = prior_by_id(prior_ledger)
    prior_entries = {p.get("id"): p for p in review.get("prior", []) if p.get("id")}
    prior_next = (prior_ledger or {}).get("next_id") or 0

    def check_cap(fid, what, c, dirs=()):
        if not resolve(c, base, dirs).exists():
            warnings.append(f"{fid}: {what} not found: {c}")

    seen_ids = set()
    for n, f in enumerate(review.get("findings", []), 1):
        fid = f.get("id") or f"findings[{n}]"
        for k in REQUIRED:
            if k not in f or f[k] in (None, "", {}, []):
                errors.append(f"{fid}: missing '{k}'" + (" — the check that proves it is fixed" if k == "verify" else ""))
        if "verify" in f and f["verify"] not in (None, "") and not isinstance(f["verify"], str):
            errors.append(f"{fid}: 'verify' must be a string")
        if f.get("priority") not in PRIORITIES:
            errors.append(f"{fid}: priority must be one of {PRIORITIES}")
        if f.get("evidence") not in EVIDENCE:
            errors.append(f"{fid}: evidence must be one of {sorted(EVIDENCE)}")
        if f.get("effort") not in ("S", "M", "L"):
            errors.append(f"{fid}: effort must be S, M or L")
        if "level" not in f or f.get("level") in (None, ""):
            warnings.append(f"{fid}: no level: say how deep the fix goes ({' | '.join(LEVELS)})")
        elif f["level"] not in LEVELS:
            errors.append(f"{fid}: level must be one of {LEVELS}")
        if f.get("dimension") and canon_dim(f["dimension"]) not in KNOWN_CANON:
            warnings.append(f"{fid}: dimension '{f['dimension']}' is not one of the Dimensions in SKILL.md")
        if f.get("id") and id_num(fid) is None:
            warnings.append(f"{fid}: id should end in a number, like UX-012, so the ledger can keep the sequence")
        if fid in seen_ids:
            errors.append(f"{fid}: duplicate id")
        if fid in prior_entries or fid in ledger:
            errors.append(f"{fid}: id collides with a prior-review item; IDs are never reused")
        elif prior_ledger is not None and id_num(fid) is not None and id_num(fid) < prior_next:
            errors.append(f"{fid}: new id is below the prior ledger's next_id ({prior_next}); IDs are never reused")
        seen_ids.add(fid)
        if f.get("quick_win") is True and f.get("effort") != "S":
            warnings.append(f"{fid}: marked quick_win but effort is {f.get('effort')}")
        for sec in ("seen", "do"):
            s = f.get(sec) if isinstance(f.get(sec), dict) else {}
            for k in ("captures", "mockups"):
                for c in s.get(k) or []:
                    check_cap(fid, f"{sec} {k[:-1]}", c)
        for i in f.get("instances") or []:
            if i.get("capture"):
                check_cap(fid, "instance capture", i["capture"])
    top = sum(1 for f in review.get("findings", []) if f.get("priority") in ("P0", "P1"))
    if top > 5:
        warnings.append(f"{top} findings at P0/P1 — re-check each against type × reach; if they still stand, keep them "
                        "and say in the verdict the product isn't ready")

    # prior statuses
    pseen = set()
    for n, p in enumerate(review.get("prior", []), 1):
        pid = p.get("id")
        if not pid:
            errors.append(f"prior[{n}]: missing 'id'")
            continue
        if pid in pseen:
            errors.append(f"prior {pid}: listed twice")
        pseen.add(pid)
        st = p.get("status")
        if st not in STATUSES:
            errors.append(f"prior {pid}: status must be one of {' | '.join(STATUSES)}")
        elif st in NEEDS_CAPTURE and not p.get("captures"):
            errors.append(f"prior {pid}: status '{st}' needs a fresh capture in 'captures'")
        elif st in NEEDS_NOTE and not str(p.get("note") or "").strip():
            errors.append(f"prior {pid}: status '{st}' needs a 'note' saying why")
        for c in p.get("captures") or []:
            check_cap(f"prior {pid}", "capture", c)
        prev = (ledger.get(pid) or {}).get("status")
        if st == "regressed" and pid in ledger and prev not in ("fixed", "partly", "regressed"):
            warnings.append(f"{pid} was {prev}, not fixed — 'regressed' means it was fixed before and broke again; use open")
        if prior_ledger is not None and pid not in ledger:
            warnings.append(f"prior {pid}: not in the prior ledger {prior_ledger['path'].name} — accepted as a new minimal item")
    if prior_ledger is not None:
        for pid, it in sorted(ledger.items(), key=lambda kv: id_sort_key(kv[0])):
            if it.get("status") in STILL_OPEN and pid not in prior_entries:
                errors.append(f"{pid} is {it.get('status')} in the prior ledger but missing from prior[] — give it a status")

    # regressions must be loud
    verdict = str(review.get("verdict") or "")
    for p in review.get("prior", []):
        if p.get("status") == "regressed":
            pri = (ledger.get(p.get("id")) or {}).get("priority") or p.get("priority")
            if pri in ("P0", "P1") and str(p.get("id")).lower() not in verdict.lower():
                warnings.append(f"regressed {pri} {p.get('id')} is not named in the verdict — name it there")

    # scorecard
    by_dim = open_findings_by_dim(review, prior_ledger)
    scored = {}
    for dim, v in review.get("scorecard", {}).items():
        cd = canon_dim(dim)
        if cd in scored:
            warnings.append(f"duplicate scorecard key: '{scored[cd]}' and '{dim}' are the same dimension — keep one")
        scored.setdefault(cd, dim)
        if v not in SCORES:
            errors.append(f"scorecard '{dim}': value must be one of {' | '.join(SCORES)}")
            continue
        if cd not in KNOWN_CANON:
            warnings.append(f"scorecard '{dim}': unknown dimension — use the names in SKILL.md Dimensions")
        sug, worst = suggest_score(by_dim.get(cd, []))
        if sug is None:
            continue
        if v in ("n/a", "not-checked"):
            if sug != "good" or by_dim.get(cd):
                warnings.append(f"scorecard '{dim}' is {v} but {worst[0]} ({worst[1]}) is open there → {sug}")
        elif SCORE_RANK[v] < SCORE_RANK[sug]:
            warnings.append(f"scorecard '{dim}' is {v} but {worst[0]} ({worst[1]}) is open there → {sug}")
    if review.get("scorecard"):
        for name in KNOWN_DIMENSIONS:
            cd = canon_dim(name)
            if cd not in scored:
                sug, worst = suggest_score(by_dim.get(cd, []))
                warnings.append(f"scorecard has no value for '{name}' — give one, or n/a / not-checked"
                                + (f" ({worst[0]} {worst[1]} is open there → {sug})" if sug in ("broken", "needs-work") else ""))
        for cd, items in by_dim.items():
            sug, worst = suggest_score(items)
            if cd not in scored and cd not in KNOWN_CANON and sug in ("broken", "needs-work"):
                warnings.append(f"scorecard has no '{cd}' but {worst[0]} ({worst[1]}) is open there → {sug}")
    else:
        warnings.append(f"no scorecard — give each of the {len(KNOWN_DIMENSIONS)} dimensions a value "
                        "(good | needs-work | broken | n/a | not-checked)")

    # matrices
    known_ids = {i.upper() for i in list(seen_ids) + list(prior_entries) + list(ledger)}
    for j in review.get("jobs", []):
        m = j.get("matrix") or {}
        sizes = as_list(m.get("sizes"))
        jname = j.get("name") or "?"
        ok_cells, ok_refs, total = 0, set(), 0
        for state, cells in matrix_rows(m):
            if sizes and len(cells) not in (len(sizes), 1):
                warnings.append(f"matrix '{jname}' / '{state}': {len(cells)} cells for {len(sizes)} sizes "
                                f"(give one per size, or one cell for 'any size')")
            for c in cells:
                total += 1
                kind, val = parse_cell(c)
                where = f"matrix '{jname}' / '{state}'"
                if kind == "ok":
                    if not val:
                        errors.append(f"{where}: 'ok' needs evidence — ok:<capture or step>")
                    else:
                        ok_cells += 1
                        ok_refs.add(val)
                elif kind == "badok":
                    errors.append(f"{where}: cell {json.dumps(val, ensure_ascii=False)} is not ok:<evidence> — "
                                  "write ok:<capture or step> (or a UX-### if it is a problem)")
                elif kind == "ux":
                    if review.get("_nonstring_id"):
                        continue  # the id error is enough; stringified ids would make every reference look dead
                    for i in val:
                        if i not in known_ids:
                            warnings.append(f"{where}: {i} matches no finding or prior item")
                elif kind in ("na", "unchecked") and not val:
                    errors.append(f"{where}: {json.dumps(str(c).strip(), ensure_ascii=False)} needs a reason — "
                                  f"{'n/a' if kind == 'na' else 'not checked'}:<reason>")
                elif kind == "empty":
                    errors.append(f"{where}: empty cell — every cell needs {CELL_GRAMMAR}")
                elif kind == "other":
                    warnings.append(f"{where}: cell {json.dumps(str(c), ensure_ascii=False)} has no UX ID and no "
                                    f"ok:/n/a:/not checked: prefix — use {CELL_GRAMMAR}")
        if total and ok_cells > 0.8 * total and len(ok_refs) < ok_cells / 2:
            warnings.append(f"{jname}: most cells are ok with little distinct evidence — check you actually tested them")

    # manifests / cli summaries
    for key in ("captures_manifest", "cli_summary"):
        for mp in manifest_paths(review, key):
            if not resolve(mp, base).exists():
                warnings.append(f"{key} not found: {mp}")
    if not review.get("verdict"):
        errors.append("verdict is missing")
    if not date:
        warnings.append("no 'date' — using today's date in the ledger")
    return errors, warnings


# ---------------------------------------------------------------- sensitive content

EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
SENSITIVE = [
    ("bearer token", re.compile(r"Bearer\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{8,}")),
    ("JWT", re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}")),
    ("API key", re.compile(r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}")),
    # (?<![A-Za-z0-9]) keeps "risk-…" / "desk-…" out; the OpenAI form also needs a digit so hyphenated
    # prose such as "sk-learn-compatible-preprocessing" is not a key.
    ("GitHub token", re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("GitLab token", re.compile(r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}")),
    ("Anthropic API key", re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"(?<![A-Za-z0-9])sk-(?!ant-)(?=(?:proj-)?[A-Za-z_-]*\d)(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("secret parameter", re.compile(r"(token|api[_-]?key|secret|password|passwd|sig|signature|auth)=([^&\s\"'\]]{4,})", re.I)),
]
SAFE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
SAFE_EMAIL_TLDS = {"test", "example", "invalid", "local", "localhost"}
FILE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "js", "mjs", "css", "json", "txt", "html", "htm",
             "map", "woff", "woff2", "ttf", "ico"}  # logo@2x.png is not an email


def mask(s: str) -> str:
    return s[:4] + "*" * min(max(len(s) - 4, 0), 12)


def email_is_safe(local: str, domain: str) -> bool:
    d = domain.lower()
    if d == "localhost" or any(d == s or d.endswith("." + s) for s in SAFE_EMAIL_DOMAINS):
        return True
    tld = d.rsplit(".", 1)[-1]
    if tld in SAFE_EMAIL_TLDS or tld in FILE_TLDS:
        return True
    lo = local.lower()
    return lo == "git" or lo.startswith(("noreply", "no-reply"))


def scan_text(text: str, label: str) -> list:
    hits = []
    for ln, line in enumerate(text.splitlines(), 1):
        for name, rx in SENSITIVE:
            for m in rx.finditer(line):
                if name == "secret parameter":
                    val = m.group(2)
                    if val.lower().lstrip("[<").startswith("redacted") or set(val) <= set("*x•."):
                        continue
                    snippet = f"{m.group(1)}={mask(val)}"
                else:
                    snippet = mask(m.group(0))
                hits.append(f"{label}:{ln}: {name}: {snippet}")
        for m in EMAIL_RE.finditer(line):
            if not email_is_safe(m.group(1), m.group(2)):
                hits.append(f"{label}:{ln}: email address: {mask(m.group(0))}")
    return hits


def scan_files(review_text: str, rpath: pathlib.Path, review: dict, base: pathlib.Path) -> tuple:
    """-> (hits, warnings). Scans review.json, manifests, referenced .txt captures and CLI probe output."""
    hits = scan_text(review_text, str(rpath))
    warns = []
    files = []
    txt = []
    for f in review.get("findings", []):
        for sec in ("seen", "do"):
            s = f.get(sec) if isinstance(f.get(sec), dict) else {}
            txt += [c for k in ("captures", "mockups") for c in s.get(k) or []]
        txt += [i.get("capture") for i in f.get("instances") or [] if i.get("capture")]
    for p in review.get("prior", []):
        txt += p.get("captures") or []
    txt_paths = [resolve(c, base) for c in txt if str(c).lower().endswith(".txt")]
    for mp in manifest_paths(review, "captures_manifest"):
        p = resolve(mp, base)
        if p.exists():
            files.append(p)
            try:
                man = json.loads(p.read_text(encoding="utf-8"))
                for c in as_list(man.get("captures") if isinstance(man, dict) else None):
                    if isinstance(c, dict) and c.get("txt"):
                        txt_paths.append(resolve(c["txt"], base, [p.parent]))
            except (ValueError, OSError):
                pass
    for sp in manifest_paths(review, "cli_summary"):
        p = resolve(sp, base)
        if p.exists():
            files.append(p)
            txt_paths += sorted(p.parent.glob("*.txt"))
    seen = set()
    for p in files + txt_paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen or not p.exists():
            continue
        seen.add(key)
        try:
            hits += scan_text(p.read_text(encoding="utf-8", errors="replace"), str(p))
        except OSError:
            pass
    # Playwright storage state inside the review folder
    skip = {".git", "node_modules", "venv", ".venv", "site-packages", "__pycache__"}
    for root, dirs, fnames in os.walk(base):
        depth = len(pathlib.Path(root).relative_to(base).parts)
        dirs[:] = [] if depth >= 2 else [d for d in dirs if d not in skip and not d.startswith(".")]
        for fn in fnames:
            if not fn.endswith(".json"):
                continue
            fp = pathlib.Path(root) / fn
            try:
                if fp.stat().st_size > 20_000_000:
                    continue
                d = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except (ValueError, OSError):
                continue
            if isinstance(d, dict) and "cookies" in d and "origins" in d:
                warns.append(f"{fp} looks like a Playwright storage state — keep storage-state files outside the review folder")
    return hits, warns


# ---------------------------------------------------------------- images

class Images:
    def __init__(self, base: pathlib.Path, out_dir: pathlib.Path, inline: bool):
        self.base, self.out_dir, self.inline = base, out_dir, inline
        self.used = []      # hrefs of images referenced (non-inline)
        self.linked = []    # hrefs of other files linked (txt, html) — published even with --inline
        self._data = {}

    def href(self, p: pathlib.Path) -> str:
        return relpath(p, self.out_dir)

    def _note(self, lst, h, p):
        if p.exists() and h not in lst:
            lst.append(h)

    def data_uri(self, p: pathlib.Path) -> str:
        k = str(p)
        if k not in self._data:
            mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            self._data[k] = f"data:{mt};base64,{base64.b64encode(p.read_bytes()).decode()}"
        return self._data[k]

    def link(self, rel, text=None, dirs=()) -> str:
        p = resolve(rel, self.base, dirs)
        h = self.href(p)
        self._note(self.linked, h, p)
        cls = "" if p.exists() else " class='missing'"
        return f"<a{cls} href='{esc(h)}' target='_blank' rel='noopener'>{esc(text or rel)}</a>"

    def tag(self, rel, alt: str = "", cls: str = "", dirs=()) -> str:
        p = resolve(rel, self.base, dirs)
        missing = "" if p.exists() else " missing"
        rel_s = str(rel)
        ext = p.suffix.lower()
        if ext == ".txt":
            body = esc(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else f"(missing: {esc(rel_s)})"
            return f"<pre class='tui{missing}' title='{esc(rel_s)}'>{body}</pre>"
        if ext not in IMAGE_EXT:
            return "<div class='filelink'>" + self.link(rel, f"open {p.name}", dirs) + "</div>"
        if ext == ".svg" and p.exists():
            try:
                with p.open("rb") as fh:
                    if b"rich-terminal" in fh.read(512):  # Textual/Rich terminal export: show it wider
                        cls = f"{cls} termcap".strip()
            except OSError:
                pass
        c = f"cap {cls}{missing}".strip()
        if self.inline and p.exists():
            return f"<img class='{esc(c)}' loading='lazy' src='{self.data_uri(p)}' alt='{esc(alt or rel_s)}' title='{esc(rel_s)}'>"
        h = self.href(p)
        self._note(self.used, h, p)
        return (f"<a class='capl{' termcap' if 'termcap' in c else ''}' href='{esc(h)}' target='_blank' rel='noopener'><img class='{esc(c)}' loading='lazy' "
                f"src='{esc(h)}' alt='{esc(alt or rel_s)}' title='{esc(rel_s)}'></a>")


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
h4{font-size:.95rem;margin:16px 0 6px;color:var(--muted)}
.meta{color:var(--muted);font-size:.9rem}.verdict{font-size:1.05rem;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-top:16px}
.callout{border-radius:10px;padding:12px 16px;margin-top:12px}.callout.bad{background:var(--bad-bg);color:var(--bad);border:1px solid var(--bad)}
.callout ul{margin:4px 0 0}.callout a{color:inherit;font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}.chip{background:var(--chip);border-radius:999px;padding:3px 10px;font-size:.85rem}
.chip.ok{background:var(--ok-bg);color:var(--ok)}.chip.warn{background:var(--warn-bg);color:var(--warn)}.chip.bad{background:var(--bad-bg);color:var(--bad)}
.chip.neutral{background:var(--chip);color:var(--muted)}.chip .sug{opacity:.8;font-size:.8em}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.finding{border-left:4px solid var(--p2)}.finding.P0{border-left-color:var(--p0)}.finding.P1{border-left-color:var(--p1)}.finding.P3{border-left-color:var(--p3)}
.fhead{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}.fid{font-family:ui-monospace,Menlo,monospace;color:var(--muted)}
.pri{font-weight:700}.pri.P0{color:var(--p0)}.pri.P1{color:var(--p1)}.pri.P2{color:var(--p2)}.pri.P3{color:var(--p3)}
.badge{font-size:.75rem;border:1px solid var(--line);border-radius:4px;padding:1px 6px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
.badge.automated{color:var(--ok)}.badge.inferred{color:var(--warn)}.badge.level{color:var(--accent);border-color:var(--accent)}
.sec{margin-top:10px}.sec>b{display:block;color:var(--muted);font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px}
.sec ul{margin:4px 0}.verify{border-left:3px solid var(--ok);padding-left:8px}
.shots{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;align-items:flex-start}
.shots>*{flex:0 1 420px;min-width:0}.shots>.termcap{flex-basis:800px}img.cap.termcap{max-width:800px}.shots>pre.tui{flex:1 1 100%}
img.cap{display:block;width:100%;max-width:420px;height:auto;border:1px solid var(--line);border-radius:6px;background:#fff}
a.capl{display:block}img.missing,a.missing{outline:2px dashed var(--bad)}
#prior td.fid{white-space:nowrap}#prior .shots>*{flex-basis:240px}#prior img.cap{max-width:240px}
.proposal{margin-top:6px}.proposal .lbl{color:var(--warn);font-weight:600}
.sheetwrap{overflow-x:auto;max-width:100%}
.sheet{display:grid;gap:10px}.sheet .row{display:grid;gap:10px;align-items:start}
.sheet .row>div{min-width:0}
.sheet .row>div:first-child{font-family:ui-monospace,Menlo,monospace;font-size:.85rem;color:var(--muted);word-break:break-all;padding-top:6px}
.sheet img.cap{max-width:none;max-height:220px;object-fit:cover;object-position:top}
.lbl{font-size:.75rem;color:var(--muted)}.flag{color:var(--warn)}
.term{display:grid;grid-template-columns:1fr;gap:14px}.term img.cap,.term img.cap.termcap{max-width:min(100%,1000px)}
.tw{overflow-x:auto;max-width:100%}table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--chip)}
td.ok{background:var(--ok-bg);color:var(--ok)}td.ok a{color:inherit}td.unchecked,td.na{color:var(--muted);font-style:italic}td.f a{font-family:ui-monospace,Menlo,monospace;color:var(--accent)}
td.bad{background:var(--bad-bg);color:var(--bad)}td.warn{background:var(--warn-bg);color:var(--warn)}
.why{font-size:.8rem}.any{display:inline-block;margin-left:6px;font-size:.7rem;color:var(--muted);font-style:normal;border:1px solid var(--line);border-radius:4px;padding:0 4px}
pre.tui{background:#0b0d12;color:#dde3ea;padding:10px;border-radius:6px;font:12px/1.25 ui-monospace,Menlo,monospace;overflow:auto;max-height:420px;margin:0}
code,.mono{font-family:ui-monospace,Menlo,monospace;font-size:.85em}
ul{padding-left:20px}a{color:var(--accent)}details summary{cursor:pointer;color:var(--muted)}
.two{display:grid;grid-template-columns:1fr;gap:12px}@media(min-width:760px){.two{grid-template-columns:1fr 1fr}}
.nav{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);font-size:.9rem;display:flex;gap:14px;flex-wrap:wrap;z-index:1}
"""

NEUTRAL_SUMMARY = {"third_party_origins", "captures", "generated_at"}


def summary_chips(s: dict, n_caps: int) -> str:
    chips = []
    axe_skipped = s.get("axe_skipped") or 0
    for k, v in s.items():
        if k in ("captures", "axe_skipped"):
            continue
        label = k.replace("_", " ")
        if k == "axe_serious_or_critical" and axe_skipped:
            if v:
                chips.append(f"<span class='chip warn'>{esc(label)}: {esc(v)}</span>")
            continue
        if isinstance(v, list):
            title = ", ".join(str(x) for x in v)
            chips.append(f"<span class='chip neutral' title='{esc(title)}'>{esc(label)}: {len(v)}</span>")
            continue
        if k in NEUTRAL_SUMMARY or isinstance(v, (str, dict)) or v is None:
            chips.append(f"<span class='chip neutral'>{esc(label)}: {esc(v)}</span>")
            continue
        chips.append(f"<span class='chip {'warn' if v else 'ok'}'>{esc(label)}: {esc(v)}</span>")
    if axe_skipped:
        chips.insert(0, f"<span class='chip warn'>axe: not run on {esc(axe_skipped)} captures</span>")
    chips.append(f"<span class='chip'>{esc(s.get('captures', n_caps))} captures</span>")
    return "<div class='chips'>" + "".join(chips) + "</div>"


def capture_flags(c: dict) -> list:
    flags = []
    if (c.get("overflow") or {}).get("overflow"):
        flags.append("overflow")
    if c.get("console_errors") or c.get("page_errors"):
        flags.append("console")
    if c.get("failed_requests"):
        flags.append("req")
    ax = c.get("axe") or {}
    cnt = ax.get("counts") or {}
    if cnt.get("critical") or cnt.get("serious"):
        flags.append(f"axe {(cnt.get('critical') or 0) + (cnt.get('serious') or 0)}")
    if ax.get("skipped"):
        flags.append("axe not run")
    if (c.get("tab_walk") or {}).get("invisible_focus"):
        flags.append("focus")
    if c.get("wait_timed_out"):
        flags.append("wait timeout")
    if c.get("error"):
        flags.append("error")
    if c.get("blocked"):
        flags.append("blocked")
    return flags


def render_manifest(A, mp: str, base: pathlib.Path, images: Images, show_name: bool):
    p = resolve(mp, base)
    if show_name:
        A(f"<h3 class='mono'>{esc(mp)}</h3>")
    if not p.exists():
        A(f"<p class='meta'>Manifest not found: {esc(mp)}</p>")
        return
    try:
        manifest = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        A(f"<p class='meta'>manifest unreadable: {esc(e)}</p>")
        return
    if not isinstance(manifest, dict):
        A("<p class='meta'>manifest is not a JSON object</p>")
        return
    caps = [c for c in as_list(manifest.get("captures")) if isinstance(c, dict)]
    A(summary_chips(manifest.get("summary") or {}, len(caps)))
    mdirs = [p.parent]

    def cap_file(c):
        return c.get("file") or c.get("svg") or c.get("png") or c.get("txt") or ""

    web = [c for c in caps if "width" in c]
    tui = [c for c in caps if "cols" in c]
    if web:
        widths = sorted({c["width"] for c in web if not c.get("variant") and isinstance(c.get("width"), (int, float))})
        routes = list(dict.fromkeys(str(c.get("route")) for c in web))
        cols = f"140px repeat({max(len(widths), 1)}, minmax(120px,1fr))"
        for scheme in ("light", "dark"):
            rows = [c for c in web if c.get("scheme") == scheme and not c.get("variant") and (c.get("state") or "default") == "default"]
            if not rows:
                continue
            A(f"<details {'open' if scheme == 'light' else ''}><summary>{esc(scheme)} scheme — {len(rows)} captures</summary>"
              "<div class='sheetwrap'><div class='sheet'>")
            A(f"<div class='row' style='grid-template-columns:{cols}'><div></div>" + "".join(f"<div class='lbl'>{esc(w)}px</div>" for w in widths) + "</div>")
            for r in routes:
                cells = []
                for w in widths:
                    c = next((x for x in rows if str(x.get("route")) == r and x.get("width") == w), None)
                    if not c:
                        cells.append("<div class='lbl'>—</div>")
                        continue
                    flags = capture_flags(c)
                    f = cap_file(c)
                    cells.append("<div>" + (images.tag(f, f"{r} at {w}px {scheme}", dirs=mdirs) if f else "<div class='lbl'>no file</div>") +
                                 (f"<div class='lbl flag'>{esc(', '.join(flags))}</div>" if flags else "") + "</div>")
                A(f"<div class='row' style='grid-template-columns:{cols}'><div>{esc(r)}</div>" + "".join(cells) + "</div>")
            A("</div></div></details>")
        variants = [c for c in web if c.get("variant") or (c.get("state") or "default") != "default"]
        if variants:
            A(f"<details><summary>states and variants — {len(variants)} captures</summary><div class='shots'>")
            for c in variants:
                flags = capture_flags(c)
                f = cap_file(c)
                A("<div>" + (images.tag(f, str(c.get("route", "")), dirs=mdirs) if f else "") +
                  f"<div class='lbl'>{esc(c.get('route'))} · {esc(c.get('state'))} · {esc(c.get('variant') or '')} · {esc(c.get('width'))}px</div>"
                  + (f"<div class='lbl flag'>{esc(', '.join(flags))}</div>" if flags else "") + "</div>")
            A("</div></details>")
    if tui:
        A(f"<details open><summary>terminal — {len(tui)} snapshots</summary><div class='term'>")
        for c in tui:
            f = c.get("svg") or c.get("png") or c.get("txt")
            notes = []
            if c.get("edge_lines"):
                notes.append(f"{c['edge_lines']} lines touch the right edge")
            if c.get("ellipsis_lines"):
                notes.append(f"{c['ellipsis_lines']} lines end in an ellipsis")
            ww = c.get("wide_widgets")
            nww = len(ww) if isinstance(ww, list) else ww
            if nww:
                notes.append(f"{nww} widgets wider than view")
            if c.get("clipped_lines"):
                notes.append(f"{c['clipped_lines']} clipped lines")
            if c.get("error"):
                notes.append(f"error: {c['error']}")
            if c.get("drew_anything") is False:
                notes.append("drew nothing")
            A("<div>" + f"<div class='lbl'>{esc(c.get('cols'))}×{esc(c.get('rows'))} {esc(c.get('state') or '')}"
              + (f" · {esc(c.get('mode'))}" if c.get("mode") else "") + "</div>"
              + (images.tag(f, f"{c.get('cols')}x{c.get('rows')} {c.get('state') or ''}", dirs=mdirs) if f else "")
              + (f"<div class='lbl flag'>{esc(' · '.join(notes))}</div>" if notes else "")
              + (f"<div class='lbl'>text: {images.link(c['txt'], 'plain text', mdirs)}</div>" if c.get("txt") and f != c.get("txt") else "")
              + "</div>")
        A("</div></details>")
    if not web and not tui:
        A("<p class='meta'>Manifest has no captures.</p>")


def yesno(v) -> str:
    if v is None:
        return "—"
    return "yes" if v else "no"


def render_cli(A, sp: str, base: pathlib.Path, images: Images):
    p = resolve(sp, base)
    A(f"<h3 class='mono'>{esc(sp)}</h3>")
    if not p.exists():
        A(f"<p class='meta'>CLI summary not found: {esc(sp)}</p>")
        return
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        A(f"<p class='meta'>CLI summary unreadable: {esc(e)}</p>")
        return
    if not isinstance(s, dict):
        A("<p class='meta'>CLI summary is not a JSON object</p>")
        return
    meta = [f"command: <code>{esc(s.get('command', '—'))}</code>"]
    if s.get("home") is not None:
        meta.append(f"home: <code>{esc(s.get('home'))}</code>")
    t_out = s.get("timeout_s", s.get("timeout"))
    if t_out is not None:
        meta.append(f"timeout: {esc(t_out)} s")
    A("<div class='meta'>" + " · ".join(meta) + "</div>")
    extra = {k: v for k, v in s.items() if k not in ("command", "home", "timeout", "timeout_s", "probes", "flags", "generated_at")
             and isinstance(v, (int, float, str, bool))}
    if extra:
        A("<div class='chips'>" + "".join(f"<span class='chip neutral'>{esc(k.replace('_', ' '))}: {esc(v)}</span>" for k, v in extra.items()) + "</div>")
    probes = [x for x in as_list(s.get("probes")) if isinstance(x, dict)]
    if probes:
        A("<div class='tw'><table><tr><th>Probe</th><th>Exit</th><th>Timed out</th><th>ANSI stdout</th><th>Duration ms</th></tr>")
        for pr in probes:
            name = str(pr.get("probe") or "?")
            argv = pr.get("argv")
            title = " ".join(str(a) for a in argv) if isinstance(argv, list) else str(argv or "")
            if pr.get("env"):
                title = (" ".join(f"{k}={v}" for k, v in pr["env"].items()) + " " + title) if isinstance(pr["env"], dict) else title
            txt = (p.parent / f"{name}.txt").resolve()
            cell = images.link(txt, name) if txt.exists() else esc(name)
            skipped = pr.get("skipped")
            exit_cell = f"skipped{': ' + esc(skipped) if isinstance(skipped, str) else ''}" if skipped else esc("—" if pr.get("exit") is None else pr.get("exit"))
            to = pr.get("timed_out")
            A(f"<tr><td title='{esc(title)}'>{cell}</td><td>{exit_cell}</td>"
              f"<td class='{'bad' if to else ''}'>{yesno(to)}</td>"
              f"<td class='{'warn' if pr.get('ansi_stdout') else ''}'>{yesno(pr.get('ansi_stdout'))}</td>"
              f"<td>{esc(pr.get('duration_ms', '—'))}</td></tr>")
        A("</table></div>")
    flags = as_list(s.get("flags"))
    if flags:
        def ftext(fl):
            if isinstance(fl, dict):
                main_t = fl.get("message") or fl.get("text") or fl.get("flag") or fl.get("name") or json.dumps(fl)
                return (f"{fl['probe']}: " if fl.get("probe") else "") + str(main_t)
            return str(fl)
        A("<div class='sec'><b>Automated flags</b><ul>" + "".join(f"<li>{esc(ftext(fl))}</li>" for fl in flags) + "</ul></div>")
    elif "flags" in s:
        A("<p class='meta'>Automated flags: none.</p>")


def render_cell(c, ncols: int, images: Images, base: pathlib.Path, single: bool) -> str:
    kind, val = parse_cell(c)
    span = f" colspan='{ncols}'" if single and ncols > 1 else ""
    any_note = "<span class='any'>any size</span>" if single and ncols > 1 else ""
    if kind == "ok":
        if not val:
            return f"<td class='bad'{span}>ok (no evidence){any_note}</td>"
        p = resolve(val, base)
        inner = images.link(val, "ok") if p.exists() and p.is_file() else "ok"
        return f"<td class='ok' title='{esc(val)}'{span}>{inner}{any_note}</td>"
    if kind == "ux":
        return (f"<td class='f' title='{esc(c)}'{span}>" + ", ".join(f"<a href='#{esc(i)}'>{esc(i)}</a>" for i in val)
                + f"{any_note}</td>")
    if kind == "na":
        return f"<td class='na' title='{esc(val)}'{span}>n/a" + (f"<span class='why'> — {esc(val)}</span>" if val else "") + f"{any_note}</td>"
    if kind == "unchecked":
        return f"<td class='unchecked'{span}>not checked" + (f"<span class='why'> — {esc(val)}</span>" if val else "") + f"{any_note}</td>"
    return f"<td class='unchecked'{span}>{esc(c)}{any_note}</td>"


def render(review: dict, base: pathlib.Path, images: Images, prior_ledger=None, merged=None) -> str:
    normalise(review)
    findings = sorted(review.get("findings", []), key=lambda f: (PRIORITIES.index(f["priority"]) if f.get("priority") in PRIORITIES else 9, id_sort_key(f.get("id"))))
    by_pri = collections.Counter(f.get("priority") for f in findings)
    if merged is None:
        merged = merge_ledger(review, prior_ledger, "", base)
    quick = quick_wins(merged)
    finding_ids = {f.get("id") for f in findings}

    def prior_anchor(pid) -> str:  # prior-table row id; never the same as a finding's article id
        return str(pid) if pid not in finding_ids else f"prior-{pid}"
    surface = [str(s) for s in as_list(review.get("surface"))]
    ledger = prior_by_id(prior_ledger)
    out = []
    A = out.append

    lang = str(review.get("lang") or "en")
    A(f"<!doctype html><html lang='{esc(lang)}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
      f"<title>{esc(review.get('project', 'UI/UX review'))} UX review</title><style>{CSS}</style></head><body><main>")
    A("<nav class='nav'><a href='#verdict'>Verdict</a><a href='#screens'>Screens</a><a href='#scorecard'>Scorecard</a>"
      "<a href='#quick'>Quick wins</a><a href='#findings'>Findings</a><a href='#jobs'>Jobs</a><a href='#keep'>Keep</a>"
      "<a href='#prior'>Prior</a><a href='#notchecked'>Not checked</a></nav>")
    A(f"<h1>UI/UX review — {esc(review.get('project', ''))}</h1>")
    A(f"<div class='meta'>{esc(review.get('date', ''))} · {esc(', '.join(surface))} · depth: {esc(review.get('depth', ''))} · "
      f"{len(findings)} findings ({esc(', '.join(f'{p} {by_pri[p]}' for p in PRIORITIES if by_pri[p]) or 'none')})</div>")

    # verdict + regressions
    A("<section id='verdict'><div class='verdict'>" + esc(review.get("verdict", "")) + "</div>")
    regs = []
    for p in review.get("prior", []):
        if p.get("status") == "regressed":
            it = ledger.get(p.get("id")) or {}
            pri = it.get("priority") or p.get("priority")
            if pri in ("P0", "P1"):
                regs.append((p.get("id"), pri, it.get("title") or p.get("title") or ""))
    if regs:
        A("<div class='callout bad' id='regressions'><b>Regressions</b><ul>" + "".join(
            f"<li><a href='#{esc(prior_anchor(i))}'>{esc(i)}</a> {esc(pri)} — {esc(t)}</li>" for i, pri, t in regs) + "</ul></div>")
    users = [u for u in review.get("users") or [] if isinstance(u, dict)]
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
        A("<div class='meta'>Run: " + esc(sc.get("run_command", "—")) + " · Jobs in scope: " + esc("; ".join(str(x) for x in as_list(sc.get("jobs")))) +
          (" · Prior review: " + esc(sc["prior_review"]) if sc.get("prior_review") else "") + "</div>")
    A("</section>")

    # screens overview
    A("<section id='screens'><h2>Screens overview</h2>")
    mans = manifest_paths(review, "captures_manifest")
    for mp in mans:
        render_manifest(A, mp, base, images, show_name=len(mans) > 1)
    if not mans:
        A("<p class='meta'>No captures manifest given (set <code>captures_manifest</code> in review.json to build the contact sheet).</p>")
    clis = manifest_paths(review, "cli_summary")
    if clis:
        A("<h2 id='cli' style='border-top:0;margin-top:20px'>CLI probes</h2>")
        for sp in clis:
            render_cli(A, sp, base, images)
    A("</section>")

    # scorecard
    by_dim = open_findings_by_dim(review, prior_ledger)
    A("<section id='scorecard'><h2>Scorecard</h2><div class='chips'>")
    for dim, v in (review.get("scorecard") or {}).items():
        label, cls = SCORES.get(v, (v, ""))
        sug, worst = suggest_score(by_dim.get(canon_dim(dim), []))
        tip, extra = "", ""
        if sug:
            tip = f"suggested from open findings: {sug} ({worst[0]} {worst[1]})"
            if v not in SCORE_RANK or SCORE_RANK[v] < SCORE_RANK[sug]:
                extra = f" <span class='sug'>(findings say {esc(SCORES[sug][0].lower())})</span>"
        A(f"<span class='chip {cls}' title='{esc(tip)}'>{esc(dim)}: {esc(label)}{extra}</span>")
    A("</div><p class='meta'>Coarse on purpose — for the trend across reviews. Broken = an open P0/P1 in the dimension; "
      "needs work = an open P2; good = checked, only P3 or nothing open.</p></section>")

    # quick wins
    A(f"<section id='quick'><h2>Quick wins ({len(quick)})</h2>")
    if quick:
        lis = []
        for it in quick:
            iid = it.get("id")
            new = iid in finding_ids
            anchor = iid if new else prior_anchor(iid)
            lis.append(f"<li><a href='#{esc(anchor)}'>{esc(iid)}</a> <span class='pri {esc(it.get('priority'))}'>{esc(it.get('priority'))}</span> "
                       f"{esc(it.get('title') or '(no title)')}" + (f" <span class='meta'>· {esc(it.get('where'))}</span>" if it.get("where") else "")
                       + ("" if new else f" <span class='badge'>{esc(it.get('status'))} · from an earlier review</span>") + "</li>")
        A("<ul>" + "".join(lis) + "</ul>")
        A("<p class='meta'>Every still-open item with S effort at P0–P2 (or marked quick_win), new and carried over — the same list fixes.md marks.</p>")
    else:
        A("<p class='meta'>None — quick wins are still-open S-effort items at P0–P2.</p>")
    A("</section>")

    # findings
    A("<section id='findings'><h2>Findings</h2><p class='meta'>" + " · ".join(f"<b>{p}</b> {h}" for p, h in PRIORITY_HELP.items()) + "</p>")
    for f in findings:
        p = f.get("priority", "P3")
        fid = f.get("id")
        A(f"<article class='card finding {esc(p)}' id='{esc(fid)}'><div class='fhead'><span class='pri {esc(p)}'>{esc(p)}</span>"
          f"<span class='fid'>{esc(fid)}</span><h3 style='margin:0'>{esc(f.get('title'))}</h3>"
          f"<span class='badge {esc(f.get('evidence'))}'>{esc(f.get('evidence'))}</span>"
          + (f"<span class='badge level' title='how deep the fix goes'>{esc(f['level'])}</span>" if f.get("level") else "")
          + (f"<span class='badge'>{esc(f['dimension'])}</span>" if f.get("dimension") else "")
          + (f"<span class='badge'>{esc(f['tag'])}</span>" if f.get("tag") else "")
          + f"<span class='badge'>effort {esc(f.get('effort'))}</span>"
          + ("<span class='badge'>quick win</span>" if is_quick_win(f) else "")
          + (f"<span class='badge'>see {esc(', '.join(str(r) for r in f['related']))}</span>" if f.get("related") else "") + "</div>")
        inst = f.get("instances") or []
        A(f"<div class='sec'><b>Where</b>{esc(f.get('where'))}"
          + ("<ul>" + "".join("<li>" + esc(i.get("where")) + (" · " + images.link(i["capture"], "capture") if i.get("capture") else "") + "</li>" for i in inst) + "</ul>" if inst else "")
          + "</div>")
        seen = f.get("seen") if isinstance(f.get("seen"), dict) else {}
        A("<div class='sec'><b>Seen</b>" + esc(seen.get("text", "")) + "<div class='shots'>" + "".join(images.tag(c, f"{fid} seen") for c in seen.get("captures") or []) + "</div></div>")
        if f.get("repro"):
            A("<div class='sec'><b>Repro</b><ol>" + "".join(f"<li>{esc(s)}</li>" for s in f["repro"]) + "</ol></div>")
        A(f"<div class='sec'><b>Why it matters</b>{esc(f.get('why'))}" + (f" <span class='meta'>(reach: {esc(f['reach'])})</span>" if f.get("reach") else "") + "</div>")
        do = f.get("do") if isinstance(f.get("do"), dict) else {}
        A("<div class='sec'><b>Do</b>" + esc(do.get("text", "")) + "<div class='shots'>" + "".join(images.tag(c, f"{fid} do") for c in do.get("captures") or []) + "</div>"
          + "".join(f"<div class='proposal'><div class='lbl'>Proposal — not the current product</div><div class='shots'>{images.tag(c, f'{fid} proposal')}</div></div>"
                    for c in do.get("mockups") or []) + "</div>")
        if f.get("verify"):
            A(f"<div class='sec verify'><b>Verify</b>{esc(f.get('verify'))}</div>")
        A("</article>")
    if not findings:
        A("<p class='meta'>No findings recorded.</p>")
    A("</section>")

    # jobs + matrices
    A("<section id='jobs'><h2>Jobs and state matrix</h2>")
    for j in review.get("jobs") or []:
        A(f"<div class='card'><h3>{esc(j.get('name'))}</h3><div class='meta'>Steps: {esc(j.get('steps', '?'))} (minimum {esc(j.get('min_steps', '?'))}) · "
          f"done without docs: {esc(j.get('without_docs', '?'))}</div>")
        m = j.get("matrix") or {}
        rows = matrix_rows(m)
        if rows:
            sizes = as_list(m.get("sizes"))
            ncols = max(len(sizes), 1)
            A("<div class='tw'><table><tr><th>State</th>" + ("".join(f"<th>{esc(s)}</th>" for s in sizes) or "<th>any size</th>") + "</tr>")
            for state, cells in rows:
                single = len(cells) == 1
                row = f"<tr><td>{esc(state)}</td>" + "".join(render_cell(c, ncols, images, base, single) for c in cells)
                if not single and len(cells) < len(sizes):
                    row += "<td class='unchecked'></td>" * (len(sizes) - len(cells))
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
        A("<div class='tw'><table><tr><th>ID</th><th>Priority</th><th>Title</th><th>Status</th><th>Note</th><th>Evidence</th></tr>")
        cls_of = {"fixed": "ok", "partly": "warn", "regressed": "bad", "open": "unchecked", "not-retested": "unchecked", "wontfix": "na", "obsolete": "na"}
        for p in sorted(prior, key=lambda x: id_sort_key(x.get("id"))):
            it = ledger.get(p.get("id")) or {}
            st = p.get("status")
            pri = it.get("priority") or p.get("priority") or ""
            A(f"<tr id='{esc(prior_anchor(p.get('id')))}'><td class='fid'>{esc(p.get('id'))}</td><td class='pri {esc(pri)}'>{esc(pri)}</td>"
              f"<td>{esc(it.get('title') or p.get('title') or '')}</td><td class='{cls_of.get(st, '')}'>{esc(st)}</td><td>{esc(p.get('note', ''))}</td><td><div class='shots'>"
              + "".join(images.tag(c, f"{p.get('id')} {st}") for c in p.get("captures") or []) + "</div></td></tr>")
        A("</table></div>")
        if prior_ledger is not None:
            listed = {p.get("id") for p in prior}
            rest = collections.Counter(it.get("status") for i, it in ledger.items() if i not in listed)
            if rest:
                A("<p class='meta'>Carried unchanged from the ledger: " + esc(", ".join(f"{n} {s}" for s, n in sorted(rest.items()))) + ".</p>")
    else:
        A("<p class='meta'>First review, or no prior findings.json given.</p>")
    A("</section>")
    A("<section id='notchecked'><h2>Not checked</h2>")
    unchecked = review.get("not_checked") or []
    A("<ul>" + "".join(f"<li><b>{esc(n.get('what'))}</b> — {esc(n.get('why', ''))}</li>" if isinstance(n, dict) else f"<li>{esc(n)}</li>" for n in unchecked) + "</ul>" if unchecked else "<p class='meta'>Everything in scope was checked.</p>")
    A("</section>")
    A(f"<p class='meta' style='margin-top:40px'>Built from review.json by the uiux-review skill · {esc(review.get('date', ''))}</p></main></body></html>")
    return "".join(out)


# ---------------------------------------------------------------- ledger + fixes

def merge_ledger(review: dict, prior_ledger, report_name: str, base: pathlib.Path) -> dict:
    """Cumulative register: every prior item, statuses from review.prior[], plus the new findings."""
    date = review.get("date") or datetime.date.today().isoformat()
    items = collections.OrderedDict((it["id"], copy.deepcopy(it)) for it in (prior_ledger or {}).get("items", []))
    for p in review.get("prior", []):
        pid = p.get("id")
        if not pid:
            continue
        st = p.get("status")
        it = items.get(pid)
        if it is None:
            it = {"id": pid}
            for k in ("title", "priority", "dimension"):
                if p.get(k):
                    it[k] = p[k]
            it["history"] = []
            items[pid] = it
        elif p.get("title") and not it.get("title"):
            it["title"] = p["title"]
        it["status"] = st
        it["last_seen"] = date
        h = {"date": date, "status": st}
        if p.get("captures"):
            h["captures"] = list(p["captures"])
        if p.get("note"):
            h["note"] = p["note"]
        it.setdefault("history", []).append(h)
    for f in review.get("findings", []):
        it = {k: copy.deepcopy(f[k]) for k in ITEM_KEYS if k in f}
        it["quick_win"] = is_quick_win(f)
        for k, v in f.items():
            if k not in ITEM_KEYS and k not in ("status", "first_seen", "last_seen", "history"):
                it[k] = copy.deepcopy(v)
        it.update(status="open", first_seen=date, last_seen=date, history=[{"date": date, "status": "open"}])
        items[f["id"]] = it
    nums = [id_num(i) for i in items]
    top = max([n for n in nums if n is not None] + [((prior_ledger or {}).get("next_id") or 1) - 1, 0])
    return {"schema": 2, "project": review.get("project"), "date": date, "report": report_name, "next_id": top + 1,
            "scorecard": review.get("scorecard", {}),
            "items": sorted(items.values(), key=lambda it: id_sort_key(it.get("id")))}


def write_ledger(ledger: dict, base: pathlib.Path, path: pathlib.Path):
    out = copy.deepcopy(ledger)
    fn = rebaser(base, path.parent)
    for it in out["items"]:
        rebase_item(it, fn)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fixes_md(ledger: dict, base: pathlib.Path, fixes_dir: pathlib.Path) -> str:
    fn = rebaser(base, fixes_dir)
    quick_ids = {it.get("id") for it in quick_wins(ledger)}
    open_items = [it for it in ledger["items"] if it.get("status") in STILL_OPEN]
    open_items.sort(key=lambda it: (PRIORITIES.index(it["priority"]) if it.get("priority") in PRIORITIES else 9, id_sort_key(it.get("id"))))
    L = [f"# Fixes — {ledger.get('project') or ''} ({ledger.get('date') or ''})", "",
         "Every open, regressed, partly fixed or not re-tested item in the ledger, by priority then ID. "
         "Paths are relative to this file; images under Do mockups are proposals, not the current product.", ""]
    if not open_items:
        L.append("No open items.")
        return "\n".join(L) + "\n"
    for it in open_items:
        L.append(f"## {it.get('id')} · {it.get('priority') or '—'} · {it.get('title') or '(no title)'}")
        L.append("")
        st = it.get("status")
        seen_bits = [f"first seen {it['first_seen']}" if it.get("first_seen") else "", f"last seen {it['last_seen']}" if it.get("last_seen") else ""]
        seen_bits = [b for b in seen_bits if b]
        L.append(f"- **Status:** {st}" + (f" ({', '.join(seen_bits)})" if seen_bits else ""))
        last_note = next((h.get("note") for h in reversed(it.get("history") or []) if isinstance(h, dict) and h.get("note")), None)
        if last_note:
            L.append(f"  - note: {last_note}")
        L.append(f"- **Level:** {it.get('level') or '—'}")
        L.append(f"- **Where:** {it.get('where') or '—'}")
        for i in it.get("instances") or []:
            if isinstance(i, dict):
                L.append(f"  - {i.get('where')}" + (f" — `{fn(i['capture'])}`" if i.get("capture") else ""))
        do = it.get("do") if isinstance(it.get("do"), dict) else ({"text": it["do"]} if isinstance(it.get("do"), str) else {})
        L.append(f"- **Do:** {do.get('text') or '—'}")
        for c in do.get("captures") or []:
            L.append(f"  - capture: `{fn(c)}`")
        for c in do.get("mockups") or []:
            L.append(f"  - mockup (proposal): `{fn(c)}`")
        L.append(f"- **Verify:** {it.get('verify') or '—'}")
        L.append(f"- **Effort:** {it.get('effort') or '—'}" + (" (quick win)" if it.get("id") in quick_ids else ""))
        L.append(f"- **Evidence:** {it.get('evidence') or '—'}")
        if it.get("related"):
            L.append(f"- **Related:** {', '.join(str(r) for r in as_list(it['related']))}")
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review", help="path to review.json")
    ap.add_argument("--out", help="report path (default: report.html beside review.json)")
    ap.add_argument("--findings-out", help="ledger path (default: findings.json beside review.json)")
    ap.add_argument("--fixes-out", help="hand-off path (default: fixes.md beside review.json)")
    ap.add_argument("--prior", help="previous findings.json (default: scope.prior_review, relative to review.json, if it exists)")
    ap.add_argument("--inline", action="store_true", help="embed images as data URIs")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--allow-sensitive", action="store_true", help="build even if tokens, keys or real emails are found")
    a = ap.parse_args(argv)

    rpath = pathlib.Path(a.review)
    base = rpath.parent
    review, raw = load_json_file(rpath, rpath.name)
    if not isinstance(review, dict):
        die(f"error: {rpath.name} must be a JSON object at the top level")
    norm_errors = normalise(review)  # validate() normalises again; keep the first pass's errors

    prior_ledger, pre_warn, prior_errors = None, [], []
    if a.prior:
        prior_ledger, pre_warn = load_prior(pathlib.Path(a.prior), base)  # missing --prior file: exits with an error
    else:
        pr = (review.get("scope") or {}).get("prior_review")
        if pr is not None and str(pr).strip():
            pp = resolve(str(pr).strip(), base)
            if pp.is_file():
                prior_ledger, pre_warn = load_prior(pp, base)
            else:
                prior_errors.append(f"scope.prior_review not found: {pp.resolve()} — fix the path, or set it to null "
                                    "for a first review")
    if prior_ledger is None and review.get("prior") and not prior_errors:
        n = len(review["prior"])
        prior_errors.append(f"prior[] has {n} {'entry' if n == 1 else 'entries'} but no prior ledger was loaded — "
                            "set scope.prior_review to the last findings.json (or use --prior)")
    errors, warnings = validate(review, base, prior_ledger)
    errors = prior_errors + norm_errors + [e for e in errors if e not in norm_errors]
    hits, scan_warn = scan_files(raw, rpath, review, base)
    warnings = pre_warn + warnings + scan_warn
    for w in warnings:
        print(f"[build_report] warning: {w}", file=sys.stderr)
    for e in errors:
        print(f"[build_report] error: {e}", file=sys.stderr)
    for h in hits:
        print(f"[build_report] sensitive{' (allowed)' if a.allow_sensitive else ''}: {h}", file=sys.stderr)
    if hits and not a.allow_sensitive:
        print("[build_report] sensitive content found — remove or redact it (or use --allow-sensitive if it is synthetic)", file=sys.stderr)
    if errors or (a.strict and warnings) or (hits and not a.allow_sensitive):
        sys.exit(1)

    out = pathlib.Path(a.out) if a.out else base / "report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    images = Images(base, out.parent, a.inline)
    fpath = pathlib.Path(a.findings_out) if a.findings_out else base / "findings.json"
    ledger = merge_ledger(review, prior_ledger, relpath(out, fpath.parent), base)
    html_out = render(review, base, images, prior_ledger, ledger)
    out.write_text(html_out, encoding="utf-8")
    write_ledger(ledger, base, fpath)
    xpath = pathlib.Path(a.fixes_out) if a.fixes_out else base / "fixes.md"
    xpath.parent.mkdir(parents=True, exist_ok=True)
    xpath.write_text(fixes_md(ledger, base, xpath.parent), encoding="utf-8")
    size_mb = out.stat().st_size / 1e6
    n_open = sum(1 for it in ledger["items"] if it.get("status") in STILL_OPEN)
    n_quick = len(quick_wins(ledger))
    print(f"[build_report] {out} ({size_mb:.1f} MB), {fpath} (next_id {ledger['next_id']}), {xpath} ({n_open} open, "
          f"{n_quick} quick win{'' if n_quick == 1 else 's'})", file=sys.stderr)
    if size_mb > 16:
        print("[build_report] warning: page over 16 MB — publish without --inline and pass images as files", file=sys.stderr)
    files = ([] if a.inline else images.used) + [h for h in images.linked if a.inline or h not in images.used]
    if files:
        print("[build_report] files to publish alongside the page (Artifact `files` map, keys as listed, relative to the report):", file=sys.stderr)
        for u in files:
            print(f"  {u}", file=sys.stderr)
        if any(u.startswith("../") for u in files):
            print("[build_report] warning: some files sit outside the report folder (../) and can't be published as artifact files — "
                  "build with --out inside the review folder, or use --inline", file=sys.stderr)


if __name__ == "__main__":
    main()
