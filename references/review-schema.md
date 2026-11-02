# review.json, findings.json and fixes.md

## What each file is

- **`review.json`** is what you write.
- **`scripts/build_report.py review.json`** turns it into three files:
  - `report.html`: the page people read.
  - `findings.json`: the ledger. It is cumulative: every finding ever issued for this project, with its full body and status history. The next review reads it.
  - `fixes.md`: every open item with its Where / Do / Verify. Give this to whoever fixes things.

Keep `review.json` next to the captures so relative paths resolve.

`review.json` is **plain JSON**: no `//` comments and no trailing commas. Field notes are in the tables below the example.

## review.json

Example: a trading dashboard. Use your own project's values.

```json
{
  "project": "TBO web dashboard",
  "date": "2026-11-02",
  "lang": "en",
  "surface": ["web", "cli"],
  "depth": "full",
  "scope": {
    "run_command": "npm run dev (port 5173), seed: npm run seed",
    "jobs": ["Check portfolio at open", "Place an order", "Review a signal"],
    "prior_review": "../2026-10-12/findings.json"
  },
  "users": [
    { "name": "Owner-operator trader", "expertise": "expert in trading, casual with software",
      "context": "laptop + one external monitor at market open; phone during the day" }
  ],
  "assumptions": ["Treated the dashboard as the only screen used during market hours."],
  "verdict": "3–5 sentences.",
  "captures_manifest": ["captures/manifest.json"],
  "cli_summary": ["cli-runs/summary.json"],
  "scorecard": {
    "Crashes and dead ends": "good",
    "Information architecture": "needs-work",
    "Navigation and keys": "good",
    "Layout and fit": "broken",
    "Visual hierarchy and colour": "needs-work",
    "States and feedback": "needs-work",
    "Copy and meaning": "needs-work",
    "Money, data and destructive actions": "broken",
    "Forms and input": "good",
    "Touch and small screens": "needs-work",
    "Onboarding": "good",
    "Accessibility baseline": "not-checked",
    "Performance feel": "good",
    "CLI ergonomics": "needs-work"
  },
  "jobs": [
    {
      "name": "Place an order",
      "steps": 6, "min_steps": 4,
      "without_docs": "partly",
      "matrix": {
        "sizes": ["360", "768", "1440", "1920"],
        "states": {
          "First run / empty": ["ok:captures/orders-new__360__light__empty.png", "ok:captures/orders-new__768__light__empty.png", "ok:captures/orders-new__1440__light__empty.png", "ok:captures/orders-new__1920__light__empty.png"],
          "Loading": ["UX-004", "ok:captures/orders-new__768__light__loading.png", "ok:captures/orders-new__1440__light__loading.png", "ok:captures/orders-new__1920__light__loading.png"],
          "Validation error": ["UX-007", "UX-007", "ok:captures/orders-new__1440__light__invalid.png", "ok:captures/orders-new__1920__light__invalid.png"],
          "Server error": ["UX-002"],
          "Network off": ["not checked: dev server refuses connections when offline is simulated"],
          "Long-running": ["n/a: placing an order is instant"]
        }
      }
    }
  ],
  "findings": [
    {
      "id": "UX-012",
      "title": "Order total shown before fees are loaded",
      "priority": "P0",
      "dimension": "Money, data and destructive actions",
      "level": "component",
      "tag": "N1 Visibility of status",
      "evidence": "judged",
      "where": "/orders/new",
      "instances": [
        { "where": "/orders/edit", "capture": "captures/orders-edit__1440__light__slow-fees.png" }
      ],
      "seen": {
        "text": "At 1440, light, fees request delayed 3 s: total renders 12,400 while the fee request is pending; the final total is 12,520.",
        "captures": ["captures/orders-new__1440__light__slow-fees.png"]
      },
      "repro": ["Delay /api/fees by 3 s", "Open New order, pick 10 × ABC", "Read the total before fees arrive"],
      "why": "A trader confirming against the first number they see pays more than they agreed to. Every order, every user.",
      "reach": "all users, every order",
      "do": {
        "text": "In the shared OrderTotal component, show '—' and 'calculating fees' until fees arrive; disable Place order until the total is final.",
        "captures": ["annotated/orders-new__1440__light__slow-fees__do.png"],
        "mockups": []
      },
      "verify": "With /api/fees delayed 3 s, the total shows '—' and Place order is disabled until fees load, on /orders/new and /orders/edit.",
      "effort": "S",
      "related": ["ARCH-004"]
    }
  ],
  "keep": ["Status colours are used consistently and always paired with a word."],
  "prior": [
    { "id": "UX-003", "status": "fixed", "captures": ["captures/orders__360__light.png"] },
    { "id": "UX-005", "status": "open" },
    { "id": "UX-006", "status": "wontfix", "note": "Owner accepts the dense table on desktop; revisit if phone use grows." }
  ],
  "not_checked": [
    { "what": "Network-off state on all screens", "why": "dev server refuses connections when offline is simulated" }
  ]
}
```

## Field notes

### Top level

| Field | Notes |
|---|---|
| `lang` | Optional. The report's `<html lang>`; default `en` |
| `surface` | Any of `web`, `tui`, `cli` |
| `depth` | `quick` or `full` |
| `scope.prior_review` | Path to the previous `findings.json`, relative to this `review.json`, or `null`. The builder loads it as the prior ledger. `--prior PATH` overrides it |
| `captures_manifest` | String or list: the `manifest.json` files from `capture_web.py` / `capture_tui.py`. One contact sheet each |
| `cli_summary` | String or list: `summary.json` files from `run_cli.py`. Each becomes a "CLI probes" table linking every probe's `.txt` |
| `scorecard` | One value per dimension. The key names are exactly the *Dimensions* in `SKILL.md`. Values are below |
| `prior` | One entry per item the prior ledger still has open. Statuses are below |

### Scorecard values

| Value | Means |
|---|---|
| `good` | Checked; only P3 or no open findings |
| `needs-work` | Any open P2 |
| `broken` | Any open P0/P1 in that dimension |
| `n/a` | The surface doesn't have it (e.g. Touch for a CLI) |
| `not-checked` | No capture or probe covered it |

The builder computes a suggested score from the open findings in each dimension. It warns when your score is better than the findings support.

### Each finding

| Field | Required | Notes |
|---|---|---|
| `id` | yes | String `UX-###`. Never reused. New IDs start at the prior ledger's `next_id` (`UX-001` on a first review) |
| `title` | yes | |
| `priority` | yes | `P0`–`P3` |
| `dimension` | | One of the *Dimensions* names |
| `level` | warning if missing | `token` \| `component` \| `pattern` \| `screen` \| `copy` \| `flow`: the lowest level whose change fixes every instance |
| `tag` | | A heuristic or WCAG tag, e.g. `N4 Consistency`, `WCAG 2.4.7` |
| `evidence` | yes | `automated` \| `judged` \| `inferred` |
| `where` | yes | Route, screen or command only. Size, scheme and state go in `seen.text` |
| `instances` | | `[{ "where", "capture"? }]`: other places with the same cause. File the defect once; list the rest here |
| `seen` | yes | `{ "text", "captures": [...] }`. A plain string becomes `{ "text": … }` |
| `repro` | | List of steps |
| `why` | yes | Which job, how, for whom |
| `reach` | | |
| `do` | yes | `{ "text", "captures", "mockups" }`. A plain string becomes `{ "text": … }`. `captures` are annotated captures of the current product (from `annotate.py`). `mockups` are proposals (SVG or PNG), shown labelled "Proposal — not the current product" |
| `verify` | yes | The check that proves it fixed, phrased so a fixer or the next review can run it. E.g. "manifest `small_targets` has no entry under 24 px on /orders at 360" |
| `effort` | yes | `S`, `M` or `L` |
| `quick_win` | | Normally leave it out: the builder marks every S-effort P0–P2 item as a quick win. `true`/`false` overrides that |
| `related` | | Other IDs, including another review's (e.g. `ARCH-004`) |

### Prior statuses

| Status | Needs |
|---|---|
| `fixed`, `partly`, `regressed` | `captures`: a fresh capture showing it |
| `open` | — |
| `wontfix`, `obsolete`, `not-retested` | `note`: why |

## Rules the builder enforces

### Errors

The builder exits 1 and writes nothing when any of these happen:

- **File problems:**
  - Missing or invalid JSON, with the line and column.
  - Wrong types: `findings`, `prior` and `jobs` must be lists; `scope` and `scorecard` must be objects; `verify` must be a string.
  - A non-string `id`, or a `prior[]` entry with no `id`.
- **Finding fields:**
  - A finding missing `id`, `title`, `priority`, `evidence`, `where`, `seen`, `why`, `do`, `verify` or `effort`.
  - `priority`, `evidence`, `effort`, `level` or a scorecard value not in its allowed set.
  - An `instances` entry without `where`.
- **IDs:**
  - Duplicate IDs.
  - A new ID that collides with any ID in the prior ledger ("IDs are never reused").
  - A new ID below the prior ledger's `next_id`.
  - An ID listed twice in `prior[]`.
- **Prior ledger:**
  - `scope.prior_review` is set but the file isn't there. Fix the path, or set it to `null` for a first review.
  - `prior[]` has entries but no prior ledger was loaded.
  - An item that is `open`, `regressed`, `partly` or `not-retested` in the prior ledger but missing from `prior[]`.
  - A status without its required `captures` or `note`.
- **Matrix and verdict:**
  - An empty cell.
  - `ok` without evidence: `ok`, `ok:`, `ok - looked fine`. It must be `ok:<evidence>`.
  - `n/a` or `not checked` without a reason.
  - A missing verdict.

### Warnings

Warnings don't block, except with `--strict`, which makes every warning fatal:

- **Findings:**
  - More than five P0/P1. Re-check each against type × reach; if they still stand, keep them and say so in the verdict.
  - A finding without `level`.
  - A capture path that doesn't exist.
  - `quick_win: true` on a non-S item.
- **Scorecard:**
  - A score better than the findings suggest.
  - An unknown dimension name, or the same dimension given twice.
  - A dimension with no value. Give one, or `n/a` / `not-checked`.
- **Matrix:**
  - A row with neither one cell per size nor exactly one "any size" cell.
  - A UX ID that matches no finding.
  - A free-text cell that fits none of the four forms.
  - Most cells `ok` with little distinct evidence.
- **Regressions:**
  - A regressed P0/P1 not named in the verdict.
  - `regressed` on an item that was never fixed. Use `open`.
- **Other:**
  - No `date` (today is used).
  - An ID that doesn't end in a number.
  - A finding `dimension` not in the list.
  - A `prior[]` ID that isn't in the prior ledger.
- **Files:**
  - `captures_manifest` or `cli_summary` not found.
  - A Playwright storage-state file inside the review folder.

### Sensitive-content gate

Before writing, the builder scans `review.json`, every manifest, every referenced `.txt` capture and every CLI probe file for:

- bearer tokens
- JWTs
- `sk_`/`pk_` live or test keys
- AWS keys
- GitHub tokens (`ghp_…`, `github_pat_…`)
- GitLab tokens (`glpat-…`)
- Anthropic and OpenAI keys (`sk-ant-…`, `sk-…`)
- Slack tokens (`xox?-…`)
- Google API keys (`AIza…`)
- private-key blocks
- `token=` / `password=`-style values
- email addresses. Allowed: `example.com/.org/.net`, `.example`, `.test`, `.invalid`, `.local`, `.localhost` and `localhost` domains; file names like `logo@2x.png`; and `git@`, `noreply@` and `no-reply@` addresses

This is a safety net for common formats, not a guarantee: still look at the captures yourself.

Each hit prints with the file, line and a masked snippet, and the build exits 1. Fix the source: re-capture with masking, or use seeded data. Pass `--allow-sensitive` only when you have checked each hit is a dummy value.

## findings.json (written by the builder)

```json
{
  "schema": 2,
  "project": "TBO web dashboard",
  "date": "2026-11-02",
  "report": "report.html",
  "next_id": 13,
  "scorecard": { "Layout and fit": "broken" },
  "items": [
    {
      "id": "UX-012", "title": "…", "priority": "P0", "dimension": "…", "level": "component",
      "evidence": "judged", "where": "/orders/new", "instances": [],
      "seen": { "text": "…", "captures": ["captures/orders-new__1440__light__slow-fees.png"] },
      "repro": ["…"], "why": "…", "do": { "text": "…", "captures": [], "mockups": [] },
      "verify": "…", "effort": "S", "quick_win": true, "related": [],
      "status": "open", "first_seen": "2026-11-02", "last_seen": "2026-11-02",
      "history": [ { "date": "2026-11-02", "status": "open" } ]
    }
  ]
}
```

- `items[]` holds every ID ever issued, with its full body.
- Capture paths are stored relative to this file, so dated review folders keep working.
- Old ledgers (with `findings[]` and `prior[]` at the top level) are still accepted as `--prior`.

## Next review

1. Point `scope.prior_review` at the last `findings.json`, or pass `--prior`.
2. Re-test every item whose status is `open`, `regressed`, `partly` or `not-retested`.
3. Give each one an entry in `prior[]` with its new status.
4. Number new findings from `next_id`. The builder carries everything else forward.
5. Compare `scorecard` for the trend line.

## Publishing

`build_report.py` prints the list of files `report.html` references: captures, annotated images, mockups and CLI probe files.

- **Artifact tool:** if your environment can publish an HTML artifact, publish `report.html` with those files.
- **Single file:** use `--inline` to embed the images as data URIs (keep the page under about 16 MB).
- **Otherwise:** give the absolute paths of `report.html`, `fixes.md` and `findings.json`.
