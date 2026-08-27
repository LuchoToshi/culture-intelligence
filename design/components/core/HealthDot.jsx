import React from 'react';
const STATES = {
  ok: { bg: 'var(--ci-ink)', border: 'var(--ci-ink)', fg: 'var(--ci-ink-3)' },
  fail: { bg: 'var(--ci-oxblood)', border: 'var(--ci-oxblood)', fg: 'var(--ci-oxblood)' },
  idle: { bg: 'transparent', border: 'var(--ci-ink-5)', fg: 'var(--ci-ink-5)' }
};
export function HealthDot({ state, label }) {
  const s = STATES[state] || STATES.idle;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 8 }}>
      <span style={{ display: 'inline-block', width: 8, height: 8, background: s.bg, border: '1px solid ' + s.border }} />
      {label && <span style={{ fontFamily: 'var(--ci-font-text)', fontSize: 10, letterSpacing: '0.08em', color: s.fg, lineHeight: 1.4 }}>{label}</span>}
    </span>
  );
}
