# State matrix — the grid every walk fills in

One grid per main job. Rows are states, columns are sizes (web: 360 / 768 / 1440 / 1920; TUI: 80×24 / 120×40 / 200×60; CLI: TTY / piped). Each cell is one of: `ok`, `finding UX-###`, or `not checked (reason)`. Empty cells are not allowed — an untested cell is `not checked`. The matrix goes into `review.json` under `jobs[].matrix` and the report renders it.

## States to cover for every job

| State | How to produce it | What to look for |
|-------|-------------------|------------------|
| First run / empty | Fresh profile or empty seed | Says what this is and how to get the first item; no error for "nothing yet" |
| Loading | Throttle the network (Playwright `route` delay) or large seed | Skeleton or spinner quickly; no layout jump when data lands |
| Slow or partial | Delay one request, fail another | Partial data labelled as partial; no misleading totals |
| Validation error | Submit a bad value | Field named, fix stated, focus moved |
| Server error | Route one API call to return 500 | Message says what failed and what to do; data not shown as if fine |
| Network off | Playwright `context.set_offline(True)` | Clear offline state; retry path; no spinner forever |
| Success | Complete the job | Confirmation where the user is looking; next step offered |
| Long-running | Start the longest job | Progress, elapsed, cancel; end state word: done / failed / stopped / interrupted |
| Interrupted | Refresh mid-task; back button; close and reopen; two tabs at once | State survives or is explained; no double action |
| Stale data | Stop the data source | "as of" stamp; stale indicator |

## Scaling tests (once per screen that lists or renders user content)

| Test | How | What to look for |
|------|-----|------------------|
| 0 items | Empty seed | Empty state, not a blank table |
| 1 item | Single row | Nothing assumes plurals or averages |
| 1000 items | Generate seed | Scroll, search, paging, selection stays visible, no freeze |
| Very long strings | 60-char name, 200-char description | Wrap, ellipsis + full text on demand, or horizontal cue; never a broken layout |
| Big and tiny numbers | 1,23,45,678.90 and 0.0001 | Grouping right for the locale, consistent decimals, no scientific notation by surprise |
| Locale and time | Set the browser locale/time zone (Playwright `locale`, `timezone_id`) | Currency symbol and grouping (e.g. INR lakhs/crores if that is the user), dates in the user's zone with the zone shown when it matters |

## Two numbers per job

- **Steps to complete** vs the minimum you can imagine. Record both; a gap of more than two is a finding.
- **Done without the docs?** yes / no / partly. "No" on a main job is at least P1.

## Example (web, one job)

```
Job: Place an order
State            360        768        1440       1920
First run/empty  ok         ok         ok         ok
Loading          UX-004     ok         ok         ok
Validation err   UX-007     UX-007     UX-007     UX-007
Server error     UX-002     UX-002     UX-002     UX-002
Network off      not checked (needs route interception; dev server rejects)  …
Success          ok         ok         ok         ok
Long-running     n/a        n/a        n/a        n/a
Interrupted      UX-011     ok         ok         ok
Steps: 6 (min 4). Without docs: partly — needed README to find the account picker.
```
