import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import apiService from '../services/api';

/**
 * Dense terminal-style market board — the scanner's live high-conviction picks
 * with action, AI grade, price, win-probability and a signal-strength bar.
 * Bloomberg-monitor feel: monospace, tight rows, color-coded.
 */
interface Item {
  symbol: string; name?: string; price?: number; action?: string;
  confidence?: number; winProbability?: number; score?: number;
  signalScore?: number; grade?: string; agreement?: number;
}

const GRADE_COLOR: Record<string, string> = { A: '#00c853', B: '#64dd17', C: '#ffab00', D: '#ff5252' };
const actionColor = (a?: string) => a === 'BUY' ? '#26a69a' : a === 'SELL' ? '#ef5350' : '#90a4ae';

const MarketBoard: React.FC<{ limit?: number }> = ({ limit = 15 }) => {
  const navigate = useNavigate();
  const [items, setItems] = useState<Item[]>([]);
  const [updated, setUpdated] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [regime, setRegime] = useState<string>('');

  const load = async () => {
    try {
      const r: any = await apiService.aiWatchlist();
      const d = r.data ?? r;
      setItems((d.items ?? []).slice(0, limit));
      setUpdated(d.updatedAt ?? d.updated_at ?? '');
      setRegime(d.marketRegime ?? d.market_regime ?? '');
    } catch {} finally { setLoading(false); }
  };

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="nd-pm-card" style={{ padding: 0, overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderBottom: '1px solid var(--nd-border)' }}>
        <span className="material-icons" style={{ fontSize: 17, color: 'var(--nd-green)' }}>monitor_heart</span>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Market Board</h3>
        {regime && (
          <span style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px',
            color: regime === 'bullish' ? '#26a69a' : regime === 'bearish' ? '#ef5350' : '#ffab00',
            border: '1px solid var(--nd-border)', borderRadius: 20, padding: '2px 9px' }}>{regime}</span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--nd-text-3)' }}>
          {updated ? `updated ${new Date(updated).toLocaleTimeString()}` : ''}
        </span>
        <button onClick={load} title="Refresh" style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-3)', display: 'flex', padding: 2 }}>
          <span className="material-icons" style={{ fontSize: 15 }}>refresh</span>
        </button>
      </div>

      {/* Column header. Win% is dropped below 420px (class .nd-mb-winpct) — six
          columns at these widths need ~338px minimum, more than a phone's
          content width leaves once the sidebar card's own padding is subtracted. */}
      <div className="nd-mb-cols" style={{ display: 'grid', gap: 6,
        padding: '6px 14px', borderBottom: '1px solid var(--nd-border)',
        fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--nd-text-3)',
        fontFamily: 'ui-monospace, monospace' }}>
        <span>Symbol</span><span>Action</span><span>Grade</span><span style={{ textAlign: 'right' }}>Price</span>
        <span className="nd-mb-winpct" style={{ textAlign: 'right' }}>Win%</span><span>Signal</span>
      </div>

      {/* Rows */}
      <div style={{ maxHeight: 430, overflowY: 'auto' }}>
        {loading && items.length === 0 && (
          <div style={{ padding: '20px 14px', fontSize: 12, color: 'var(--nd-text-3)', textAlign: 'center' }}>Loading scanner…</div>
        )}
        {!loading && items.length === 0 && (
          <div style={{ padding: '20px 14px', fontSize: 12, color: 'var(--nd-text-3)', textAlign: 'center' }}>
            No high-conviction picks right now (scanner runs during market hours).
          </div>
        )}
        {items.map((it, i) => {
          const sig = Math.max(0, Math.min(100, it.signalScore ?? (it.score ?? 0) * 100));
          const win = (it.winProbability ?? it.confidence ?? 0) * 100;
          return (
            <div key={it.symbol + i} onClick={() => navigate(`/stocks/${it.symbol}`)} className="nd-mb-cols" style={{
              display: 'grid', gap: 6, alignItems: 'center',
              padding: '7px 14px', borderBottom: i < items.length - 1 ? '1px solid var(--nd-border)' : 'none',
              cursor: 'pointer', fontFamily: 'ui-monospace, monospace', fontSize: 12,
            }}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-bg)')}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700, color: 'var(--nd-text-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.symbol}</div>
                <div style={{ fontSize: 9.5, color: 'var(--nd-text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.name}</div>
              </div>
              <span style={{ fontSize: 10.5, fontWeight: 700, color: actionColor(it.action) }}>{it.action ?? '—'}</span>
              <span style={{ fontSize: 11, fontWeight: 800, color: GRADE_COLOR[it.grade ?? ''] ?? 'var(--nd-text-3)' }}>{it.grade ?? '—'}</span>
              <span style={{ textAlign: 'right', color: 'var(--nd-text-1)' }}>{it.price != null ? `₹${it.price.toFixed(2)}` : '—'}</span>
              <span className="nd-mb-winpct" style={{ textAlign: 'right', fontWeight: 600, color: win >= 60 ? '#26a69a' : win >= 45 ? '#ffab00' : 'var(--nd-text-2)' }}>{win.toFixed(0)}%</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <div style={{ flex: 1, height: 5, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{ width: `${sig}%`, height: '100%', background: sig >= 65 ? '#26a69a' : sig >= 45 ? '#ffab00' : '#ef5350', borderRadius: 3 }} />
                </div>
                <span style={{ fontSize: 10, color: 'var(--nd-text-3)', minWidth: 22, textAlign: 'right' }}>{sig.toFixed(0)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default MarketBoard;
