import React from 'react';

interface RefreshControlProps {
  sweeping: boolean;
  lastSweep: any;
  runSweep: () => void;
  seeding: boolean;
  seedMsg: string;
  runSeed: () => void;
}

const RefreshControl: React.FC<RefreshControlProps> = ({ sweeping, lastSweep, runSweep, seeding, seedMsg, runSeed }) => {
  return (
    <div className="nd-pm-card">
      {/* Title row + button */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 0 }}>
        <div style={{ flex: 1, minWidth: 180 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
            <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-2)' }}>autorenew</span>
            <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Refresh from latest data</span>
          </div>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
            Replays real backtests and rebuilds the bank from the freshest candles.
            Runs <strong style={{ color: 'var(--nd-text-2)' }}>automatically every night (~02:00 IST)</strong> — or trigger it now.
          </p>
        </div>
        <button
          className="nd-btn nd-btn-primary"
          onClick={runSweep}
          disabled={sweeping}
          style={{ borderRadius: 9, padding: '10px 18px', fontSize: 13, fontWeight: 600, gap: 7, flexShrink: 0 }}
        >
          <span className="material-icons" style={{ fontSize: 15, animation: sweeping ? 'nd-spin 0.9s linear infinite' : 'none' }}>
            refresh
          </span>
          {sweeping ? 'Refreshing…' : 'Refresh Now'}
        </button>
      </div>

      {sweeping && (
        <div style={{
          marginTop: 12, display: 'flex', alignItems: 'center', gap: 8,
          fontSize: 12, color: 'var(--nd-text-2)',
          background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
          borderRadius: 8, padding: '10px 12px',
        }}>
          <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-green)', animation: 'nd-spin 0.9s linear infinite' }}>autorenew</span>
          Running real backtests across the watchlist — this takes a minute or two…
        </div>
      )}

      {!sweeping && lastSweep && (
        <div style={{
          marginTop: 12,
          display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap',
          fontSize: 11, color: 'var(--nd-text-3)',
          background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
          borderRadius: 8, padding: '8px 12px',
        }}>
          <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-green)' }}>check_circle</span>
          Last refresh:
          <span style={{ color: 'var(--nd-text-2)' }}>{new Date(lastSweep.finishedAt).toLocaleString()}</span>
          <span style={{ color: 'var(--nd-border)' }}>·</span>
          {(lastSweep.casesInserted ?? 0).toLocaleString()} cases
          <span style={{ color: 'var(--nd-border)' }}>·</span>
          {lastSweep.backtestsOk ?? 0} backtests
          {lastSweep.durationSecs != null && <>{' · '}{lastSweep.durationSecs}s</>}
        </div>
      )}

      {/* Dense seed — secondary action */}
      <div style={{
        marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--nd-border)',
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
      }}>
        <button
          className="nd-btn nd-btn-outline"
          onClick={runSeed}
          disabled={seeding}
          style={{ fontSize: 12, padding: '7px 14px', borderRadius: 8, gap: 6 }}
        >
          <span className="material-icons" style={{ fontSize: 14, animation: seeding ? 'nd-spin 0.9s linear infinite' : 'none' }}>
            {seeding ? 'autorenew' : 'download'}
          </span>
          {seeding ? 'Seeding…' : 'Dense seed (forward-return labels)'}
        </button>
        {seedMsg && (
          <span style={{
            fontSize: 11,
            color: seedMsg.startsWith('✓') ? 'var(--nd-green)'
                 : seedMsg.startsWith('✗') ? '#e74c3c'
                 : 'var(--nd-text-3)',
          }}>{seedMsg}</span>
        )}
      </div>
    </div>
  );
};

export default RefreshControl;
