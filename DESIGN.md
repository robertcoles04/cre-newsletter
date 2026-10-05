# Design: CRE Blurb HTML preview

Direction: **Offering Memorandum.** The look of a CRE broker's OM book: credible,
serious, calm, premium. Not playful, not flashy, no emoji. Code: `src/render_html.py`.
Sample (illustrative data): `samples/preview-sample.html`.

## Palette (role: value)
- Ground (page behind the sheet): `#EEF0F2`
- Sheet: `#FFFFFF`, max-width 760px (text keeps a 70ch measure), centered, soft offset shadow
- Navy (cover band, headings, links): `#0E2A47`
- Navy tint (cover date line, never gray): `#B9C8DA`
- Navy wash (Term panel background): `#F2F5F9`; selection: `#CCD8E6`
- Gold (cover rule, h2 rule, list markers; never text): `#B08D3C`
- Ink (body text): `#1B2430`; muted (captions, footer): `#56616F`
- Hairline (table rows, footer rule): `#D9DDE3`
- Up `#1F7A4D`, down `#B23A3A`, unch = muted, ghost n/a `#A3ABB5`
- Editor notes: amber bg `#FBF4E4`, line `#E3C88A`, ink `#6E4A0B`

## Type
- Display: "Libre Caslon Text", fallback Georgia, serif (title, h2, term name)
- Body: "Public Sans", fallback -apple-system, Segoe UI, Helvetica, Arial, sans-serif
- Body 17px (16px on phones), line-height 1.6, measure at most 70ch
- Numbers use `font-variant-numeric: tabular-nums`

## Components
- **Cover band:** full-width navy, "CRE Blurb" in serif, white. Date line
  ("Monday, October 5, 2026 • Daily Edition • 4 min read"; Saturday and Sunday say
  "Weekend Edition"; read time = words / 230, rounded, min 1) in navy tint. A 3px gold rule element closes it.
- **Market Summary:** OM "Investment Summary" table built by code from
  `factsheet["values"]`, never model text. Groups Rates / Federal Reserve / REITs
  (Rates include the 2Y, the 10Y-2Y curve and the 30-year mortgage; Fed Funds shows
  the target range "3.75% to 4.00%"). A Rates row whose latest date differs from
  RATES_ASOF adds "(as of Oct 2)" to its hint. Rows are label | value + change with
  hairline dividers.
  Rows whose key is missing are skipped; a row with only a change (CMBS delinquency
  when Trepp states no rate) shows just the change. Caption: "Rates as of
  {RATES_ASOF} close." right under the Rates group (no sources line). Replaces the
  Markdown `## The Numbers`; only its prose ("What it means", REIT movers) is kept,
  after the groups. Order: Rates, caption, yield curve + mortgage (2-up), Federal
  Reserve, Fed odds bar, REITs, REIT scoreboard, prose, 10-Year chart.
- **Charts:** made by our own code (`src/chart.py`, matplotlib), never photos or
  images from the web. Palette tokens only: navy line, gold dot on the latest value,
  navy tint for comparison lines, up/down green/red bars, ink labels, muted ticks,
  hairline grid. Public Sans with Segoe UI / Helvetica / Arial / DejaVu fallbacks,
  white background, no titles, legend boxes or 3D (the caption names the chart; a
  short line swatch + word is the only key). Saved at 2x. Five charts, each optional
  (too little data skips it; an error skips it with an editor note):
  10-Year Treasury (last 30 observations), yield curve (2Y/5Y/10Y/30Y today vs. about
  a month ago), 30-year mortgage (last 26 weeks), Fed odds (one stacked bar cut /
  hold / hike in navy, navy tint and muted, values in a key row), REIT scoreboard
  (every tracked REIT's daily % move, sorted, labeled "PLD · warehouse" with a middle
  dot, skipped under 3 tickers). Captions are fixed text in `CHART_CAPTIONS`; alt text
  is code-filled with the actual values. Every chart is an `<img>` with width/height;
  only the first image on the page is eager, the rest `loading="lazy"`. The
  full-width charts (10-Year, Fed odds, REITs) also have a narrower phone version
  (`<name>-sm.png`) served through `<picture>` under 600px so their text stays
  readable. The yield curve and mortgage charts sit in a CSS grid 2-up row that
  stacks on phones. Link previews (og:image) use the 10-Year chart: its 2:1 shape
  suits preview cards, while the scoreboard's height changes with the ticker count.
- **Data Room:** after Term of the Day, the slower-moving Credit rows (high-yield
  spread, bank CRE loans and delinquency, Trepp CMBS delinquency) in the same row
  styling, with the intro "Slower-moving credit data. Rows marked Updated changed since
  the last issue." Rows dated within 7 days get a muted uppercase "Updated" tag and a
  normal change mark; older rows show their change muted with no arrow. Their as-of
  stays in the label, e.g. "Bank CRE delinquency (Q1 2026)".
- **Change marks:** authored inline SVG triangle + signed text + color. Color is
  never the only signal.
- **n/a:** the muted text "n/a" (ghost color) with `title="Data unavailable today"`.
- **No dashes:** no em or en dashes anywhere on the site. Code text uses commas or
  hyphens; model prose is told not to use them and `no_dashes()` replaces any that
  slip through at render time ("a — b" becomes "a, b").
- **Section heading (h2):** serif, navy, more space above than below, 1px gold rule.
- **Story link:** navy, 1px underline, 3px underline-offset. Links are the only
  colored text besides the change marks.
- **Term panel / Data Room panel:** navy-wash blocks, term in serif. No colored side
  border. Muted hints stay AA on the wash (about 5.8:1).
- **Icons:** small line icons authored in code as inline SVG (`ICON_PATHS` in
  `render_html.py`): 18px, 1.5px stroke, navy (currentColor), `aria-hidden="true"`,
  inline before the heading text. Sun (Sun Belt), waves (West Coast), globe
  (International), stack (Data Room), calendar (Week Ahead), book (Term of the Day).
  No other headings get icons.
- **In this issue:** after The Brief, a plain list of links to every later h2
  (markdown h2s get slug ids like `top-stories`), between hairlines. Only shown with 3
  or more links. No JavaScript.
- **Navigation (site):** Home / Archive / About / RSS; the current page gets
  `aria-current="page"` and a 2px gold underline. Issue pages end with "Previous issue"
  / "Next issue" links (when they exist) and "Back to top". A "Skip to content" link
  appears on keyboard focus. On phones, nav, jump-list and issue-list links are at
  least 44px tall.
- **Link text:** never a raw URL. Bare URLs, `<https://...>` autolinks and links whose
  text is a URL are relabeled with the site name ("Google News" for news.google.com).
- **Editor notes:** amber box at the very top, "Editor notes, not for publishing",
  only when `problems` is non-empty.
- **Footer:** the exact FOOTER text (once per page) plus "Written with AI from the
  linked sources. Every number is pulled automatically from public data. Edited by
  Robert.", small and muted.

## Rules
- No JavaScript. Self-contained except the Google Fonts link and the chart image.
- No eyebrow/kicker labels above headings; no colored border-left/right over 1px;
  no unicode glyphs or emoji as icons. The only icons are the authored inline SVGs
  listed under Icons.
- Light theme only. No animation; `prefers-reduced-motion` turns off any that a
  browser adds.
- HTML comments and stray `{{ }}` are stripped.
- Phones (<600px): sheet goes full width, 16px gutters, body 16px, no horizontal
  scroll (images max-width 100%, long words wrap). Market Summary values stack over
  their change, right-aligned.
- Browser surfaces are themed: `::selection`, `:focus-visible`, underline offset.
