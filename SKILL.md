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
- Make no paid or external calls *from the product under review* (AI APIs, payments, email, brokers, third-party APIs). Installing the review tools is fine.
- Run against seeded or synthetic data. Copied production data counts as real user data unless the owner confirms it is anonymised. Never sign in to a real account or touch real user data; local test accounts only. If the app needs a secret to start, ask for a dummy or a seeded mode rather than a live key.
- **Before any state that writes** (submit, delete, double-submit, purchase), confirm the backend is local or disposable. Check the run command and environment for production hosts and live keys (`sk_live_`, a production `DATABASE_URL`). If you can't confirm it, walk read-only and list the writing states under *Not checked*. Default to read-only when nobody answered step 0.
- Read the project's spec, README, CLAUDE.md and docs first to learn who the user is and the main jobs they do. Judge against those jobs, not against other products.
- Every finding carries evidence and an evidence tag. The tag tells the fixer how much to re-verify, so never leave it off:
  - **automated:** a bundled script measured it. A measurement is not a conclusion: confirm it on the capture before calling anything P0/P1, and quote the number.
  - **judged:** you saw it yourself on a capture, text dump or command transcript.
  - **inferred:** from code or docs, not run. At most P1 until reproduced.

  When evidence is mixed, use the weaker tag.
- If earlier reviews exist, read their `findings.json` first (see Re-reviews). Build on them; don't repeat them.
- No work plan, sprint or timeline: fixes will mostly be done by AI. Give effort only as S, M or L.
- Treat the report as one share away from public: seeded data only in captures, no secrets in console logs. `capture_web.py` masks password and card fields, strips URL query strings and redacts tokens in console text. `capture_tui.py` (pty mode) and `run_cli.py` remove secret-looking environment variables but don't redact what the program prints. `build_report.py` refuses to build if it finds a common token or key format, or a real-looking email address. The full list is in `references/review-schema.md`. Fix the source rather than overriding it. The scan is a safety net, not a guarantee. Before sharing, look yourself at every capture of a settings, profile or billing screen.

## Workflow

**0. Scope — one question, then go.** Ask once, in a single message: how to run it (command, port, seed data, test account), which 3–5 jobs are in scope, where prior reviews live, and depth (`quick` = the main job at one size, ~15 captures; `full` = all jobs, all sizes and states). If the person isn't there to answer, take the obvious reading, say which you took at the top of the report, and carry on. Don't ask again mid-review; park open questions under *Not checked*.

**1. Users, jobs, context of use.** Name the main user, their expertise, the top jobs, and where and how they use it: laptop, server over SSH, phone, three monitors at market open, once a month. Context of use changes which findings matter (a 2-second lag is P3 for a setup wizard and P1 for a live monitor). State these assumptions in the report.

**2. Capture — the automated layer.** Run the bundled scripts before judging anything; they give objective evidence cheaply so your judgment goes where machines can't see.
**Web.** Run `python3 scripts/capture_web.py --check` first: it shows which browser, axe and Pillow are available. Then `python3 scripts/capture_web.py --url … --routes …`.

- **Sizes and variants:** every route at 360×800, 768×1024, 1440×900 and 1920×1080, in light and dark scheme, plus two variants per route:
  - a reduced-motion capture at 1440×900
  - a 640×720 capture, which equals a 1280 window at 200% zoom (WCAG 1.4.4 resize text), saved as `<route>__640__light__zoom200.png`
- **What it records** in `captures/manifest.json`:
  - horizontal overflow per capture
  - console errors and failed requests
  - axe-core violations
  - small targets
  - a Tab-key walk recording focus order and whether focus is visible
  - third-party origins the page called
- **Options:**
  - The default wait is `load`. Add `--wait-for <main selector>` for apps that render after load. Use `--wait networkidle` only for static sites: polling or websocket apps never go idle.
  - `--block-third-party` if you can't confirm outside services are sandboxed.
  - `--mask <selector>` for anything else sensitive on screen.
- **Not run is not clean:** read `manifest.summary` first. `axe_skipped`, `*_errors` and `errors` > 0 mean those checks or captures did **not** run. That is *Not checked*, not a pass.

**TUI.**

- Textual apps: `python3 scripts/capture_tui.py textual pkg.module:AppClass` (from the project root).
- Any other TUI (curses, Bubble Tea, Ratatui…): `python3 scripts/capture_tui.py pty "<command>"`.
- Default sizes are 80×24, 120×40 and 200×60. Use `--keys "tab,j,enter" --state <name>` to reach a state.
- The manifest counts likely-cut text with `edge_lines`, `ellipsis_lines` and `wide_widgets`. Find those rows in the `.txt` dump.

**CLI.** Read `<cmd> --help` yourself first. Then run:

```sh
python3 scripts/run_cli.py "<cmd>" --sub "<read-only subcommand>" \
  --sample "<read-only args>" --bad-input "<args wrong for this tool>"
```

- **What it runs:** help at 80 columns, version, the bare command, a wrong flag, bad input, and the sample on a pseudo-tty, piped, with `NO_COLOR=1` and with `TERM=dumb`. Each probe runs once with a timeout, no pager, a scratch `HOME` and secret-looking variables removed.
- **Choose these options deliberately:**
  - `--skip-bare` if the bare command does anything.
  - `--keep-cwd` for tools that act on the current project (git, cargo).
  - `--keep-home` for tools that need their config from `HOME`.
- **Never give a `--sub` or `--sample` that writes, sends or spends.** It runs several times.
- Details are in `references/cli-checklist.md`.
- Fallback ladder when the Playwright browser won't launch (the workspace network often blocks Chromium downloads): the installed system Chrome via `--channel chrome` or `--executable-path`; then the desktop app's built-in browser (`preview_start` + `resize_window`) when the app runs on the person's machine; then Claude in Chrome. If none works, list the sizes under *Not checked* — don't infer layout from CSS and present it as seen.

**3. Walk each job, start to finish, against the state matrix.** `references/state-matrix.md` is the grid: per job × per size, cover first run/empty, loading, slow or partial, validation error, server error, network off, success, long-running, interrupted (refresh mid-task, back button, two tabs), stale data; plus the scaling tests — 0/1/1000 items, very long strings, locale formats (currency, large-number grouping, time zone, "as of" stamps).

**How to capture a state.**

- **Web:** write a short Playwright script in the review folder that reuses the bundled capturer:

  ```python
  import sys; sys.path.insert(0, "<skill-dir>/scripts")
  from capture_web import Capturer
  with Capturer(out="captures") as c:
      page = c.new_page(360, 800)
      c.goto(page, url)
      # set up the state here
      c.snap(page, route="/orders", state="server-error")
  ```

  Set up the state with Playwright:

  | State | How |
  |---|---|
  | Server error | `page.route("**/api/x", lambda r: r.fulfill(status=500))` |
  | Slow | `page.route` with a delay |
  | Offline | `page.context.set_offline(True)` |
  | Interrupted | `page.reload()` mid-task |

  Snaps merge into the same `captures/manifest.json`, and each capture's errors are its own. `--fresh` (or `Capturer(fresh=True)`) starts the manifest over.
- **TUI:** re-run `python3 scripts/capture_tui.py … --keys … --state <name>`.
- **CLI:** save each real command's transcript to `cli-runs/<state>.txt` with its exit code.

- **Columns per state:** layout-sensitive states get a cell per size. Behavioural states (server error, offline, interrupted, stale, long-running) get one "any size" cell, tested at the primary size.
- **Cell values:** `ok:<capture or step>`, a finding ID, `n/a:<reason>` or `not checked:<reason>`. A bare `ok` is rejected. For each job, also record two numbers: steps to complete vs. the minimum, and whether you finished without reading the docs.

**4. Judge each dimension** using the checklist for the surface: `references/web-checklist.md`, `references/tui-checklist.md`, `references/cli-checklist.md`. Load only the one(s) that apply. Tag findings with a heuristic or WCAG criterion from `references/heuristics.md` when it sharpens the point (`N4 Consistency`, `WCAG 2.4.7`); it turns taste into something the fixer can look up.

**5. Calibrate, then write.** Re-check priorities with *Severity and effort* below. Then:

1. **Write `review.json`.** It is plain JSON; the schema is in `references/review-schema.md`.
   - Point `captures_manifest` and `cli_summary` at what step 2 produced.
   - Point `scope.prior_review` at the last `findings.json`.
2. **Run `python3 scripts/build_report.py review.json`.** It validates, checks for secrets, and writes three files:
   - `report.html`
   - `findings.json`: the cumulative ledger.
   - `fixes.md`: every open item with Where / Do / Verify.
3. **Deliver the report.**
   - If your environment can publish an HTML artifact, publish `report.html` with the files the builder lists, or use `--inline`.
   - Otherwise give the absolute paths.
   - Always say where `findings.json` and `fixes.md` are.
4. **Give the chat summary.**

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

Priority is type × reach. Type: **P0** crash, data loss, money, or a state that misleads; **P1** blocks or slows a main job; **P2** friction; **P3** polish. Then adjust for reach: how many users, how often, is there a workaround. A P1-type issue on a screen used once a year is a P2. If more than about five items land in P0/P1, re-check each against type × reach. If they still stand, keep them and say in the verdict that the product isn't ready for the job, rather than spreading them thin.

Effort: **S** one component or copy change; **M** a screen-level change or a new state; **L** a flow or IA change across screens.

## Report

`build_report.py` renders the sections from `review.json`; your job is their content:

- **Verdict:** 3–5 sentences — the best thing about it, what holds it back most, and the assumptions from step 1.
- **Screens overview:** a contact sheet of every capture per size (built from the manifest), so a reader sees the whole app before the first finding.
- **Scorecard:** each dimension as *Good / Needs work / Broken*, or *n/a* (the surface doesn't have it) or *not-checked*. Coarse on purpose: it's for the trend across reviews, not for arguing about.
  - Broken = any open P0/P1 in the dimension.
  - Needs work = any open P2.
  - Good = checked, with only P3 or nothing open.

  The builder warns when a score is better than the findings support.
- **Quick wins:** every still-open S-effort item at P0–P2 (new or carried over), computed by the builder. Set `quick_win: false` or `true` on a finding to override. It's the list that gets done today.
- **Findings,** P0 first. Each has:
  - A stable ID (`UX-###`, never reused) and a one-line title.
  - **Where:** route, screen or command.
  - **Seen:** the capture with size, scheme and state, and the evidence tag.
  - **Repro:** the steps.
  - **Why it matters:** which job, how, for whom.
  - **Do:** the specific change. An annotated capture via `scripts/annotate.py` (`do.captures`), or a small SVG/PNG mockup for layout changes (`do.mockups`, rendered as a proposal).
  - **Level:** the lowest level whose change fixes it — `token`, `component`, `pattern`, `screen`, `copy` or `flow`.
  - **Instances:** the other places with the same cause.
  - **Verify:** the check that proves it fixed.
  - **Effort.**
- **Jobs and state matrix:** per job, steps vs minimum, whether it was done without docs, and the filled grid.
- **Keep:** what works and should not be changed.
- **Prior reviews:** each earlier item as *Fixed / Partly / Open / Regressed / Won't fix / Obsolete / Not re-tested*. Fixed, Partly and Regressed need a fresh capture; the other three need a note. A regressed P0/P1 is named in the verdict.
- **Not checked:** sizes, devices, states or flows you couldn't test, and why.

In chat:

- the verdict
- the P0 and P1 titles with IDs, and any regressions
- the quick-wins count
- where `fixes.md` is
- ask which IDs to take forward

If you published an artifact, its card carries the link, so don't repeat it. Otherwise give the report path.

## Re-reviews

**Where reviews live.** A project's reviews live together. Use `docs/reviews/uiux/<date>/` if the repo has a reviews folder. Otherwise ask where the last `findings.json` is; if there is none, treat this as a first review and say so.

**How to run one.**

1. Read the latest `findings.json` first. It holds every item ever issued, with its full Do and Verify.
2. Set `scope.prior_review` to it, or use `--prior`.
3. Re-test every item that is open, regressed, partly or not-retested, and give each a `prior[]` entry with its new status. The builder refuses to run if one is missing.
4. Number new findings from `next_id`.

Mark Fixed only with a fresh capture showing it fixed, and use each item's `verify` as the test. Record a regression even if it isn't in your scope.

## What a weak review looks like

Findings without a job ("improve spacing") — tie every item to a job and a user. Sixty P2s and no verdict — the verdict and the top five are the review; the rest is appendix. Re-litigating product decisions — the spec defines the jobs; you judge how well the product does them. A redesign where a label would do — propose the smallest change that fixes the job, then mention the bigger option if it's worth it. Screens described from CSS as if seen — if you didn't capture it, say so. The same defect filed once per screen — find the shared cause and file it once at the lowest level that fixes every instance (token, component or pattern), listing the instances. A Do with no Verify — if the fixer can't check it, it isn't a fix.

## Files in this skill

Setup: Python ≥ 3.9. Run the scripts with `python3 <skill-dir>/scripts/<name>.py`, using the venv's `python3` for the web and TUI scripts. Install into a throwaway venv outside the project:

- web: `pip install playwright axe-playwright-python pillow`
- TUI: `pip install textual pyte pexpect pillow`

`capture_web.py` tries the system Chrome before needing `playwright install chromium`. `run_cli.py` and `build_report.py` need only the standard library.

- `scripts/capture_web.py`: Playwright capture plus automated checks → `captures/manifest.json`. `--check` for a preflight; `--help` for options.
- `scripts/capture_tui.py`: Textual pilot or pty snapshots at terminal sizes.
- `scripts/run_cli.py`: the CLI ergonomics runner, with timeouts and a scratch `HOME`. `run_cli.sh` is a wrapper kept for old commands.
- `scripts/annotate.py`: numbered boxes on a capture for "Do".
- `scripts/build_report.py`: `review.json` → `report.html` + `findings.json` (cumulative ledger) + `fixes.md`. `--prior`, `--strict`, `--inline`, `--allow-sensitive`.
- `references/heuristics.md` — Nielsen's 10 and WCAG 2.2 AA, mapped to the dimensions.
- `references/web-checklist.md`, `tui-checklist.md`, `cli-checklist.md` — what to check and how, per surface.
- `references/state-matrix.md` — the job × size × state grid.
- `references/review-schema.md`: the fields of `review.json`, `findings.json` and `fixes.md`, and what the builder enforces.
