import React from 'react';
export function FilterChip({ label, active, onClick }) {
  return (
    <button onClick={onClick} style={{ fontFamily: 'var(--ci-font-text)', fontSize: 11, letterSpacing: 'var(--ci-track-chip)', textTransform: 'uppercase',
      background: active ? 'var(--ci-ink)' : 'transparent', color: active ? 'var(--ci-paper)' : 'var(--ci-ink-3)',
      border: '1px solid ' + (active ? 'var(--ci-ink)' : 'transparent'), padding: '5px 12px', cursor: 'pointer', transition: 'border-color .15s, color .15s' }}>{label}</button>
  );
}
