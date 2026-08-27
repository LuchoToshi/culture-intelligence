# Design source of truth (repo note)

This directory is the Culture Intelligence design system as exported from Claude Design
("Signal Registry Review" project). It is the **design source of truth** — see
[readme.md](readme.md) for the brand rules.

- `tokens/` — the canonical `--ci-*` CSS custom properties.
- `components/core/` — the 10 extracted components as React `.jsx` + `.d.ts` + `.prompt.md`.
  The product does **not** run React; these are the visual/API reference the Jinja2 macros
  in `src/culture/web/templates/_macros.html` implement, and the future `/design-sync` input.
- `guidelines/` — foundation specimen cards.
- `reference/` — the five approved screens (`*.dc.html`, rendered by `support.js`).
  Open them in a browser as the fidelity reference; they are Claude Design artboards with
  demo data, not production markup.

**Runtime copy:** the tokens are consumed by the app via
`src/culture/web/templates/_tokens.css` (concatenated from `tokens/`, plus the
reduced-motion guard). If tokens change here, regenerate that file — and vice versa: any
token change made during implementation must be mirrored back into `tokens/` so a future
Claude Design sync sees the same values.
