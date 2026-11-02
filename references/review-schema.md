# review.json and findings.json

`review.json` is what you write; `scripts/build_report.py review.json` turns it into `report.html` (the artifact) and `findings.json` (the ledger the next review reads). Keep both next to the captures so relative paths resolve.

## review.json

```jsonc
{
  "project": "TBO web dashboard",
  "date": "2026-11-02",
  "surface": ["web"],                 // any of "web", "tui", "cli"
  "depth": "full",                    // "quick" | "full"
  "scope": {
    "run_command": "npm run dev (port 5173), seed: npm run seed",
    "jobs": ["Check portfolio at open", "Place an order", "Review a signal"],
    "prior_review": "docs/reviews/uiux/2026-10-12/findings.json"   // or null
  },
  "users": [
    { "name": "Owner-operator trader", "expertise": "expert in trading, casual with software",
      "context": "laptop + one external monitor at market open; phone during the day" }
  ],
  "assumptions": ["Treated the dashboard as the only screen used during market hours."],
  "verdict": "3–5 sentences.",
  "captures_manifest": "captures/manifest.json",   // optional; builds the screens overview
  "scorecard": {                                   // one of: good | needs-work | broken
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
    "Accessibility baseline": "needs-work",
    "Performance feel": "good"
    // "CLI ergonomics" when surface includes "cli"
  },
  "jobs": [
    {
      "name": "Place an order",
      "steps": 6, "min_steps": 4,
      "without_docs": "partly",                     // yes | no | partly
      "matrix": {
        "sizes": ["360", "768", "1440", "1920"],
        "states": {
          "First run / empty": ["ok", "ok", "ok", "ok"],
          "Loading": ["UX-004", "ok", "ok", "ok"],
          "Validation error": ["UX-007", "UX-007", "UX-007", "UX-007"],
          "Network off": ["not checked: dev server rejects offline mode", "…", "…", "…"]
        }
      }
    }
  ],
  "findings": [
    {
      "id": "UX-012",                 // stable, never reused; continue from prior findings.json next_id
      "title": "Order total shown before fees are loaded",
      "priority": "P0",               // P0 | P1 | P2 | P3
      "dimension": "Money, data and destructive actions",
      "tag": "N1 Visibility of status",   // optional heuristic / WCAG tag
      "evidence": "judged",           // automated | judged | inferred
      "where": "/orders/new, 1440 light, slow-fees state",
      "seen": {
        "text": "Total renders ₹12,400 while the fee request is still pending; the final total is ₹12,520.",
        "captures": ["captures/orders-new__1440__light.png"]
      },
      "repro": ["Throttle /api/fees to 3 s", "Open New order, pick 10 × ABC", "Read the total before fees arrive"],
      "why": "A trader confirming against the first number they see pays more than they agreed to. Every order, every user.",
      "reach": "all users, every order",
      "do": {
        "text": "Show '—' and a 'calculating fees' label until fees arrive; disable Place order until the total is final.",
        "captures": ["annotated/orders-new__1440__light__do.png"]   // from annotate.py, or an HTML/SVG mockup labelled proposal
      },
      "effort": "S",
      "quick_win": true,
      "related": ["ARCH-004"]         // optional cross-references
    }
  ],
  "keep": ["Status colours are used consistently and always paired with a word."],
  "prior": [                           // every item from the previous findings.json
    { "id": "UX-003", "title": "Primary action below the fold at 360", "status": "fixed",     // fixed | open | regressed
      "captures": ["captures/orders__360__light.png"] }
  ],
  "not_checked": [
    { "what": "Network-off state on all screens", "why": "dev server refuses connections when offline is simulated" }
  ]
}
```

Rules the builder enforces: every finding has `id`, `title`, `priority`, `evidence`, `where`, `seen`, `why`, `do`, `effort`; `priority` is P0–P3; `evidence` is one of the three tags; IDs are unique and don't collide with prior IDs. It warns (not fails) when a capture path doesn't exist, when more than five findings are P0/P1, or when `quick_win` is set on something that isn't S effort.

## findings.json (written by the builder)

```jsonc
{
  "project": "TBO web dashboard",
  "date": "2026-11-02",
  "report": "report.html",
  "next_id": 13,                       // use this as the first new ID next time
  "scorecard": { "...": "good" },
  "findings": [
    { "id": "UX-012", "title": "…", "priority": "P0", "dimension": "…", "evidence": "judged",
      "where": "…", "effort": "S", "quick_win": true, "status": "open",
      "captures": ["captures/orders-new__1440__light.png"] }
  ],
  "prior": [ { "id": "UX-003", "status": "fixed" } ]
}
```

Next review: read `findings.json`, carry each `findings[]` item into `prior[]` with a fresh status, start new IDs at `next_id`, and compare `scorecard` for the trend line.

## Publishing

`build_report.py` prints the list of files the HTML references (captures, annotated images). Publish `report.html` as the artifact and pass those paths in the Artifact `files` map so the images load; or run with `--inline` to embed them as data URIs when there are only a few (the page must stay under 16 MB).
