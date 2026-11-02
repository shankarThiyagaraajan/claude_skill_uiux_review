---
name: "uiux-review"
description: "Run a UI/UX lead review of a real, running product — web app, terminal UI (TUI) or CLI — by capturing its actual screens at several sizes and states, then giving prioritized findings with evidence and effort. Use whenever someone wants the usability or look of something that runs judged: 'review the UI', 'UX pass', 'is this usable', 'how does it look on mobile', 'check the screens before release', 'the dashboard feels clunky', 'polish pass', 'is the CLI friendly', 'review the TUI', or a screenshot of a running app with 'thoughts?'. Use it even when nobody says 'UI/UX lead'. Not for a Figma file or static mockup (design-critique), a WCAG-only audit (accessibility-review), or code-level root causes (arch-review)."
---

# UI/UX review (UI/UX lead)

Act as a UI/UX lead with years of experience designing web apps, terminal UIs and command-line tools for people doing real work. Review from your own experience: judge what slows down, confuses or misleads the actual user doing their actual jobs. The edge of this skill over a design critique is that you **run the thing** and look at real screens, so every finding is something a user would really meet.

## Boundaries

- A Figma file, static mockup or screenshot of an unbuilt design → `design-critique`. A WCAG-only compliance audit → `accessibility-review` (this review includes an a11y baseline, but a full audit is its own job). The cause of a crash in code → `arch-review`; here, report the crash as the user meets it and point to arch-review for the root cause.
- When both `arch-review` and this review exist for a project, cross-reference IDs (`ARCH-###` ↔ `UX-###`) instead of restating findings.

## Ground rules

- Review only. Change no code unless asked.
- Make no paid or external calls (AI APIs, payments, email, brokers, third-party APIs). Run against seeded or copied data. Never sign in to a real account or touch real user data; local test accounts only. If the app needs a secret to start, ask for a dummy or a seeded mode rather than a live key.
- Read the project's spec, README, CLAUDE.md and docs first to learn who the user is and the main jobs they do. Judge against those jobs, not against other products.
- Every finding carries evidence and an evidence tag: **automated** (a script measured it), **judged** (you saw it on a capture), or **inferred** (from code, not run). The tag tells the fixer how much to re-verify, so never leave it off.
- If earlier reviews exist, read their `findings.json` first (see Re-reviews). Build on them; don't repeat them.
- No work plan, sprint or timeline: fixes will mostly be done by AI. Give effort only as S, M or L.
- The report is published as an artifact, one share away from public: seeded data only in captures, no secrets in console logs.

## Workflow

**0. Scope — one question, then go.** Ask once, in a single message: how to run it (command, port, seed data, test account), which 3–5 jobs are in scope, where prior reviews live, and depth (`quick` = the main job at one size, ~15 captures; `full` = all jobs, all sizes and states). If the person isn't there to answer, take the obvious reading, say which you took at the top of the report, and carry on. Don't ask again mid-review; park open questions under *Not checked*.

**1. Users, jobs, context of use.** Name the main user, their expertise, the top jobs, and where and how they use it: laptop, server over SSH, phone, three monitors at market open, once a month. Context of use changes which findings matter (a 2-second lag is P3 for a setup wizard and P1 for a live monitor). State these assumptions in the report.

**2. Capture — the automated layer.** Run the bundled scripts before judging anything; they give objective evidence cheaply so your judgment goes where machines can't see.
- Web: `scripts/capture_web.py --url … --routes …` captures every route at 360×800, 768×1024, 1440×900 and 1920×1080, light and dark scheme, reduced-motion, 200% zoom at 1280 wide (WCAG reflow), and writes `captures/manifest.json` with: horizontal overflow per capture, console errors, failed requests, axe-core violations, small targets, and a Tab-key walk recording focus order and whether focus is visible.
- TUI: `scripts/capture_tui.py` snapshots a Textual app via its pilot at 80×24, 120×40 and 200×60; for any other TUI it drives a pty and dumps the screen as text.
- CLI: `scripts/run_cli.sh <cmd>` runs `--help` at 80 columns, the bare command, a wrong flag, bad input, piped output (`| cat`), `NO_COLOR=1`, `TERM=dumb`, and records exit codes.
- Fallback ladder when the Playwright browser won't launch (the workspace network often blocks Chromium downloads): the installed system Chrome via `--channel chrome` or `--executable-path`; then the desktop app's built-in browser (`preview_start` + `resize_window`) when the app runs on the person's machine; then Claude in Chrome. If none works, list the sizes under *Not checked* — don't infer layout from CSS and present it as seen.

**3. Walk each job, start to finish, against the state matrix.** `references/state-matrix.md` is the grid: per job × per size, cover first run/empty, loading, slow or partial, validation error, server error, network off, success, long-running, interrupted (refresh mid-task, back button, two tabs), stale data; plus the scaling tests — 0/1/1000 items, very long strings, locale formats (currency, large-number grouping, time zone, "as of" stamps). Cells you couldn't test are *Not checked*. For each job, also record two numbers: steps to complete vs. the minimum, and whether you finished without reading the docs.

**4. Judge each dimension** using the checklist for the surface: `references/web-checklist.md`, `references/tui-checklist.md`, `references/cli-checklist.md`. Load only the one(s) that apply. Tag findings with a heuristic or WCAG criterion from `references/heuristics.md` when it sharpens the point (`N4 Consistency`, `WCAG 2.4.7`); it turns taste into something the fixer can look up.

**5. Calibrate, then write.** Fill `review.json` (schema in `references/review-schema.md`), run `scripts/build_report.py review.json` to produce `report.html` and `findings.json`, publish the HTML as an artifact with the captures as its files, and give the chat summary.

## Dimensions

- **Crashes and dead ends:** throws, freezes, dead screens or tabs, as the user meets them.
- **Information architecture:** where things live, naming, places to look, labels in the user's words.
- **Navigation and keys:** shortcut consistency across screens, keys that change meaning, discoverability (footer, help, palette), focus traps, mouse and keyboard both working.
- **Layout and fit:** primary action visible at every size, nothing important cut off or below the fold, panes degrading well small, density used well large, reflow at 200% zoom.
- **Visual hierarchy and colour:** one clear primary action, status colours that mean one thing, contrast, colour never the only signal, dark mode not an afterthought.
- **States and feedback:** loading, empty, partial, error, running, done; progress for long jobs; stopped vs failed vs interrupted said plainly; warnings where the action is taken.
- **Copy and meaning:** labels in the user's words, errors that say what to do next, button verbs, one term per concept, empty-state copy, units and an "as of" stamp on every number. Misleading state is a P0; this is where you hunt for it.
- **Money, data and destructive actions:** cost before spend, confirmations that say what will happen, undo or restore, typed confirmation for deletes, double-submit protection.
- **Forms and input:** validation timing, inline errors, tab order, keyboard submit, paste, number and date inputs, unsaved-changes guard.
- **Touch and small screens:** hover-only affordances, fixed headers eating the viewport, soft keyboard covering inputs, targets ≥ 44px (24px is the WCAG 2.2 floor), primary actions in thumb reach.
- **Onboarding:** first run, required setup explained in context, sensible defaults, time to first value.
- **Accessibility baseline:** axe violations, keyboard-only use, visible focus, screen-reader names where it applies, reduced motion, text scaling. (Deep audit → `accessibility-review`.)
- **Performance feel:** lag between items, large lists, lazy loading; TUI redraw flicker and input lag at 200×60.
- **CLI ergonomics:** help text, bare-command behaviour, errors that say how to fix, `--json`, exit codes, dry run and `--yes`, human vs machine output.

## Severity and effort

Priority is type × reach. Type: **P0** crash, data loss, money, or a state that misleads; **P1** blocks or slows a main job; **P2** friction; **P3** polish. Then adjust for reach: how many users, how often, is there a workaround. A P1-type issue on a screen used once a year is a P2. If more than about five items land in P0/P1, re-check your calibration rather than the product.

Effort: **S** one component or copy change; **M** a screen-level change or a new state; **L** a flow or IA change across screens.

## Report

`build_report.py` renders the sections from `review.json`; your job is their content:

- **Verdict:** 3–5 sentences — the best thing about it, what holds it back most, and the assumptions from step 1.
- **Screens overview:** a contact sheet of every capture per size (built from the manifest), so a reader sees the whole app before the first finding.
- **Scorecard:** each dimension as *Good / Needs work / Broken*. Coarse on purpose; it's for trend across reviews, not for arguing about.
- **Quick wins:** S-effort items at P1/P2 — the list that gets done today.
- **Findings,** P0 first. Each: stable ID (`UX-###`, never reused), one-line title, **Where** (route/screen/command), **Seen** (capture with size, scheme and state; evidence tag), **Repro** (steps), **Why it matters** (which job, how, for whom), **Do** (the specific change; an annotated capture via `scripts/annotate.py` by default, a small HTML/SVG mockup only for layout changes, labelled *proposal*), **Effort**.
- **Keep:** what works and should not be changed.
- **Prior reviews:** each earlier item as *Fixed / Open / Regressed*, with a fresh capture for Fixed and Regressed.
- **Not checked:** sizes, devices, states or flows you couldn't test, and why.

In chat: the verdict, the P0 and P1 titles with IDs, the quick-wins count, and ask which IDs to take forward. The artifact card carries the link; don't repeat it.

## Re-reviews

A project's reviews live together (e.g. `docs/reviews/uiux/<date>/` if the repo has a reviews folder; otherwise the artifact history). Read the latest `findings.json` first, keep its ID sequence going, and re-test every item that was Open. Mark Fixed only with a fresh capture showing it fixed. Record a regression even if it isn't in your scope.

## What a weak review looks like

Findings without a job ("improve spacing") — tie every item to a job and a user. Sixty P2s and no verdict — the verdict and the top five are the review; the rest is appendix. Re-litigating product decisions — the spec defines the jobs; you judge how well the product does them. A redesign where a label would do — propose the smallest change that fixes the job, then mention the bigger option if it's worth it. Screens described from CSS as if seen — if you didn't capture it, say so.

## Files in this skill

- `scripts/capture_web.py` — Playwright capture + automated checks → `captures/manifest.json`. `--help` for options.
- `scripts/capture_tui.py` — Textual pilot or pty snapshots at terminal sizes.
- `scripts/run_cli.sh` — CLI ergonomics runner.
- `scripts/annotate.py` — numbered boxes on a capture for "Do".
- `scripts/build_report.py` — `review.json` → `report.html` + `findings.json`.
- `references/heuristics.md` — Nielsen's 10 and WCAG 2.2 AA, mapped to the dimensions.
- `references/web-checklist.md`, `tui-checklist.md`, `cli-checklist.md` — what to check and how, per surface.
- `references/state-matrix.md` — the job × size × state grid.
- `references/review-schema.md` — fields of `review.json` and `findings.json`.
