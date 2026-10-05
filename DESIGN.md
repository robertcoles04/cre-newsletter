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
  ("Monday, October 5, 2026 • Daily Edition"; Saturday and Sunday say "Weekend
  Edition") in navy tint. A 3px gold rule element closes it.
- **Market Summary:** OM "Investment Summary" table built by code from
  `factsheet["values"]`, never model text. Groups Rates / Federal Reserve / REITs /
  Credit (Rates now include the 2Y, the 10Y-2Y curve and the 30-year mortgage; Credit
  holds the high-yield spread, bank CRE loans and delinquency, and Trepp's CMBS
  delinquency). Weekly/quarterly rows carry their as-of in the label, e.g.
  "30-Year Mortgage (Oct 1)". Rows are label | value + change with hairline dividers.
  Rows whose key is missing are skipped; a row with only a change (CMBS delinquency
  when Trepp states no rate) shows just the change. Caption: "Rates as of
  {RATES_ASOF} close." only (no sources line). Replaces the
  Markdown `## The Numbers`; only its prose ("What it means") is kept under it,
  then the chart `<img>` with a caption.
- **Change marks:** authored inline SVG triangle + signed text + color. Color is
  never the only signal.
- **n/a:** the muted text "n/a" (ghost color) with `title="Data unavailable today"`.
- **No dashes:** no em or en dashes anywhere on the site. Code text uses commas or
  hyphens; model prose is told not to use them and `no_dashes()` replaces any that
  slip through at render time ("a — b" becomes "a, b").
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
