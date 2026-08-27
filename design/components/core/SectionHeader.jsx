import React from 'react';
export function SectionHeader({ title, meta, right, weight = 'light' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, paddingBottom: 10, borderBottom: weight === 'heavy' ? 'var(--ci-rule-heavy)' : 'var(--ci-rule)' }}>
      <span style={{ fontFamily: 'var(--ci-font-text)', fontSize: 10, fontWeight: 600, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--ci-ink)' }}>{title}</span>
      {meta && <span style={{ fontFamily: 'var(--ci-font-text)', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ci-ink-4)' }}>{meta}</span>}
      {right && <span style={{ marginLeft: 'auto' }}>{right}</span>}
    </div>
  );
}
