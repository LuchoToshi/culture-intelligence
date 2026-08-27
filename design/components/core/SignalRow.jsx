import React from 'react';
import { StageBadge } from './StageBadge.jsx';
import { ScoreMeter } from './ScoreMeter.jsx';
export function SignalRow({ signal, href = '#', onPin, pinned, dense = true }) {
  const s = signal;
  const flag = s.edi - s.ado >= 2;
  return (
    <a href={href} style={{ textDecoration: 'none', color: 'inherit', display: 'grid',
      gridTemplateColumns: '18px minmax(260px,1.4fr) 140px 64px minmax(150px,0.9fr) 108px 108px 108px 70px',
      gap: '0 18px', alignItems: 'baseline', padding: (dense ? 12 : 20) + 'px 0',
      borderBottom: 'var(--ci-rule-light)', cursor: 'pointer', fontFamily: 'var(--ci-font-text)' }}>
      {onPin
        ? <button onClick={e => { e.preventDefault(); onPin(s); }} style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: 11, color: pinned ? 'var(--ci-oxblood)' : 'var(--ci-line-mid)', alignSelf: 'center' }}>{pinned ? '●' : '○'}</button>
        : <span />}
      <span style={{ fontFamily: 'var(--ci-font-display)', fontSize: 'var(--ci-size-row)', lineHeight: 1.15, color: 'var(--ci-ink)' }}>
        {s.name} {flag && <span style={{ color: 'var(--ci-oxblood)', fontSize: 14 }}>†</span>}
      </span>
      <StageBadge stage={s.stage} />
      <span style={{ textAlign: 'right', fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>{s.evidence}</span>
      <span style={{ fontSize: 11, color: 'var(--ci-ink-3)', lineHeight: 1.5 }}>{s.cities.join(' · ')}</span>
      <ScoreMeter value={s.edi} />
      <ScoreMeter value={s.ado} />
      <ScoreMeter value={s.sat} accent />
      <span style={{ textAlign: 'right', fontSize: 11, color: 'var(--ci-ink-3)', fontVariantNumeric: 'tabular-nums' }}>{s.span}</span>
    </a>
  );
}
