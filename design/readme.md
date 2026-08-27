# Culture Intelligence — Design System

Fashion-first urban taste intelligence. The UI is an intelligence briefing crossed with a fashion editorial — "the briefing room" — never a trend dashboard. Audience: brand strategists, buyers, trend forecasters (they read i-D and 032c, not Tableau). Credibility comes from restraint and density.

Sources: designed in Claude Design from `uploads/claude-design-brief.md`; reference screens live in this project as `*.dc.html` (Signal Registry, Signal Detail, Weekly Report, Source Universe, City Intelligence).

## Content fundamentals
- Matter-of-fact analyst voice; declarative sentences; no exclamation, no emoji, ever.
- Failures and gaps are stated plainly with mechanism and date: "FAILING · feed truncated to 3 items since W33".
- Weeks are the clock: W21, W35. Counts are honest ("11 of 14 reported"); silence is reported, never hidden ("No new content found during this period." — italic, gray).
- Editorial attention ≠ adoption. Copy never blends them; divergence is called "hype risk, verify before acting".
- System-generated analysis is labeled ("Synthesis · read as analysis", "Model-assisted", "pending review").

## Visual foundations
- Palette: warm paper `#FAFAF7`, near-black ink `#111`, four grays, hairlines `#E4E2DA`/`#C9C7BF`. ONE accent: oxblood `#6B1D26`, used only for signal state and emphasis (emerging dots, divergence daggers, failures, alert bands). No gradients, no glassmorphism, no shadows, no rounded cards — square corners everywhere except 7px stage dots.
- Type: Newsreader (serif — headlines, signal names, prose, big stats) + Archivo (grotesk — labels, data, tables, UI). Big serif vs small dense data type; uppercase letterspaced labels (0.10–0.22em tracking); tabular numerals throughout.
- Layout: column-driven broadsheet grids, hairline rules as structure (2px for section breaks, 1px mid, 1px light for rows). Whitespace is vertical rhythm, not padding bloat. Page: 1440px max, 48px side padding.
- Data display: typographic first — five-cell meters, counts, small-caps stages — before any chart. The only "chart" is the stacked-square evidence timeline (outline = editorial, solid = adoption).
- Interaction: hover = paper tint `#F2F0E8` or ink-darkening; transitions 150ms; one-time load animations only where motion encodes meaning (meter fill, timeline left-to-right); `prefers-reduced-motion` disables everything. Nothing pulses, bounces, or glows.
- Links: ink, oxblood on hover.

## Iconography
None. No icon font, no SVGs, no emoji. Unicode glyphs serve as marks: † divergence, ●/○ pin & watch states, ▾/▴ sort, ⌘K, · separators, − / + disclosure. Keep it that way.

## Index
- `styles.css` → `tokens/` (fonts, colors, typography, spacing)
- `components/core/` — ScoreMeter, StageBadge, TierBadge, HealthDot, SectionHeader, Masthead, FilterChip, StatBlock, AlertBand, SignalRow (each: .jsx + .d.ts + .prompt.md)
- `guidelines/` — foundation specimen cards
- Reference screens: `Signal Registry.dc.html`, `Signal Detail.dc.html`, `Weekly Report.dc.html`, `Source Universe.dc.html`, `City Intelligence.dc.html`
- `SKILL.md` — agent skill entry point

## Intentional additions
- Masthead, FilterChip, StatBlock, AlertBand: not named in the brief's extraction list but recur on every screen.

## Notes
- No logo was provided; the wordmark is plain letterspaced Archivo. Do not invent a mark.
- Fonts load from Google Fonts (`tokens/fonts.css`); no font binaries were provided.
