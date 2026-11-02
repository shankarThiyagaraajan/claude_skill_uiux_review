# Heuristics and criteria — what to tag a finding with

Tagging is optional. Use it when it sharpens the point or gives the fixer something to look up. One tag per finding is plenty.

## Nielsen's 10 usability heuristics

| Tag | Heuristic | What it looks like when broken | Dimension |
|-----|-----------|-------------------------------|-----------|
| N1 | Visibility of system status | No spinner, no progress on long jobs, "saved" never confirmed, stale data with no "as of" | States and feedback; Copy and meaning |
| N2 | Match between system and real world | Internal names in labels, codes instead of words, date in ISO when users think in "today" | Copy and meaning; Information architecture |
| N3 | User control and freedom | No undo, no cancel on a long job, back button breaks state, modal with no exit | Money, data and destructive actions; Navigation |
| N4 | Consistency and standards | Same key means two things, three names for one concept, button styles that don't predict behaviour | Navigation and keys; Visual hierarchy |
| N5 | Error prevention | Destructive action one click away, no confirmation that says what will happen, no dry run | Money, data and destructive actions; Forms |
| N6 | Recognition rather than recall | Shortcuts not discoverable, settings remembered nowhere, user must remember IDs across screens | Navigation and keys; IA |
| N7 | Flexibility and efficiency | No keyboard path for power users, no bulk action, no `--yes` / `--json` for scripting | Navigation and keys; CLI ergonomics |
| N8 | Aesthetic and minimalist design | Every number the same weight, no primary action, dense screens with no grouping | Visual hierarchy and colour; Layout |
| N9 | Help users recognise, diagnose and recover from errors | "Something went wrong", stack traces to users, errors that don't say the fix | Copy and meaning; States and feedback; CLI |
| N10 | Help and documentation | No help in context, `--help` that lists flags without examples, setup explained only in a README | Onboarding; CLI ergonomics |

## WCAG 2.2 AA — the criteria that come up in product reviews

The accessibility baseline in this review is not a compliance audit (that is `accessibility-review`). These are the criteria you will actually meet while walking jobs, with the quickest way to check each.

| Criterion | Name | Quick check | Dimension |
|-----------|------|-------------|-----------|
| 1.1.1 | Non-text content | Icons-only buttons have an accessible name (axe flags most) | Accessibility baseline |
| 1.3.1 | Info and relationships | Headings are real headings, tables are tables, form fields have labels | Accessibility; Forms |
| 1.4.1 | Use of colour | Status conveyed by colour also has a word, icon or pattern | Visual hierarchy and colour |
| 1.4.3 | Contrast (minimum) | ≥ 4.5:1 body text, ≥ 3:1 large text; axe reports ratios | Visual hierarchy and colour |
| 1.4.4 | Resize text | 200% zoom still usable | Layout and fit |
| 1.4.10 | Reflow | At 320 CSS px wide (≈ 1280 px at 400%, or 360 px capture) no horizontal scroll for content | Layout and fit |
| 1.4.11 | Non-text contrast | Controls and focus rings ≥ 3:1 against background | Visual hierarchy; Accessibility |
| 2.1.1 | Keyboard | Every action reachable by keyboard; the Tab walk in the manifest shows gaps | Navigation and keys |
| 2.1.2 | No keyboard trap | Can leave every widget and modal with Tab or Esc | Navigation and keys |
| 2.4.3 | Focus order | Tab order follows reading order (manifest `tab_walk`) | Navigation and keys |
| 2.4.7 | Focus visible | Focused element visibly outlined (manifest `focus_visible`) | Navigation and keys |
| 2.4.11 | Focus not obscured (new in 2.2) | Sticky headers/footers don't hide the focused element | Layout and fit |
| 2.5.5 / 2.5.8 | Target size | 44×44 is the comfortable bar (AAA); 24×24 is the 2.2 AA floor (manifest `small_targets`) | Touch and small screens |
| 2.5.7 | Dragging movements (new in 2.2) | Anything draggable has a non-drag alternative | Forms; Touch |
| 3.2.1 / 3.2.2 | On focus / on input | Focusing or typing doesn't navigate or submit by surprise | Forms and input |
| 3.3.1 / 3.3.3 | Error identification / suggestion | Error names the field and says how to fix | Forms; Copy and meaning |
| 3.3.2 | Labels or instructions | Required fields and formats stated before the error | Forms and input |
| 3.3.7 | Redundant entry (new in 2.2) | Don't ask for the same thing twice in one flow | Forms; Onboarding |
| 3.3.8 | Accessible authentication (new in 2.2) | No puzzles or transcription to log in | Onboarding |
| 4.1.2 | Name, role, value | Custom controls announce what they are (axe covers most) | Accessibility baseline |
| 4.1.3 | Status messages | Toasts and live updates announced without stealing focus | States and feedback |

## Terminal and CLI conventions worth citing

There is no WCAG for terminals; cite these instead.

- **Keyboard conventions:** `?` or `F1` for help, `q`/`Esc` to leave, `/` to search, `j/k` and arrows both move, `Tab` cycles panes, `Enter` acts, `Ctrl-C` stops the current job before it kills the app.
- **Footer/help bar:** the current screen's keys are visible without opening help; keys that don't apply are hidden or dimmed, not silently inert.
- **Colour:** respect `NO_COLOR` and `TERM=dumb`; never rely on colour alone (many terminals remap palettes); status words next to status colours.
- **CLI output:** human output to stdout when a TTY, structured (`--json`) when asked or when piped; errors to stderr; exit codes: 0 success, 1 general failure, 2 usage error, 130 interrupted.
- **Safety:** destructive commands need `--yes` or a typed confirmation, and offer `--dry-run`; the bare command does something harmless (help or status), never an action.
- **Help:** `--help` fits 80 columns, shows the two or three commands people actually run with examples, and mentions where config lives.
