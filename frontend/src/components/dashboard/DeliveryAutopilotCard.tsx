import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../../services/api';
import { inr } from '../../utils/format';

// ── Delivery (multi-day) paper-trading autopilot ───────────────────────────────

const DeliveryAutopilotCard: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: 'Delivery Portfolio', capital: '200000', targetPct: '12', stopPct: '6', maxPositions: '5' });

  const load = useCallback(async () => {
    try { setData((await apiService.deliveryPortfolios() as any).data); } catch {}
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  const toggle = async () => {
    setBusy(true);
    try { await apiService.enableDeliveryPaper(!data?.enabled); } catch {}
    setTimeout(() => { setBusy(false); load(); }, 2500);
  };
  const runTick = async () => { setBusy(true); try { await apiService.deliveryPaperTick(); } catch {} setTimeout(() => { setBusy(false); load(); }, 2500); };
  const create = async () => {
    try {
      await apiService.createDeliveryPortfolio({ name: form.name, capital: +form.capital || 200000,
        maxPositions: +form.maxPositions || 5, targetPct: +form.targetPct || 12, stopPct: +form.stopPct || 6 });
      setShowCreate(false); load();
    } catch {}
  };
  const del = async (id: string) => { try { await apiService.deleteDeliveryPortfolio(id); load(); } catch {} };

  const pfs: any[] = data?.portfolios ?? [];
  const on = !!data?.enabled;

  return (
    <div className="nd-card" style={{ padding: '16px 18px', marginBottom: 20, borderLeft: `3px solid ${on ? '#3b82f6' : 'var(--nd-border)'}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div className="nd-icon-chip" style={{ background: on ? '#3b82f61a' : 'var(--nd-surface)' }}>
          <span className="material-icons" style={{ color: on ? '#3b82f6' : 'var(--nd-text-2)' }}>calendar_month</span>
        </div>
        <div style={{ flex: 1, minWidth: 180 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Delivery Autopilot
            <span style={{ fontSize: 10, fontWeight: 700, marginLeft: 6, color: on ? '#3b82f6' : 'var(--nd-text-3)', border: `1px solid ${on ? '#3b82f6' : 'var(--nd-border)'}`, borderRadius: 4, padding: '0 5px' }}>{on ? 'ON' : 'OFF'}</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Multi-day paper portfolios on delivery picks — an AI agent times the exits (target / stop / time-stop / downgrade). Feeds the Delivery line.</div>
        </div>
        <button onClick={() => setShowCreate(s => !s)} style={{ padding: '6px 12px', fontSize: 12, fontWeight: 600, borderRadius: 7, border: '1px solid var(--nd-border)', background: 'transparent', color: 'var(--nd-text-2)', cursor: 'pointer' }}>+ Portfolio</button>
        <button onClick={runTick} disabled={busy} style={{ padding: '6px 12px', fontSize: 12, fontWeight: 600, borderRadius: 7, border: '1px solid #3b82f6', background: 'transparent', color: '#3b82f6', cursor: 'pointer' }}>{busy ? '…' : 'Run now'}</button>
        <button onClick={toggle} disabled={busy} style={{ padding: '6px 14px', fontSize: 12, fontWeight: 700, borderRadius: 7, border: 'none', background: on ? 'var(--nd-red)' : '#3b82f6', color: '#fff', cursor: 'pointer' }}>{on ? 'Disable' : 'Enable'}</button>
      </div>

      {showCreate && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12, padding: '10px 12px', background: 'var(--nd-surface)', borderRadius: 8 }}>
          {[['name', 'Name', 130], ['capital', 'Capital ₹', 110], ['maxPositions', 'Max pos', 70], ['targetPct', 'Target %', 70], ['stopPct', 'Stop %', 70]].map(([k, label, w]) => (
            <div key={k as string}><div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{label}</div>
              <input className="nd-input" style={{ width: w as number }} value={(form as any)[k as string]}
                onChange={e => setForm({ ...form, [k as string]: k === 'name' ? e.target.value : e.target.value.replace(/[^0-9.]/g, '') })} /></div>
          ))}
          <button onClick={create} style={{ padding: '8px 14px', borderRadius: 7, border: 'none', background: '#3b82f6', color: '#fff', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>Create</button>
        </div>
      )}

      {pfs.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>No delivery portfolios yet — click <strong>+ Portfolio</strong>, then Enable to let the agent manage it daily.</div>
      ) : pfs.map((p: any) => {
        const ret = p.returnPct ?? 0;
        return (
          <div key={p.id} style={{ border: '1px solid var(--nd-border)', borderRadius: 8, padding: '10px 12px', marginBottom: 8 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, color: 'var(--nd-text-1)' }}>{p.name}</span>
              <span style={{ fontSize: 9, fontWeight: 700, color: p.source === 'optimize' ? '#a855f7' : '#3b82f6', border: `1px solid ${p.source === 'optimize' ? '#a855f7' : '#3b82f6'}`, borderRadius: 4, padding: '0 5px' }}>{p.source === 'optimize' ? 'OPTIMIZE TEST' : 'AI-MANAGED'}</span>
              <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>₹{inr(p.value)} · {p.positions.length} pos · cash ₹{inr(p.cash)}</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: ret >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{ret >= 0 ? '+' : ''}{ret}%</span>
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                <button onClick={() => setOpen(open === p.id ? null : p.id)} style={{ background: 'none', border: 'none', color: 'var(--nd-blue)', cursor: 'pointer', fontSize: 11 }}>{open === p.id ? 'hide' : 'positions'}</button>
                <button onClick={() => del(p.id)} style={{ background: 'none', border: 'none', color: 'var(--nd-red)', cursor: 'pointer', fontSize: 15 }}>×</button>
              </span>
            </div>
            {open === p.id && (
              <div style={{ marginTop: 8 }}>
                {[...p.positions, ...((p.closed || []).slice(-5).reverse())].length === 0 ? <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>No positions.</div> : (
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
                    <thead><tr style={{ color: 'var(--nd-text-3)', fontSize: 10, textAlign: 'right' }}>
                      <th style={{ textAlign: 'left', padding: '3px 6px' }}>Stock</th><th>Entry</th><th>Now</th><th>Target</th><th>Stop</th><th>P&L%</th><th style={{ textAlign: 'left' }}>Status</th>
                    </tr></thead>
                    <tbody>
                      {p.positions.map((pos: any) => (
                        <tr key={pos.symbol} style={{ borderTop: '1px solid var(--nd-border)' }}>
                          <td style={{ padding: '4px 6px', fontWeight: 600 }}>{pos.symbol}</td>
                          <td style={{ textAlign: 'right' }}>₹{pos.entryPrice}</td>
                          <td style={{ textAlign: 'right' }}>₹{pos.current}</td>
                          <td style={{ textAlign: 'right', color: 'var(--nd-green)' }}>₹{pos.target}</td>
                          <td style={{ textAlign: 'right', color: 'var(--nd-red)' }}>₹{pos.stop}</td>
                          <td style={{ textAlign: 'right', color: (pos.pnlPct ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 600 }}>{(pos.pnlPct ?? 0) >= 0 ? '+' : ''}{pos.pnlPct}%</td>
                          <td style={{ padding: '4px 6px', color: 'var(--nd-text-3)', fontSize: 10.5 }}>{pos.statusReason}</td>
                        </tr>
                      ))}
                      {(p.closed || []).slice(-5).reverse().map((c: any, i: number) => (
                        <tr key={'c' + i} style={{ borderTop: '1px solid var(--nd-border)', opacity: 0.6 }}>
                          <td style={{ padding: '4px 6px' }}>{c.symbol} <span style={{ fontSize: 9 }}>closed</span></td>
                          <td style={{ textAlign: 'right' }}>₹{c.entryPrice}</td>
                          <td style={{ textAlign: 'right' }}>₹{c.exitPrice}</td>
                          <td colSpan={2} style={{ textAlign: 'right', fontSize: 10 }}>{c.daysHeld}d</td>
                          <td style={{ textAlign: 'right', color: (c.pnlPct ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 600 }}>{(c.pnlPct ?? 0) >= 0 ? '+' : ''}{c.pnlPct}%</td>
                          <td style={{ padding: '4px 6px', color: 'var(--nd-text-3)', fontSize: 10 }}>{c.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default DeliveryAutopilotCard;
