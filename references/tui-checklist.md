# TUI checklist — what to check and how

Capture first with `scripts/capture_tui.py` at 80×24, 120×40 and 200×60 (add the size the real user runs at if the spec says). Textual apps give SVG screenshots through the pilot; anything else gives a text dump of the screen from a pty. Judge from those.

## Fit at each size

- **80×24** is the hard test: is the main job doable at all? What is cut off, wrapped badly or hidden? Does the app refuse to run, warn, or silently clip?
- **120×40** is the common size: does every pane get enough room for its content, or does one pane crowd the others?
- **200×60** is the density test: is the extra space used (more rows, wider columns, extra pane) or is the 80-column layout centred in a sea of blank?
- Resize while running (send `SIGWINCH` or change the pilot size): does the layout re-flow or corrupt?
- Long strings: a 60-character name in a 20-column cell — truncated with an ellipsis, wrapped, or overflowing into the next column?

## Keys and navigation

- Is there a footer or key bar showing the keys for the current screen? Are keys that don't apply hidden or dimmed?
- `?` or `F1` opens help; `q` or `Esc` leaves the current screen; `Ctrl-C` stops the current job before it kills the app.
- The same key means the same thing on every screen. Record a key map table for the report (screen × key → action) and flag the cells that differ.
- Both `j/k` and arrows move; `Tab` cycles panes; `Enter` acts; `/` searches where there is a list.
- Focus is visible: which pane and which row are active must be obvious from a text dump alone (not only from colour).
- Modal dialogs: Esc closes; focus returns to where it was.
- Mouse: clicking and scrolling work if the toolkit supports it; nothing is mouse-only.

## Colour and signals

- Capture with `NO_COLOR=1` or a monochrome theme: is every status still readable from words, symbols or position?
- Status colours mean one thing each. A palette remapped by the user's terminal must not change meaning.
- Contrast on common dark and light terminal themes; grey-on-black "muted" text is a frequent failure.
- Emoji and wide glyphs: do they align in columns? Is there a plain-ASCII mode for terminals and fonts that lack them?

## States and feedback

- First run with no data: does the screen say what this is and how to get the first item?
- Loading and long jobs: a spinner or progress line, elapsed time, and a way to cancel. When done: done / failed / stopped / interrupted said in words.
- Errors: shown in the UI near where the action was taken, not only in a log file.
- Live data: an "updated hh:mm:ss" stamp and a stale indicator when updates stop.
- Interrupt mid-job (`Ctrl-C` once): does the app stop the job and stay up, or die and leave partial state?

## Copy and meaning

- Pane titles, column headers and status words in the user's language, consistent with the CLI and docs of the same project.
- Numbers: units, grouping, alignment (numbers right-aligned, text left).
- Confirmations for destructive actions say exactly what will happen; typed confirmation for irreversible ones.

## Performance feel

- Redraw flicker when data updates; cursor jumping; input lag at 200×60 with a full list.
- Scrolling a 1000-row list: smooth, and does the selection stay visible?

## Accessibility

- Screen-reader users of terminals rely on the text dump: does it read in a sensible order? Are status changes announced in text, not only by colour?
- A plain or no-emoji mode, and a reduced-motion mode (no spinners animating every frame) if the app animates.
- Minimum font-size independence: nothing assumes a specific glyph width beyond standard box-drawing.
