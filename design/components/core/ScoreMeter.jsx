import React from 'react';
export function ScoreMeter({ value, max = 5, accent = false, showNumeral = true, cellWidth = 12 }) {
  const hot = accent && value >= 4;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 8 }}>
      <span style={{ display: 'inline-flex', gap: 3 }}>
        {Array.from({ length: max }, (_, i) => (
          <span key={i} style={{ display: 'inline-block', width: cellWidth, height: 5,
            background: i < value ? (hot ? 'var(--ci-oxblood)' : 'var(--ci-ink)') : 'transparent',
            border: '1px solid ' + (hot && i < value ? 'var(--ci-oxblood)' : 'var(--ci-ink)') }} />
        ))}
      </span>
      {showNumeral && <span style={{ fontFamily: 'var(--ci-font-text)', fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--ci-ink-4)' }}>{value}/{max}</span>}
    </span>
  );
}
