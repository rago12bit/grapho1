# Accessibility

Hypocenter's pages are plain HTML and CSS with small ES modules, so most of what a browser and a screen reader
expect works without extra machinery. This file says what is in place, what isn't yet, and how to report a barrier.

## In place

- **Language and structure.** Every page declares `lang="en"`, uses headings in order, real `<button>`s and links
  for actions, and `<table>`s for tabular results.
- **Labels and state.** Icon-only and graphic controls carry `aria-label`s (35 across the site); toggles such as
  the metric switch on Results, the label chips on Explore and the replay speed expose `aria-pressed` (22); the guided demo and the
  landing page's "where it sits" diagram announce changes through `aria-live` regions.
- **Keyboard.** Everything is reachable with Tab. `Ctrl K` / `⌘ K` jumps to the incident room's question bar and
  `Esc` dismisses an answer; the parts of the landing page diagram take `Enter` and `Space`. A visible focus ring
  (`:focus-visible`) marks where you are on every page, and the question bar shows focus on its own border.
- **Motion.** With *reduce motion* set in the operating system, smooth scrolling, the cube's spin, the contour
  background, the replay animation and the bar animations on Results are switched off or shown in their end state.
- **Colour.** Light and dark themes follow the operating system, with a toggle in the incident room. Status is
  never colour alone: confidence, claim checks and verdicts are written out in words ("high confidence",
  "14/14 claims checked", "right", "wrong").
- **Small screens.** Every page is laid out for phone width (390 px) without horizontal scrolling.
- **The graph as text.** The call graph is drawn on a canvas, which a screen reader can't read; everything it
  shows is also in the case file as text: the failure path (`mobile-bff → auth`), what each alert's timing was,
  who owns what, and a timeline of every alert and change.

## Not yet

- The canvas views (incident graph, Explore, Replay) can't be navigated node by node with a keyboard or a
  screen reader; use the case file, the Explore node table and the Replay timeline instead.
- No skip link on the site pages.
- No formal audit (an automated checker such as axe, or testing with a screen reader end to end) has been run.

## Reporting a barrier

Open an issue titled "Accessibility: …" with the page, what you were trying to do and what you use (browser,
screen reader, zoom level). Barriers are treated as bugs.
