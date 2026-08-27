import React from 'react';
export function AlertBand({ label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, padding: '12px 0', borderBottom: 'var(--ci-rule-light)', fontFamily: 'var(--ci-font-text)' }}>
      <span style={{ display: 'inline-block', width: 7, height: 7, background: 'var(--ci-oxblood)', alignSelf: 'center' }} />
      <span style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--ci-oxblood)', whiteSpace: 'nowrap' }}>{label}</span>
      <span style={{ fontSize: 11, color: 'var(--ci-ink-3)', letterSpacing: '0.02em' }}>{children}</span>
    </div>
  );
}
