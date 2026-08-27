import React from 'react';
export function Masthead({ items = [], active, onSearch }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '28px 0 14px 0', borderBottom: 'var(--ci-rule-heavy)', fontFamily: 'var(--ci-font-text)' }}>
      <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: 'var(--ci-track-brand)', textTransform: 'uppercase', color: 'var(--ci-ink)' }}>Culture Intelligence</div>
      <div style={{ display: 'flex', gap: 32, fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase', alignItems: 'baseline' }}>
        {items.map(it => it.label === active
          ? <span key={it.label} style={{ color: 'var(--ci-ink)', borderBottom: '1px solid var(--ci-ink)', paddingBottom: 2 }}>{it.label}</span>
          : <a key={it.label} href={it.href} style={{ color: 'var(--ci-ink-3)', textDecoration: 'none' }}>{it.label}</a>)}
        {onSearch && <button onClick={onSearch} style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', font: 'inherit', letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ci-ink-5)' }}>Search ⌘K</button>}
      </div>
    </div>
  );
}
