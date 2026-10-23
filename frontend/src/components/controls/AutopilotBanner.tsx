/**
 * Autopilot — the two self-trading loops, on the Trading Controls console.
 *
 * Moved off the dashboard 2026-09-09 and restyled onto the console surface the
 * rest of the page uses: mono uppercase for labels, `.nd-switch` for the
 * toggles, `.nd-seg` for the mode picker. It kept its dashboard chrome for a
 * day — rounded icon chips and filled pill buttons — and read as a foreign
 * object bolted onto a page of faders.
 *
 * The lamp on the header is the same one the entry-gate tabs use, and means
 * the same thing: something here is running right now.
 */
import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../../services/api';

/** One loop: name, what it is doing right now, and its switch. */
const APRow: React.FC<{
  title: string; desc: string; on: boolean; busy: boolean;
  onToggle: () => void; first?: boolean;
}> = ({ title, desc, on, busy, onToggle, first }) => (
  <div className="nd-fader-row"
       style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '12px 0',
                borderTop: first ? 'none' : '1px solid var(--nd-border)' }}>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--nd-text-1)' }}>{title}</span>
        {on && <span className="nd-run-tag">running</span>}
      </div>
      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', lineHeight: 1.5, marginTop: 3 }}>{desc}</div>
    </div>
    <button className="nd-switch" role="switch" aria-checked={on} aria-label={title}
      disabled={busy} onClick={onToggle} />
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
  const timing = paper.timingMode ?? 'normal';

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
    <div className="nd-bank" style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '14px 16px', marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 4 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-2)',
                       fontFamily: 'ui-monospace, monospace', letterSpacing: '.12em', textTransform: 'uppercase' }}>
          Autopilot
        </span>
        {anyOn && <i className="nd-lamp" aria-hidden="true" />}
        <span className="nd-live-note">auto-trades the AI watchlist to keep training the agents</span>
      </div>

      <APRow first title="Paper (live)" desc={paperDesc}
        on={!!paper.enabled} busy={busy === 'paper'} onToggle={() => toggle('paper', !paper.enabled)} />

      {/* Entry timing rides under the paper row because it only shapes THAT
          loop's triggers — indented so it reads as a sub-setting, not a peer. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 0 12px 2px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: 'var(--nd-text-3)', fontFamily: 'ui-monospace, monospace',
                       letterSpacing: '.06em', textTransform: 'uppercase' }}>
          Entry timing
        </span>
        <div className="nd-seg" role="group" aria-label="Paper entry timing">
          {(['normal', 'aggressive'] as const).map(m => (
            <button key={m} aria-pressed={timing === m} disabled={busy === 'timing'}
              onClick={() => setPaperTiming(m)}>{m}</button>
          ))}
        </div>
        <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)' }}>
          {timing === 'aggressive' ? 'looser triggers — more trades' : 'standard triggers'}
        </span>
      </div>

      <APRow title="Backtest (1× replay)" desc={btDesc}
        on={!!bt.enabled} busy={busy === 'backtest'} onToggle={() => toggle('backtest', !bt.enabled)} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 0 2px 2px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: 'var(--nd-text-3)', fontFamily: 'ui-monospace, monospace',
                       letterSpacing: '.06em', textTransform: 'uppercase' }}>
          Next trade date
        </span>
        <span className="nd-readout" style={{ width: 'auto', fontSize: 11.5, padding: '3px 9px',
                                              ['--fader' as any]: 'var(--nd-accent)' }}>
          {bt.cursor ?? '—'}
        </span>
        <button className="nd-console-btn" onClick={resetCursor} disabled={busy === 'reset'}
          title="Reset the backtest walk to the last trading day before today">
          {busy === 'reset' ? 'resetting…' : 'reset to last trading day'}
        </button>
      </div>
    </div>
  );
};

export default AutopilotBanner;
