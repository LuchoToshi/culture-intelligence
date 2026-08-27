import React from 'react';
const DOTS = {
  emerging: { bg: 'var(--ci-oxblood)', border: 'var(--ci-oxblood)' },
  strengthening: { bg: 'var(--ci-ink)', border: 'var(--ci-ink)' },
  mainstream: { bg: 'var(--ci-ink-5)', border: 'var(--ci-ink-5)' },
  saturated: { bg: 'transparent', border: 'var(--ci-ink-5)' }
};
export function StageBadge({ stage }) {
  const d = DOTS[stage] || DOTS.mainstream;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontFamily: 'var(--ci-font-text)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--ci-ink-2)' }}>
      <span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%', background: d.bg, border: '1px solid ' + d.border }} />
      {stage}
    </span>
  );
}
