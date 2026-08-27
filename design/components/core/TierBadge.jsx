import React from 'react';
const TIERS = {
  core: { bg: 'var(--ci-ink)', fg: 'var(--ci-paper)', border: 'var(--ci-ink)' },
  watch: { bg: 'transparent', fg: 'var(--ci-ink)', border: 'var(--ci-ink)' },
  candidate: { bg: 'transparent', fg: 'var(--ci-ink-4)', border: 'var(--ci-line-mid)' },
  dormant: { bg: 'transparent', fg: 'var(--ci-ink-5)', border: 'var(--ci-line-mid)' }
};
export function TierBadge({ tier }) {
  const t = TIERS[tier] || TIERS.candidate;
  return (
    <span style={{ fontFamily: 'var(--ci-font-text)', fontSize: 8, fontWeight: 600, letterSpacing: '0.16em', textTransform: 'uppercase', padding: '2px 7px', border: '1px solid ' + t.border, color: t.fg, background: t.bg }}>{tier}</span>
  );
}
