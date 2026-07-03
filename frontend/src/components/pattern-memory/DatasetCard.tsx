import React from 'react';

interface DatasetCardProps {
  dataset: { summary: any; rows: any[] } | null;
  loadDataset: () => void;
}

const DatasetCard: React.FC<DatasetCardProps> = ({ dataset, loadDataset }) => {
  if (!dataset || (dataset.summary?.days ?? 0) < 0) return null;

  return (
    <div className="nd-pm-card" style={{ borderLeft: '3px solid #0ea5e9' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <span className="material-icons" style={{ fontSize: 18, color: '#0ea5e9' }}>dataset</span>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>1-Second Dataset</h3>
        <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
          real tick data captured live from the Groww stream — backtests/replays read this first
        </span>
        <button onClick={loadDataset} title="Refresh" style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-3)', display: 'flex', padding: 2 }}>
          <span className="material-icons" style={{ fontSize: 15 }}>refresh</span>
        </button>
      </div>
      <div className="nd-pm-stats-grid">
        {[
          { label: 'Symbols',     value: (dataset.summary?.symbols ?? 0).toLocaleString() },
          { label: 'Days stored', value: (dataset.summary?.days ?? 0).toLocaleString() },
          { label: 'Total ticks', value: (dataset.summary?.totalTicks ?? 0).toLocaleString() },
          { label: 'Size',        value: `${(((dataset.summary?.totalBytes ?? 0) / 1024 / 1024)).toFixed(1)} MB` },
        ].map(s => (
          <div key={s.label} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '12px 14px' }}>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--nd-text-3)', marginBottom: 6 }}>{s.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.value}</div>
          </div>
        ))}
      </div>
      {(dataset.rows?.length ?? 0) === 0 ? (
        <p style={{ margin: '12px 0 0', fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
          Empty so far — the dataset grows automatically as live/paper sessions run during market hours
          (1-second resolution). It's then resampled to any bar size for backtests and replays.
        </p>
      ) : (
        <div style={{ marginTop: 12, maxHeight: 160, overflowY: 'auto' }}>
          {dataset.rows.slice(0, 30).map((r: any, i: number) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: i < Math.min(30, dataset.rows.length) - 1 ? '1px solid var(--nd-border)' : 'none', fontSize: 11 }}>
              <span style={{ fontWeight: 600, color: 'var(--nd-text-1)', minWidth: 90 }}>{r.symbol}</span>
              <span style={{ color: 'var(--nd-text-3)' }}>{r.date}</span>
              <span style={{ marginLeft: 'auto', color: 'var(--nd-text-2)' }}>{(r.ticks ?? 0).toLocaleString()} ticks</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default DatasetCard;
