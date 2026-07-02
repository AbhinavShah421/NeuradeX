import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../../services/api';

const APToggle: React.FC<{ on: boolean; busy: boolean; onClick: () => void }> = ({ on, busy, onClick }) => (
  <button onClick={onClick} disabled={busy}
    style={{ width: 46, height: 26, borderRadius: 14, border: 'none', cursor: busy ? 'wait' : 'pointer', position: 'relative', background: on ? 'var(--nd-green)' : 'var(--nd-border)', transition: 'background 0.2s', flexShrink: 0 }}>
    <span style={{ position: 'absolute', top: 3, left: on ? 23 : 3, width: 20, height: 20, borderRadius: '50%', background: '#fff', transition: 'left 0.2s', boxShadow: '0 1px 3px #0003' }} />
  </button>
);

const APRow: React.FC<{ icon: string; title: string; desc: string; on: boolean; busy: boolean; onToggle: () => void; first?: boolean }> =
  ({ icon, title, desc, on, busy, onToggle, first }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0', borderTop: first ? 'none' : '1px solid var(--nd-border)' }}>
    <span className="material-icons" style={{ fontSize: 18, color: on ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>{icon}</span>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)', display: 'flex', alignItems: 'center', gap: 6 }}>
        {title}{on && <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--nd-green)', border: '1px solid var(--nd-green)', borderRadius: 4, padding: '0 4px' }}>ON</span>}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{desc}</div>
    </div>
    <APToggle on={on} busy={busy} onClick={onToggle} />
  </div>
);

const AutopilotBanner: React.FC = () => {
  const [ap, setAp] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { const r = await apiService.getAutopilot(); setAp((r as any).data); } catch {}
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);
  const toggle = async (mode: 'paper' | 'backtest', next: boolean) => {
    setBusy(mode);
    try { const r = await apiService.setAutopilot(next, mode); setAp((r as any).data); } catch {} finally { setBusy(null); }
  };
  const resetCursor = async () => {
    setBusy('reset');
    try { const r = await apiService.resetBacktestCursor(); setAp((r as any).data); } catch {} finally { setBusy(null); }
  };
  const setPaperTiming = async (mode: 'normal' | 'aggressive') => {
    setBusy('timing');
    try { const r = await apiService.setAutopilotPaperTiming(mode); setAp((r as any).data); } catch {} finally { setBusy(null); }
  };
  if (!ap) return null;
  const paper = ap.paper ?? {};
  const bt = ap.backtest ?? {};
  const anyOn = paper.enabled || bt.enabled;

  const paperDesc = paper.enabled
    ? (paper.marketOpen
        ? `Paper-trading ${paper.running ?? 0} of ${paper.watchlistSize ?? 0} watchlist stocks live`
        : `Market closed — will paper-trade all ${paper.watchlistSize ?? 0} watchlist stocks at open`)
    : 'Live paper-trade the whole watchlist during market hours';

  const btDesc = bt.enabled
    ? (bt.activeWindow === false
        ? `Paused for paper-trading hours — resumes after close · ${bt.completedDays ?? 0} days trained`
        : (bt.running ?? 0) > 0
          ? `Replaying ${bt.queueDate ?? bt.cursor} at ${bt.speed ?? 1}× · ${bt.queuePending ?? 0}/${bt.queueTotal ?? 0} sessions left · ${bt.completedDays ?? 0} days trained`
          : `Next day: ${bt.cursor ?? '—'} · ${bt.completedDays ?? 0} days trained so far`)
    : 'Replays past days (walking back) outside market hours to train on dense real data';

  return (
    <div className="nd-card" style={{ padding: '16px 18px', marginBottom: 20, borderLeft: `3px solid ${anyOn ? 'var(--nd-green)' : 'var(--nd-border)'}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
        <div className="nd-icon-chip" style={{ background: anyOn ? 'var(--nd-green-50)' : 'var(--nd-surface)' }}>
          <span className="material-icons" style={{ color: anyOn ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>smart_toy</span>
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Autopilot</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Auto-trades the AI watchlist to keep training the agents</div>
        </div>
      </div>
      <APRow first icon="sync" title="Paper (live)" desc={paperDesc}
        on={!!paper.enabled} busy={busy === 'paper'} onToggle={() => toggle('paper', !paper.enabled)} />

      {/* Paper entry-timing mode */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0 8px 30px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>Entry timing</span>
        <div style={{ display: 'flex', gap: 2, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: 2 }}>
          {(['normal', 'aggressive'] as const).map(m => {
            const active = (paper.timingMode ?? 'normal') === m;
            return (
              <button key={m} onClick={() => setPaperTiming(m)} disabled={busy === 'timing'}
                style={{ padding: '3px 12px', borderRadius: 6, border: 'none', cursor: busy === 'timing' ? 'wait' : 'pointer', fontSize: 11, fontWeight: 600, textTransform: 'capitalize',
                  background: active ? (m === 'aggressive' ? '#f59e0b' : 'var(--nd-green)') : 'transparent',
                  color: active ? '#fff' : 'var(--nd-text-2)' }}>
                {m}
              </button>
            );
          })}
        </div>
        <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)' }}>
          {(paper.timingMode ?? 'normal') === 'aggressive' ? 'looser triggers — more trades' : 'standard triggers'}
        </span>
      </div>

      <APRow icon="history" title="Backtest (1× replay)" desc={btDesc}
        on={!!bt.enabled} busy={busy === 'backtest'} onToggle={() => toggle('backtest', !bt.enabled)} />
      {/* Next trade date + reset */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0 0 30px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>
          Next trade date:&nbsp;
          <strong style={{ color: 'var(--nd-text-2)', fontFamily: 'monospace' }}>{bt.cursor ?? '—'}</strong>
        </span>
        <button onClick={resetCursor} disabled={busy === 'reset'}
          title="Reset the backtest walk to the last trading day before today"
          style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 6,
            border: '1px solid var(--nd-border)', background: 'var(--nd-surface)',
            color: 'var(--nd-text-2)', cursor: busy === 'reset' ? 'wait' : 'pointer', fontSize: 11, fontWeight: 600 }}>
          <span className="material-icons" style={{ fontSize: 13 }}>{busy === 'reset' ? 'hourglass_top' : 'restart_alt'}</span>
          {busy === 'reset' ? 'Resetting…' : 'Reset to last trading day'}
        </button>
      </div>
    </div>
  );
};

export default AutopilotBanner;
