import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import apiService from '../services/api';

// Per-agent drill-down: every executed trade this agent voted on, its vote at
// the time, and whether it was RIGHT (BUY correct when the trade won; SELL/HOLD
// correct when it lost/flat). Reached from the Orders execution-trace popup.

const ACTION_COLOR: Record<string, string> = { BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b' };

interface AgentTrade {
  symbol: string; date: string; time: string; vote: string;
  confidence: number; weight: number; ensemble_action: string;
  outcome: string; pnl_pct: number; correct: boolean;
}
interface Summary {
  n: number; correct: number; accuracy: number | null;
  by_action: Record<string, { n: number; correct: number; accuracy: number | null }>;
}

const AgentDetail: React.FC = () => {
  const { agent = '' } = useParams();
  const navigate = useNavigate();
  const [trades, setTrades]   = useState<AgentTrade[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter]   = useState<string>('ALL');

  useEffect(() => {
    setLoading(true);
    apiService.getAgentTrades(agent, 200)
      .then((r: any) => { const d = r?.data ?? r; setTrades(d.trades ?? []); setSummary(d.summary ?? null); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [agent]);

  const shown = filter === 'ALL' ? trades : trades.filter(t => t.vote === filter);

  return (
    <div style={{ padding: '20px 24px', maxWidth: 960, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <button onClick={() => navigate(-1)} style={{ background: 'none', border: '1px solid var(--nd-border)', borderRadius: 8, cursor: 'pointer', padding: '4px 8px', color: 'var(--nd-text-2)', display: 'flex', alignItems: 'center' }}>
          <span className="material-icons" style={{ fontSize: 18 }}>arrow_back</span>
        </button>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--nd-text-1)', textTransform: 'capitalize' }}>{agent} agent</h1>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Every executed trade this agent voted on</div>
        </div>
      </div>

      {/* Summary tiles */}
      {summary && summary.n > 0 && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
          <Tile label="Overall accuracy" value={summary.accuracy != null ? `${(summary.accuracy * 100).toFixed(0)}%` : '—'} sub={`${summary.n} trades`} good={(summary.accuracy ?? 0) >= 0.5} />
          {['BUY', 'SELL', 'HOLD'].map(act => {
            const s = summary.by_action[act];
            if (!s) return null;
            return <Tile key={act} label={`${act} accuracy`} value={s.accuracy != null ? `${(s.accuracy * 100).toFixed(0)}%` : '—'} sub={`${s.n} votes`} good={(s.accuracy ?? 0) >= 0.5} color={ACTION_COLOR[act]} />;
          })}
        </div>
      )}

      {/* Vote filter */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        {['ALL', 'BUY', 'SELL', 'HOLD'].map(f => (
          <button key={f} onClick={() => setFilter(f)}
            style={{ padding: '5px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: 'pointer',
              border: `1px solid ${filter === f ? (ACTION_COLOR[f] ?? 'var(--nd-accent)') : 'var(--nd-border)'}`,
              background: filter === f ? `${ACTION_COLOR[f] ?? 'var(--nd-accent)'}18` : 'transparent',
              color: filter === f ? (ACTION_COLOR[f] ?? 'var(--nd-accent)') : 'var(--nd-text-2)' }}>
            {f}
          </button>
        ))}
      </div>

      {/* Trades table */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)' }}>Loading…</div>
      ) : shown.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)' }}>No decisions recorded for this filter yet.</div>
      ) : (
        <div style={{ overflowX: 'auto', border: '1px solid var(--nd-border)', borderRadius: 12 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: 'var(--nd-surface)', textAlign: 'left', color: 'var(--nd-text-3)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.4 }}>
                <th style={{ padding: '10px 14px' }}>Symbol</th>
                <th style={{ padding: '10px 14px' }}>Date · Time</th>
                <th style={{ padding: '10px 14px' }}>Its vote</th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}>Conf · Weight</th>
                <th style={{ padding: '10px 14px' }}>Ensemble</th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}>Result</th>
                <th style={{ padding: '10px 14px', textAlign: 'center' }}>Right?</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((t, i) => (
                <tr key={i} style={{ borderTop: '1px solid var(--nd-border)' }}>
                  <td style={{ padding: '9px 14px', fontWeight: 600, color: 'var(--nd-text-1)' }}>{t.symbol}</td>
                  <td style={{ padding: '9px 14px', color: 'var(--nd-text-3)' }}>{t.date} · {t.time}</td>
                  <td style={{ padding: '9px 14px', fontWeight: 700, color: ACTION_COLOR[t.vote] ?? 'var(--nd-text-3)' }}>{t.vote}</td>
                  <td style={{ padding: '9px 14px', textAlign: 'right', color: 'var(--nd-text-2)' }}>{(t.confidence * 100).toFixed(0)}% · {t.weight.toFixed(2)}</td>
                  <td style={{ padding: '9px 14px', fontWeight: 600, color: ACTION_COLOR[t.ensemble_action] ?? 'var(--nd-text-3)' }}>{t.ensemble_action}</td>
                  <td style={{ padding: '9px 14px', textAlign: 'right', fontWeight: 600, color: t.pnl_pct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(2)}%</td>
                  <td style={{ padding: '9px 14px', textAlign: 'center' }}>
                    <span className="material-icons" style={{ fontSize: 18, color: t.correct ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                      {t.correct ? 'check_circle' : 'cancel'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

const Tile: React.FC<{ label: string; value: string; sub: string; good: boolean; color?: string }> = ({ label, value, sub, good, color }) => (
  <div style={{ flex: '1 1 140px', background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '12px 16px' }}>
    <div style={{ fontSize: 11, color: color ?? 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 24, fontWeight: 700, color: good ? 'var(--nd-green)' : 'var(--nd-text-1)' }}>{value}</div>
    <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{sub}</div>
  </div>
);

export default AgentDetail;
