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
    <div className="nd-bank" style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '14px 16px', marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-2)',
                       fontFamily: 'ui-monospace, monospace', letterSpacing: '.12em', textTransform: 'uppercase' }}>
          Delivery autopilot
        </span>
        {on && <i className="nd-lamp" aria-hidden="true" />}
        <span className="nd-live-note">{on ? 'the agent is managing these daily' : 'off'}</span>
        <span style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
          <button className="nd-console-btn" onClick={() => setShowCreate(s => !s)}>+ portfolio</button>
          <button className="nd-console-btn is-go" onClick={runTick} disabled={busy}>{busy ? '…' : 'run now'}</button>
          <button className={`nd-console-btn${on ? ' is-stop' : ' is-go'}`} onClick={toggle} disabled={busy}>
            {on ? 'disable' : 'enable'}
          </button>
        </span>
      </div>
      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', lineHeight: 1.5, marginBottom: 12 }}>
        Multi-day paper portfolios on delivery picks — an AI agent times the exits
        (target / stop / time-stop / downgrade). Feeds the Delivery line.
      </div>

      {showCreate && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12, padding: '10px 12px', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8 }}>
          {[['name', 'Name', 130], ['capital', 'Capital ₹', 110], ['maxPositions', 'Max pos', 70], ['targetPct', 'Target %', 70], ['stopPct', 'Stop %', 70]].map(([k, label, w]) => (
            <div key={k as string}>
              <div style={{ fontSize: 9.5, color: 'var(--nd-text-3)', fontFamily: 'ui-monospace, monospace',
                            letterSpacing: '.08em', textTransform: 'uppercase', marginBottom: 3 }}>{label}</div>
              <input className="nd-input" style={{ width: w as number }} value={(form as any)[k as string]}
                onChange={e => setForm({ ...form, [k as string]: k === 'name' ? e.target.value : e.target.value.replace(/[^0-9.]/g, '') })} /></div>
          ))}
          <button className="nd-console-btn is-go" onClick={create} style={{ padding: '7px 14px' }}>create</button>
        </div>
      )}

      {pfs.length === 0 ? (
        <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>No delivery portfolios yet — <strong>+ portfolio</strong>, then <strong>enable</strong> to let the agent manage it daily.</div>
      ) : pfs.map((p: any) => {
        const ret = p.returnPct ?? 0;
        return (
          <div key={p.id} style={{ border: '1px solid var(--nd-border)', borderRadius: 8, padding: '10px 12px', marginBottom: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--nd-text-1)' }}>{p.name}</span>
              <span className="nd-chip-tag" style={{ color: p.source === 'optimize' ? 'var(--nd-purple)' : 'var(--nd-accent)' }}>
                {p.source === 'optimize' ? 'optimize test' : 'ai-managed'}
              </span>
              <span style={{ fontSize: 11, color: 'var(--nd-text-3)', fontFamily: 'ui-monospace, monospace' }}>
                ₹{inr(p.value)} · {p.positions.length} pos · cash ₹{inr(p.cash)}
              </span>
              <span style={{ fontSize: 13, fontWeight: 700, fontFamily: 'ui-monospace, monospace',
                             color: ret >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{ret >= 0 ? '+' : ''}{ret}%</span>
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                <button className="nd-console-btn" onClick={() => setOpen(open === p.id ? null : p.id)}>
                  {open === p.id ? 'hide' : 'positions'}
                </button>
                <button className="nd-console-btn is-stop" onClick={() => del(p.id)} title={`Delete ${p.name}`}>×</button>
              </span>
            </div>
            {open === p.id && (
              <div style={{ marginTop: 8 }}>
                {[...p.positions, ...((p.closed || []).slice(-5).reverse())].length === 0 ? <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>No positions.</div> : (
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
                    <thead><tr style={{ color: 'var(--nd-text-3)', fontSize: 9.5, textAlign: 'right',
                                       fontFamily: 'ui-monospace, monospace', letterSpacing: '.06em',
                                       textTransform: 'uppercase' }}>
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
