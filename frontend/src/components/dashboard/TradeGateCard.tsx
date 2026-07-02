import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../../services/api';

const TradeGateCard: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { const r = await apiService.getTradeGate(); setData((r as any).data); } catch {}
  }, []);
  useEffect(() => { load(); }, [load]);
  const pick = async (mode: string) => {
    if (busy || mode === data?.mode) return;
    setBusy(true);
    try { await apiService.setTradeGate(mode); await load(); } catch {} finally { setBusy(false); }
  };
  if (!data) return null;
  const opts: any[] = data.options ?? [];
  const active = opts.find(o => o.id === data.mode);
  return (
    <div className="nd-card" style={{ padding: '16px 18px', marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <div className="nd-icon-chip"><span className="material-icons" style={{ color: 'var(--nd-text-2)' }}>tune</span></div>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Trade Gate</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>How selective entries are — applies to paper, backtest & autopilot</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 6, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: 4 }}>
        {opts.map(o => {
          const on = o.id === data.mode;
          return (
            <button key={o.id} onClick={() => pick(o.id)} disabled={busy}
              style={{ flex: 1, padding: '8px 6px', borderRadius: 7, border: 'none', cursor: busy ? 'wait' : 'pointer', fontSize: 13, fontWeight: 600,
                background: on ? 'var(--nd-green)' : 'transparent', color: on ? '#fff' : 'var(--nd-text-2)', transition: 'all 0.15s' }}>
              {o.label}
            </button>
          );
        })}
      </div>
      {active && <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', marginTop: 8, lineHeight: 1.5 }}>{active.desc}</div>}
    </div>
  );
};

export default TradeGateCard;
