/**
 * Trade post-mortem — the full causal chain for one trade.
 *
 * Shown as a tab inside the Orders execution modal. The trace tab answers
 * "what did the system do"; this answers "why did it end that way", which
 * needs three things the trade record alone cannot supply:
 *
 *   1. The price PATH between entry and exit. A trade that went +0.8% and
 *      round-tripped is a completely different failure from one that never
 *      traded green, and the endpoints look identical. This is the single most
 *      diagnostic number on the panel.
 *   2. The setup at entry with its measured, day-clustered edge.
 *   3. Each agent's own stated reasoning at the entry bar.
 *
 * The "was there ever a bankable gain" line is gated on the 0.125% round-trip
 * cost, not an arbitrary threshold: a peak below the cost could never have been
 * realised, so calling it "a move the exit failed to keep" blames the wrong
 * half of the system.
 *
 * Response keys arrive camelCased by the app-wide axios interceptor.
 */
import React, { useEffect, useState } from 'react';
import apiService from '../services/api';

const ACTION_COLOR: Record<string, string> = {
  BUY: 'var(--nd-green)', SELL: 'var(--nd-red)', HOLD: 'var(--nd-text-3)',
};

/** Naming the half of the system at fault is the point of the panel. "Entry"
 *  and "exit" are the only two that lead anywhere: one says stop taking this
 *  kind of trade, the other says stop closing it like this. */
const BLAME_LABEL: Record<string, string> = {
  entry: 'THE ENTRY',
  exit: 'THE EXIT',
  unclear: 'NO SINGLE CAUSE',
  unknown: 'NOT ENOUGH DATA',
};

const BLAME_COLOR: Record<string, string> = {
  entry: 'var(--nd-red)',
  exit: '#f59e0b',
  unclear: 'var(--nd-text-3)',
  unknown: 'var(--nd-text-3)',
};

const card: React.CSSProperties = {
  background: 'var(--nd-surface)',
  border: '1px solid var(--nd-border)',
  borderRadius: 10,
  padding: '12px 14px',
  marginBottom: 12,
};

const label: React.CSSProperties = {
  fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'uppercase',
  letterSpacing: 0.5, marginBottom: 6, fontWeight: 700,
};

const Bar: React.FC<{ best: number; worst: number; pnl: number }> = ({ best, worst, pnl }) => {
  // One axis from worst to best excursion, with entry at 0 and where it
  // actually closed. Makes "never went green" visible at a glance.
  const lo = Math.min(worst, pnl, 0);
  const hi = Math.max(best, pnl, 0);
  const span = hi - lo || 1;
  const pct = (v: number) => ((v - lo) / span) * 100;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ position: 'relative', height: 8, background: 'var(--nd-bg)', borderRadius: 4, border: '1px solid var(--nd-border)' }}>
        <div style={{ position: 'absolute', left: `${pct(Math.min(0, pnl))}%`, width: `${Math.abs(pct(pnl) - pct(0))}%`, top: 0, bottom: 0, background: pnl >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', opacity: 0.5, borderRadius: 4 }} />
        <div title="entry" style={{ position: 'absolute', left: `${pct(0)}%`, top: -3, width: 2, height: 14, background: 'var(--nd-text-2)' }} />
        <div title="best" style={{ position: 'absolute', left: `${pct(best)}%`, top: -3, width: 2, height: 14, background: 'var(--nd-green)' }} />
        <div title="worst" style={{ position: 'absolute', left: `${pct(worst)}%`, top: -3, width: 2, height: 14, background: 'var(--nd-red)' }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9.5, color: 'var(--nd-text-3)', marginTop: 4 }}>
        <span style={{ color: 'var(--nd-red)' }}>worst {worst}%</span>
        <span>entry 0%</span>
        <span style={{ color: 'var(--nd-green)' }}>best {best}%</span>
      </div>
    </div>
  );
};

const TradePostmortem: React.FC<{ tradeId: string }> = ({ tradeId }) => {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setD(null); setErr(null);
    apiService.tradePostmortem(tradeId)
      .then(r => { if (alive) (r?.error ? setErr(r.error) : setD(r)); })
      .catch(e => { if (alive) setErr(e?.message || 'Could not load post-mortem'); });
    return () => { alive = false; };
  }, [tradeId]);

  if (err) return <div style={{ fontSize: 12, color: 'var(--nd-red)', padding: 12 }}>{err}</div>;
  if (!d) return <div style={{ fontSize: 12, color: 'var(--nd-text-3)', padding: 12 }}>Analysing…</div>;

  const path = d.pricePath ?? {};
  const blame = d.blame ?? {};
  const blameColor = BLAME_COLOR[blame.target] ?? 'var(--nd-text-3)';
  const agents: any[] = d.agents ?? [];
  const buyers = agents.filter(a => (a.action || '').toUpperCase() === 'BUY');
  const others = agents.filter(a => (a.action || '').toUpperCase() !== 'BUY');

  return (
    <div>
      {/* ── The verdict, first and unmissable ──
          The previous version listed facts and left the reader to work out what
          was at fault. The whole point of a post-mortem is to name it. */}
      {blame.target && (
        <div style={{ ...card, borderColor: `${blameColor}66`, background: `${blameColor}0f`, borderLeft: `3px solid ${blameColor}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 10, fontWeight: 800, letterSpacing: 0.6, color: blameColor }}>
              BLAME: {BLAME_LABEL[blame.target] ?? blame.target.toUpperCase()}
            </span>
            {blame.hindsight && (
              <span style={{ fontSize: 9, color: 'var(--nd-text-3)', border: '1px solid var(--nd-border)', borderRadius: 4, padding: '0 5px' }}>
                with hindsight
              </span>
            )}
          </div>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 6 }}>
            {blame.headline}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--nd-text-2)', lineHeight: 1.6 }}>
            {blame.detail}
          </div>
          {blame.contributing && (
            <div style={{ fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.55, marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--nd-border)' }}>
              <strong style={{ color: 'var(--nd-text-2)' }}>Also against it: </strong>{blame.contributing}
            </div>
          )}
          {(blame.agentsImplicated ?? []).length > 0 && (
            <div style={{ fontSize: 12, marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--nd-border)' }}>
              <span style={{ color: 'var(--nd-text-3)' }}>Voted to take this entry: </span>
              {(blame.agentsImplicated as string[]).map(a => (
                <span key={a} style={{ display: 'inline-block', background: `${blameColor}22`, color: blameColor, borderRadius: 4, padding: '1px 7px', marginRight: 5, fontWeight: 600, textTransform: 'capitalize' }}>{a}</span>
              ))}
              <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginTop: 5, lineHeight: 1.5 }}>
                These agents argued for this specific entry. That is a fact about this trade —
                it is not evidence any of them is systematically at fault. See the corpus
                verdicts below.
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Supporting detail ── */}
      <div style={{ ...card, borderColor: d.isLoss ? 'var(--nd-red)55' : 'var(--nd-green)55' }}>
        <div style={label}>{d.isLoss ? 'What happened, step by step' : 'How this trade closed'}</div>
        {(d.causes ?? []).length > 0
          ? (d.causes as string[]).map((c, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, fontSize: 12.5, color: 'var(--nd-text-1)', lineHeight: 1.55, marginBottom: 6 }}>
              <span style={{ color: 'var(--nd-text-3)', flexShrink: 0 }}>{i + 1}.</span>
              <span>{c}</span>
            </div>
          ))
          : <div style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>Closed at {d.pnlPct}% via {d.exitReason ?? 'an unrecorded exit'}.</div>}
      </div>

      {/* ── Price path: the diagnostic number ── */}
      {path.bars ? (
        <div style={card}>
          <div style={label}>How far it went, before it ended</div>
          <Bar best={path.bestPct} worst={path.worstPct} pnl={d.pnlPct ?? 0} />
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 8, lineHeight: 1.5 }}>
            {path.everGreen
              ? `Peaked at +${path.bestPct}% and closed at ${d.pnlPct}%.`
              : `Never traded above entry — best was ${path.bestPct}%.`}
            {' '}Over {path.bars} one-minute bars. A peak below the 0.125% round-trip cost
            was never bankable, so it points at the entry rather than the exit.
          </div>
        </div>
      ) : null}

      {/* ── Setup at entry ── */}
      <div style={card}>
        <div style={label}>Setup at entry</div>
        {d.setup ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: d.setupEdgePp < 0 ? 'var(--nd-red)' : 'var(--nd-text-1)' }}>{d.setup}</span>
            {d.setupEdgePp != null && (
              <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                {d.setupEdgePp > 0 ? '+' : ''}{d.setupEdgePp}pp vs same-day entries
                {d.setupEstablished ? '' : ' · not established'}
              </span>
            )}
          </div>
        ) : (
          <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', fontStyle: 'italic', marginBottom: 8 }}>
            Entry indicators not retained for this trade — setup unknown.
          </div>
        )}
        {Object.keys(d.indicators ?? {}).length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 11, color: 'var(--nd-text-3)' }}>
            {Object.entries(d.indicators as Record<string, any>).map(([k, v]) => (
              <span key={k}>{k} <strong style={{ color: 'var(--nd-text-1)' }}>{typeof v === 'number' ? v : String(v)}</strong></span>
            ))}
          </div>
        )}
        {d.entryReason && (
          <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginTop: 8, lineHeight: 1.5 }}>
            <span style={{ color: 'var(--nd-text-3)' }}>Gate: </span>{d.entryReason}
          </div>
        )}
      </div>

      {/* ── Who argued for it, and what they said ── */}
      <div style={card}>
        <div style={label}>Who voted to enter ({buyers.length} of {d.nAgents})</div>
        {[...buyers, ...others].map((a) => (
          <div key={a.agent} style={{ borderTop: '1px solid var(--nd-border)', padding: '7px 0' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 12, color: 'var(--nd-text-1)', textTransform: 'capitalize', minWidth: 92 }}>{a.agent}</span>
              <span style={{ fontSize: 12, fontWeight: 700, color: ACTION_COLOR[(a.action || '').toUpperCase()] ?? 'var(--nd-text-3)' }}>{a.action}</span>
              {a.weight != null && <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>w {Number(a.weight).toFixed(2)}</span>}
              {a.confidence != null && <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{(Number(a.confidence) * 100).toFixed(0)}%</span>}
              {/* Corpus verdict, only when it clears the bar. */}
              {(a.baselineVerdict === 'culprit' || a.baselineVerdict === 'protective') && (
                <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 4, marginLeft: 'auto',
                  background: a.baselineVerdict === 'culprit' ? 'var(--nd-red)1f' : 'var(--nd-green)1f',
                  color: a.baselineVerdict === 'culprit' ? 'var(--nd-red)' : 'var(--nd-green)' }}>
                  {a.baselineVerdict} (t {a.baselineT})
                </span>
              )}
            </div>
            {a.reasoning && (
              <div style={{ fontSize: 11, color: 'var(--nd-text-2)', lineHeight: 1.5, marginTop: 3 }}>{a.reasoning}</div>
            )}
          </div>
        ))}
        <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 10, lineHeight: 1.5 }}>
          {d.culpritNote}
        </div>
      </div>
    </div>
  );
};

export default TradePostmortem;
