import React from 'react';
export function StatBlock({ value, label, accent }) {
  return (
    <div style={{ padding: '0 16px', borderLeft: 'var(--ci-rule-light)' }}>
      <div style={{ fontFamily: 'var(--ci-font-display)', fontSize: 28, fontVariantNumeric: 'tabular-nums', color: accent ? 'var(--ci-oxblood)' : 'var(--ci-ink)' }}>{value}</div>
      <div style={{ fontFamily: 'var(--ci-font-text)', fontSize: 9, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--ci-ink-4)', marginTop: 2 }}>{label}</div>
    </div>
  );
}
