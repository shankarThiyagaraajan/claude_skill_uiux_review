# Web checklist — what to check and how

Read the `captures/manifest.json` first: it already answers the automated items. The rest you judge from the captures and from walking the jobs. Each item says how to get the evidence.

## From the manifest (automated)

- `overflow` true at any width → Layout and fit, usually P1 at 360 (content hidden) or P2 at larger widths. Evidence: the capture and the `scroll_width` vs `client_width` numbers.
- `console_errors` → Crashes and dead ends if the UI is affected; otherwise note under Not checked / arch-review.
- `failed_requests` (4xx/5xx, blocked) → States and feedback: did the UI say anything? A silent failed request with a normal-looking screen is misleading state (P0).
- `axe.violations` → Accessibility baseline. `serious`/`critical` impact with a user-facing effect is P1; the rest P2. Quote the rule id and the first node selector.
- `small_targets` → Touch and small screens. Only count interactive elements the user needs on the small sizes.
- `tab_walk` → Navigation and keys. Look for: order jumping around the page, elements that never receive focus, `focus_visible: false` on any step, a walk that never reaches the primary action.
- `scheme: dark` captures → compare with light: unreadable text, invisible borders, status colours that changed meaning.
- `zoom200` capture → Layout and fit (WCAG 1.4.4 / 1.4.10): does the layout reflow or get clipped?
- `reduced_motion` capture → does anything still animate or auto-advance?

## Information architecture

- Can you name where each of the top jobs starts from the first screen, without the docs?
- Count the places a user must look to complete each job. More than three is a finding.
- Labels: are nouns the user's nouns (from the spec) or the code's? Same concept, same word, everywhere?
- Is the current location shown (title, breadcrumb, highlighted nav)?

## Navigation and keys

- Shortcuts: is there a list? Are they the same on every screen? Does any key change meaning between screens?
- Keyboard-only: complete one full job with no mouse. Note where you got stuck.
- Mouse-only: complete it with no keyboard (touch users).
- Modals: Esc closes, focus returns to the trigger, focus can't leave the modal while open.
- Back button and refresh: state survives (filters, scroll, half-filled form, running job).
- Deep links: can a screen be bookmarked or shared by URL?

## Layout and fit

- At 360 wide: is the primary action of each screen visible without scrolling? Is anything truncated with no way to read it? Do tables become readable (stack, scroll with a visible cue, or hide columns)?
- At 768: do side panes collapse sensibly, or do two panes fight?
- At 1440 and 1920: is the extra space used (density, more columns) or is a 360-wide layout stretched?
- Sticky headers/footers: do they hide content or the focused element (WCAG 2.4.11)?
- Long strings: paste a 60-character name and a 15-digit number — wrap, truncate with tooltip, or break the layout?

## Visual hierarchy and colour

- Squint test on each capture: what stands out? Is it the primary action and the most important number?
- Count primary-styled buttons per screen. More than one is a finding.
- Status colours: one meaning each, with a word or icon beside them (WCAG 1.4.1). Red for "down" and red for "sell" on the same screen is a finding.
- Contrast: axe gives ratios; also check disabled states, placeholder text and chart labels, which axe often skips.
- Dark mode: real theme or inverted? Shadows, borders, images, charts all readable?

## States and feedback (see state-matrix.md)

- Loading: skeleton or spinner within 300 ms; does anything jump when data arrives?
- Empty: does the empty state say what this screen is for and how to get the first item?
- Error: server error, network off, validation — three different messages, each saying what to do.
- Long-running: progress, cancel, and a clear word for the end state (done / failed / stopped / interrupted).
- Success: confirmation where the user is looking, not only in a toast that disappears.
- Live data: every number has an "as of" time; a stale indicator when updates stop.

## Copy and meaning

- Read every error message aloud: does it name the problem and the next step?
- Button labels are verbs that say what happens ("Place order", not "OK").
- Units on every number; thousands grouping right for the locale; dates in the user's time zone with the zone shown when it matters.
- Confirmations say exactly what will happen ("Delete 12 rows from Holdings. This cannot be undone.").
- Jargon check: pick five labels and ask whether the spec's user would know them.

## Money, data and destructive actions

- Anything that spends, sends or deletes: shown before, confirmed with specifics, undoable or restorable after?
- Typed confirmation for irreversible deletes of real data.
- Double submit: click Save twice fast — two records?
- Unsaved changes: navigate away mid-form — warning?

## Forms and input

- Validation on blur or on submit, never on every keystroke of a half-typed value.
- Inline error next to the field, field named in the error, focus moves to the first error.
- Tab order matches visual order; Enter submits from text fields.
- Number inputs: keyboard shows numbers on mobile (`inputmode`), paste with commas works, negative and zero handled.
- Dates: typed entry as well as a picker; time zone stated.
- Required fields and formats stated before submission (WCAG 3.3.2).

## Touch and small screens

- Hover-only: tooltips, hover menus, hover-revealed actions — unreachable on touch unless there is a tap alternative.
- Targets: manifest `small_targets`; also spacing between adjacent targets.
- Fixed header + fixed footer + soft keyboard: how much of a 360×800 screen is left for the form?
- Primary action reachable by thumb (lower half) on phone-sized screens.
- Swipe or drag interactions have a button alternative (WCAG 2.5.7).

## Onboarding

- Fresh profile, no data: how many steps to first value? Can the user tell what to do next at each step?
- Required setup (keys, paths, accounts) explained where it is needed, with a sensible default or a sample.
- Is there a way back to onboarding later?

## Performance feel

- Move between five items quickly: any lag, flashes of empty content, layout shifts?
- A list of 1000 items: scroll smooth, search instant, pagination or virtualisation present?
- Note Lighthouse/Web Vitals numbers only if a tool is at hand; "feels slow" with a capture of the intermediate state is acceptable evidence, tagged *judged*.
