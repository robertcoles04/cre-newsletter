# Design: CRE Blurb website and HTML preview

Direction: **Broadsheet.** A serious newspaper front page: white paper, black ink, a
navy markets ticker, thick-and-thin rules, small-caps section labels, a 12-column grid on
desktop. Credible and calm, never playful or flashy, no emoji. Code: `src/render_html.py`
(one stylesheet, `CSS`, plus the markup) and `src/site.py` (pages, nav). Approved mockups
(design canvas, Oct 6 2026): `Main.dc.html` (desktop) and `Mobile.dc.html` (phone).
Sample (illustrative data): `samples/preview-sample.html`.

## Palette (role: value)
- Paper (page background): `#FFFFFF`
- Ink (body text, headlines, rules): `#121417`
- Navy (ticker band, links, kicker labels, direction marks, Why-it-matters panel rule): `#0E2A47`
- Navy tint (neutral rate changes on the ticker): `#B9C8DA`
- Gold (active-nav underline, Term panel top rule; rules and borders only): `#A9853A`.
  Gold text (Brief numerals, talking-point numbers "01", "02"): `--gold-text: #806226`
  (passes 4.5:1 on white and on the panel). On the navy ticker the date tag uses the lighter `#C9A85A`.
- Hairline (dividers, column rules): `#DADDE1`
- Muted (hints, captions, source lines, utility row): `#5B6470`; deck text `#3A424C`
- Panel (Brief on phones, Why-it-matters, Term of the Day): `#F3F5F8`; selection `#CCD8E6`
- Up `#1F7A4D`, down `#B23A3A`: prices only (VNQ, REIT movers). On the navy ticker,
  price moves use `#9ED9B6` (up) and `#F2A6A6` (down) so they stay readable.
- Editor notes: amber bg `#FBF4E4`, line `#E3C88A`, ink `#6E4A0B`

## Type
- Self-hosted fonts (SIL Open Font License), latin subset woff2 in `fonts/` with their
  `OFL-*.txt` licenses, copied to `site/fonts/`: Libre Caslon Display 400, Source Serif 4
  (variable 400 to 700 with the opsz axis, plus italic 400), Public Sans (variable 400 to
  700). `@font-face` with `font-display: swap` (`render_html.FONT_FILES`); URLs are
  relative to each page's depth, root-relative `/fonts/` on 404.html. Site pages make no
  Google Fonts request. Only the standalone 5 AM preview (issues/<date>.html, seen only by
  the editor) keeps the Google Fonts css2 link, so it renders anywhere. Fallbacks:
  Georgia / Times New Roman for the serifs, -apple-system / Segoe UI / Helvetica / Arial
  for Public Sans.
- **Libre Caslon Display:** the masthead "CRE Blurb" (76px desktop, 48px phone, clamped),
  large section titles (The Numbers, Coffee chat: 34px / 28px), the term name (32px /
  26px), Brief numerals, page titles (44px / 34px), the footer name.
- **Source Serif 4:** headlines and body. Lead headline 700, 44px (30px phone); grid
  story headlines 24px (21px); deck 21px (17px); body 17px (16px), line-height about 1.55.
- **Public Sans:** labels, data and UI: section labels, nav, ticker, rows, hints,
  captions, source lines, footer. Numbers use `font-variant-numeric: tabular-nums`.
- **Section label:** 13px (12px phone), 700, uppercase, letter-spacing .14em, ink, over
  a 1px ink rule (2px for The Brief and Market Snapshot). Kicker ("Top Story"): 12px,
  700, uppercase, .12em, navy.

## Masthead (every page: issues, home, archive, about, glossary, 404)
- **Utility row** (12px, muted, hairline below): date, "Daily Edition" or "Weekend
  Edition" plus "N min read" (navy, uppercase), and "Free. New issue every weekday
  morning, lighter on weekends." Read time = words of the rendered body / 230, rounded
  half up, min 1. Phones hide this row and show "Tuesday, October 6, 2026 · Daily Edition"
  in small caps above the name instead.
- **Ticker:** the navy band, code-filled from the issue's `factsheet["values"]`, never
  hand-written. Items appear only when the value exists and is not n/a, in this order:
  date tag ("OCT 5 CLOSE" from RATES_ASOF), 10-Yr, 5-Yr, 2-Yr, SOFR, Fed funds (target
  range, no change), 30-Yr Mortgage, REITs (VNQ), biggest gain and biggest drop tickers,
  Fed hold odds, High-yield spread, CMBS delinquency. Rate changes are neutral (authored
  SVG triangle + magnitude in navy tint; "0 bps" without an arrow; "1 bps" reads "1 bp");
  price changes are light green / light red. Pure CSS: three copies of the items in an
  inline-flex track, `translateX(-33.3333%)`, linear infinite, duration `--n` (item
  count) x 4.6s on desktop (about 60s) and x 2.5s on phones (about 32s), paused on hover.
  **Pause/Play control** (WCAG 2.2.2, no JavaScript, `render_html.ticker_band`): a real
  checkbox `#ticker-pause` (class `visually-hidden-but-focusable`) and its label styled
  as a button at the band's right end (Public Sans 11px 700 uppercase, white on navy, a
  1px navy-tint rule on its left, a small SVG pause or play icon, visually hidden
  " markets ticker" for screen readers). Checked: the track gets
  `animation-play-state: paused` and the label reads "Play". Keyboard focus shows a 2px
  white ring inside the label; phones give it a 44px tap target. It sits outside the
  aria-hidden track. Under `prefers-reduced-motion` there is no animation, the control is
  hidden, and the first copy of the items wraps as plain rows (`overflow: visible`,
  `flex-wrap: wrap`); copies 2 and 3 carry class `tk-copy` and are hidden. No scroll
  area, so nothing a keyboard cannot reach. The track is `aria-hidden`; the band has `role="img"` and an
  `aria-label` that reads every value once in words ("Markets as of Oct 5 close: 10-Year
  5.31% up 3 bps, ..."). Non-issue pages show the latest published issue's ticker and date.
- **Name block:** centered "CRE Blurb" (on issue pages and the home page it is the
  page's only `<h1 class="name">`, with the full date in an `.sr-only` span; other pages
  keep a `<p>` and have their own h1; a "# " title in issue Markdown becomes h2), the tagline in tracked small caps ("The daily
  commercial real estate briefing for students and professionals"; phones: "Daily
  CRE briefing for students"), then the thick-and-thin double rule (3px + 1px ink).
- **Nav (site):** centered, Public Sans 13px 600 uppercase: Today, Markets (the latest
  issue's `#numbers`; on the home page just `#numbers`), Archive, Glossary, About.
  `aria-current="page"` on the current one with a 2px gold underline (Today is current on
  the home page and on the latest issue's own page). Phones: one row,
  spaced edge to edge, every link 44px tall. A "Skip to content" link shows on focus.

## Issue page layout
Desktop is a 1180px container (24px gutters) on a 12-column grid from 900px up; below
900px everything stacks in one column (16px gutters under 600px). One renderer for the
site and the 5 AM preview.
1. **Front row:** the first Top Story as the lead (8 columns, hairline on its right):
   kicker "Top Story", headline linked to its source, deck (the summary), "Source: X"
   when the source link ends the story, and a "Why it matters" panel (navy top rule) that
   sits at the bottom of the column, so the lead and The Brief end at about the same
   height. Beside it (4 columns): The Brief, numbered 1 to 3 with gold Caslon numerals and
   hairline dividers. DOM (and focus) order is Brief, Snapshot, lead, the phone order;
   from 900px the grid places them (lead `1 / span 8` row 1, Brief `9 / span 4` row 1,
   Snapshot row 2), no CSS `order`. Then the **Market Snapshot** as a full-width strip: three equal
   cells (10-Year, SOFR, VNQ) split by vertical hairlines, each with label, big value,
   change and hint, a 2px label rule above and a 1px ink rule below, then "Rates as of
   Oct 5 close. Full market data" (links to `#numbers`). Phones: Brief (panel with a navy
   top rule), Snapshot as rows, then the lead.
2. **Top Stories:** the remaining stories in a grid with vertical hairlines: 1 story = 1
   column, 2 or 4 = 2 columns, 3 or more otherwise = 3 columns (a last story alone in its
   row spans two). Each: headline (linked), summary, "Why it matters:" line, source.
   No kicker unless it can be derived reliably (today it cannot), so none is shown.
3. **Coffee chat band** (3px ink rule above, 1px below): title + note in 4 columns, the
   numbered (01, 02, 03 in gold) serif italic points in 8. Handles the new
   `**Coffee chat talking points:**` list and the older single `**Coffee chat line:**`.
4. **Debt Markets** (7 columns) beside **Market Watch** (5; region h3s keep their icons).
5. **Quick Hits** as a 3-column list. Then any other section (AI in Real Estate, Deals of
   the Week, Week in Review, Market Spotlight, REIT Weekly, Week Ahead, Careers Corner)
   full width, its body in two newspaper columns with a hairline rule.
6. **The Numbers** (`id="numbers"`, Caslon title, 3px ink rule above): the full Market
   Summary. Left 7 columns: Rates rows, "Rates as of X close.", the 10-Year chart, the
   yield curve + mortgage pair. Right 5: Federal Reserve rows + Fed odds chart, REITs
   rows + scoreboard. Then the prose ("What it means", "Why X moved") in two columns.
7. **Term of the Day** (panel, 2px gold top rule, term in Caslon) beside the **Data Room**.
8. The site adds "New issue every morning at creblurb.org.", the Recent issues list
   (home, from 3 issues), Previous / Next issue and Back to top.
- **No empty cells, no tall blanks:** when a section is missing its neighbor spans the
  full width (`.pair > :only-child`, `front no-lead`, `front no-brief`). When one side of
  a pair has more than twice the other's words, both go full width one after another
  (`render_html._pair`, class `solo`). The Term panel stretches to its row's height.
- **Jump list ("In this issue"):** kept on phones and tablets under the front (3+ links,
  anchors to every later section), hidden on desktop where the grid does that job.

## Components
- **Data rows** (Snapshot, The Numbers, Data Room): label + muted hint | value + change,
  11px padding, hairline dividers, Public Sans. Rows are built by code from
  `factsheet["values"]` (`SUMMARY_GROUPS`), never model text; missing keys are skipped;
  a row with only a change shows just the change. Rates rows whose date differs from
  RATES_ASOF add "(as of Oct 2)" to the hint. Every row has a hint (`HINTS`).
- **Change marks:** authored inline SVG triangle + signed text. Color = good/bad only for
  prices (VNQ, REIT movers, scoreboard chart): green up, red down. Rates, Federal Reserve
  and Data Room marks are navy (class `chg rate`). Color is never the only signal. A
  change that rounds to zero has no sign.
- **n/a:** muted italic "n/a" plus `.sr-only` " (data unavailable today)" (no title or
  aria-label). When
  the next FOMC date is known the three Fed odds rows always show.
- **Data Room:** slower-moving credit rows (high-yield spread, bank CRE loans and
  delinquency, Trepp CMBS delinquency). Rows dated within 7 days get a 13px uppercase
  "Updated" tag and a navy change mark; older rows show their change muted, no arrow.
- **Charts:** made by our own code (`src/chart.py`, matplotlib), never photos. Navy line,
  gold dot on the latest value, navy tint comparison lines, up/down bars, hairline grid,
  no titles, legend boxes or 3D; captions are fixed text (`CHART_CAPTIONS`) with a
  hairline above, alt text code-filled with the values. Five optional charts (10-Year,
  yield curve, 30-year mortgage, Fed odds, REIT scoreboard); full-width ones have a
  `-sm` phone version through `<picture>`. Only the first image is eager. og:image uses
  the 10-Year chart.
- **"Why it matters:"** in grid stories and sections: class `why`, muted bold lead-in.
- **Link text:** never a raw URL. Bare URLs, autolinks and URL-text links are relabeled
  with the site name ("Google News" for news.google.com). Links: navy, 1px underline at
  35% opacity, 3px offset; headline links are ink with no underline until hover.
- **Icons:** small authored inline SVGs (`ICON_PATHS`), 18px, navy, `aria-hidden`, only
  on the Market Watch region h3s (sun, waves, globe). Section labels have no icons.
- **Editor notes:** amber box at the top of main, "Editor notes, not for publishing",
  only when `problems` is non-empty (5 AM preview only).
- **Footer:** 3px ink rule, "CRE Blurb" in Caslon, "Written with AI from the linked
  sources. Every number is pulled automatically from public data. Reviewed by the
  editor.", the exact FOOTER text once per page, then a quiet full-width row of muted
  links: About · Privacy · Terms · Accessibility (hairline middots, 44px tall on phones).
  Links are relative to the page's depth; 404.html and the standalone preview use
  absolute https://creblurb.org/ links. The site never names the publisher; the one
  contact address is corrections@creblurb.org (`site.CONTACT_EMAIL`).
- **Favicon:** `/favicon.svg`, a navy square with a gold serif "CB". The site also writes
  `robots.txt` and `sitemap.xml` (home, archive, glossary, about, privacy, terms,
  accessibility, every issue) and the RSS `feed.xml`.

## Other pages
- **Archive:** month labels; each issue is a newspaper row: the date (link) in Public
  Sans, then that issue's top headline in Source Serif 600 (first Top Stories `###`,
  else the describe() sentence). Two columns on desktop, stacked on phones.
- **Glossary** (`/glossary/`): every Term of the Day from published issues ("## Term of
  the Day" opening with `**Term:**`), alphabetical; the term in Caslon, its definition,
  and "From the issue of" with each issue date linked. A term that ran twice keeps its
  newest definition and lists both dates.
- **About**, **Privacy** (`/privacy/`), **Terms** (`/terms/`), **Accessibility**
  (`/accessibility/`) and **404:** the same masthead and a centered 820px text column.
  The three policy pages open with "Last updated: <date>" and are plain-English, short
  (`site.INFO_PAGES`).

## Rules
- No JavaScript. Self-contained except the chart images and the self-hosted fonts
  (the preview alone links Google Fonts).
- No gradients, emoji or rounded cards; no unicode glyphs as icons (triangles are SVG).
  No colored border-left/right over 1px.
- No em or en dashes anywhere. Code text uses commas or hyphens; `no_dashes()` replaces
  any in model prose at render time ("a, b").
- Light theme only. The ticker is the only animation; `prefers-reduced-motion` stops it
  (and any other animation a browser adds).
- HTML comments and stray `{{ }}` are stripped.
- Phones (<600px): single column, 16px gutters, no horizontal scroll (images max-width
  100%, long words wrap, the ticker clips), nav / jump-list / issue links at least 44px.
- Browser surfaces are themed: `::selection`, `:focus-visible`, underline offset.
- Accessibility (WCAG 2.1 AA, checked with axe-core at 1280px, 375px and reduced motion:
  0 violations): Market Summary group names are `<h3 class="group-name">`; list-style-none
  lists (.brief ol, .points, .quick-hits ul, .toc ul, .issue-list) carry `role="list"`;
  a chart with no recorded alt gets one from the fact sheet ("...; latest 5.31% as of
  Oct 5", `render_html.default_alt`); each "Source:" link in Top Stories ends with an
  `.sr-only` ": <headline>". `.sr-only` is the shared visually hidden utility.
