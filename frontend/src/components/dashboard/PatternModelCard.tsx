import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../../services/api';

// ── Pattern Recognition Model (dedicated, continuously-learning) ───────────────

const PatternModelCard: React.FC<{ embedded?: boolean }> = ({ embedded }) => {
  const [status, setStatus] = useState<any>(null);
  const [curve, setCurve] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setStatus(await apiService.patternModelStatus()); } catch {}
    try { const c = await apiService.patternModelCurve(); setCurve((c as any).data?.points ?? []); } catch {}
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const train = async () => {
    setBusy(true); setMsg(null);
    try { await apiService.trainPatternModel({ lookbackDays: 365, horizon: 3 }); setMsg('Training started — patterns only. Accuracy updates as it learns.'); }
    catch { setMsg('Could not start training.'); }
    setTimeout(() => { setBusy(false); load(); }, 2500);
  };

  const m = status?.model ?? {};
  const recent = m.recentAccuracy != null ? m.recentAccuracy * 100 : null;
  const lifetime = m.lifetimeAccuracy != null ? m.lifetimeAccuracy * 100 : null;
  const hcAcc = m.highConfAccuracy != null ? m.highConfAccuracy * 100 : null;
  const hcCov = m.highConfCoverage != null ? m.highConfCoverage * 100 : null;
  const slice = status?.lastTrain?.universeSlice;
  const running = status?.running;

  // sparkline of batch (generalisation) accuracy over training snapshots
  const pts = curve.filter(p => p.batchAccuracy != null);
  const W = 560, H = 90, PL = 30, PR = 8, PT = 8, PB = 14;
  const ys = pts.map(p => p.batchAccuracy * 100);
  const yMin = pts.length ? Math.max(0, Math.min(...ys) - 5) : 40;
  const yMax = pts.length ? Math.min(100, Math.max(...ys) + 5) : 60;
  const sx = (i: number) => PL + (pts.length <= 1 ? 0.5 : i / (pts.length - 1)) * (W - PL - PR);
  const sy = (v: number) => PT + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PT - PB);
  const line = pts.map((p, i) => `${sx(i).toFixed(1)},${sy(p.batchAccuracy * 100).toFixed(1)}`).join(' ');

  return (
    <div className={embedded ? undefined : 'nd-card'} style={{ padding: '16px 18px', marginBottom: embedded ? 0 : 20 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Pattern Recognition Model</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
            Learns price <span style={{ color: '#06b6d4' }}>patterns only</span> across the full NSE universe. <span style={{ color: '#a855f7' }}>High-confidence</span> = accuracy when the model is sure (it abstains otherwise)
          </div>
        </div>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#a855f7' }}>{hcAcc != null ? `${hcAcc.toFixed(1)}%` : '—'}</div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>high-confidence{hcCov != null ? ` · ${hcCov.toFixed(0)}% of picks` : ''}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#06b6d4' }}>{recent != null ? `${recent.toFixed(1)}%` : '—'}</div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>overall · {lifetime != null ? `${lifetime.toFixed(0)}% life` : 'untrained'}</div>
          </div>
          <button onClick={train} disabled={busy || running} style={{
            padding: '6px 12px', fontSize: 12, fontWeight: 600, borderRadius: 7, cursor: busy || running ? 'default' : 'pointer',
            border: '1px solid #06b6d4', background: busy || running ? 'transparent' : '#06b6d4',
            color: busy || running ? '#06b6d4' : '#fff', opacity: busy || running ? 0.6 : 1,
          }}>{running ? 'Training…' : busy ? 'Starting…' : 'Train now'}</button>
        </div>
      </div>

      {pts.length >= 2 ? (
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 90 }} preserveAspectRatio="none">
          {[yMin, (yMin + yMax) / 2, yMax].map((v, i) => (
            <g key={i}>
              <line x1={PL} y1={sy(v)} x2={W - PR} y2={sy(v)} stroke="var(--nd-border)" strokeWidth="0.5" />
              <text x={2} y={sy(v) + 3} fontSize="8" fill="var(--nd-text-3)">{v.toFixed(0)}%</text>
            </g>
          ))}
          <polyline points={line} fill="none" stroke="#06b6d4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx={sx(pts.length - 1)} cy={sy(pts[pts.length - 1].batchAccuracy * 100)} r="3" fill="#06b6d4" />
        </svg>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', padding: '14px 0' }}>
          {m.trained ? 'Learning — train again to extend the curve.' : 'Not trained yet. Click "Train now" (or let the backtest autopilot train it) to start pattern learning.'}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 4 }}>
        {m.nSamples != null ? `${m.nSamples.toLocaleString()} patterns learned` : ''}
        {pts.length ? ` · ${pts.length} rounds` : ''}
        {slice ? ` · universe ${slice}` : ''}
        {msg ? ` · ${msg}` : ''}
      </div>
    </div>
  );
};

export default PatternModelCard;
