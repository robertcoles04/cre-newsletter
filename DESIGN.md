# Design: CRE Blurb HTML preview

Direction: **Offering Memorandum.** The look of a CRE broker's OM book: credible,
serious, calm, premium. Not playful, not flashy, no emoji. Code: `src/render_html.py`.
Sample (illustrative data): `samples/preview-sample.html`.

## Palette (role: value)
- Ground (page behind the sheet): `#EEF0F2`
- Sheet: `#FFFFFF`, max-width 680px, centered, soft offset shadow
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
  ("Monday, October 5, 2026 • Weekday Edition") in navy tint. A 3px gold rule
  element closes it.
- **Market Summary:** OM "Investment Summary" table built by code from
  `factsheet["values"]`, never model text. Groups Rates / Federal Reserve / REITs;
  rows are label | value + change with hairline dividers. Rows whose key is missing
  are skipped. Caption: "Rates as of {RATES_ASOF} close. Sources: ...". Replaces the
  Markdown `## The Numbers`; only its prose ("What it means") is kept under it,
  then the chart `<img>` with a caption.
- **Change marks:** authored inline SVG triangle + signed text + color. Color is
  never the only signal.
- **n/a:** faded em dash with `title="Data unavailable today"`.
- **Section heading (h2):** serif, navy, more space above than below, 1px gold rule.
- **Story link:** navy, 1px underline, 3px underline-offset. Links are the only
  colored text besides the change marks.
- **Term panel:** navy-wash block, term in serif. No colored side border.
- **Editor notes:** amber box at the very top, "Editor notes, not for publishing",
  only when `problems` is non-empty.
- **Footer:** the exact FOOTER text plus "CRE Blurb · Drafted by pipeline,
  reviewed by Robert", small and muted.

## Rules
- No JavaScript. Self-contained except the Google Fonts link and the chart image.
- No eyebrow/kicker labels above headings; no colored border-left/right over 1px;
  no unicode glyphs or emoji as icons.
- HTML comments and stray `{{ }}` are stripped.
- Phones (<600px): sheet goes full width, 16px gutters, no horizontal scroll.
- Browser surfaces are themed: `::selection`, `:focus-visible`, underline offset.
