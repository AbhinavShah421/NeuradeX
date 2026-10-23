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
  const vote = d.vote ?? {};
  const dp = d.decisionPath ?? {};

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
            {' '}Over {path.bars} one-minute bars.{' '}
            {/* This sentence used to be printed unconditionally, which made it
                read as a verdict on every trade. On a trade that peaked ABOVE
                the cost it flatly contradicted the headline: the banner blamed
                the exit while this line said the entry. Say what is true of
                THIS path, and let the two agree. */}
            {path.peakWasBankable
              ? `The peak cleared the ${path.roundTripCostPct ?? 0.125}% round-trip cost, so there was a
                 real gain to keep — that points at the exit, not the entry.`
              : `The peak never cleared the ${path.roundTripCostPct ?? 0.125}% round-trip cost, so there
                 was no gain here that could have been banked — that points at the entry, not the exit.`}
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

      {/* ── How the trade came to be taken at all ── */}
      {dp.notes?.length > 0 && (
        <div style={card}>
          <div style={label}>How this trade got taken</div>
          {dp.ensembleAbstained && (
            <div style={{ display: 'inline-block', fontSize: 10, fontWeight: 800, letterSpacing: 0.5, color: '#f59e0b', background: '#f59e0b1f', border: '1px solid #f59e0b55', borderRadius: 4, padding: '2px 7px', marginBottom: 8 }}>
              THE ENSEMBLE VOTED HOLD — THE SCORED GATE OVERRODE IT
            </div>
          )}
          {dp.score != null && (
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 22, fontWeight: 800, color: (dp.margin ?? 99) <= 5 ? '#f59e0b' : 'var(--nd-text-1)' }}>{dp.score}</span>
              <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>entry score, needed {dp.scoreMin}</span>
              <span style={{ fontSize: 11, color: (dp.margin ?? 0) <= 5 ? '#f59e0b' : 'var(--nd-text-3)' }}>
                cleared by {dp.margin}
              </span>
            </div>
          )}
          {(dp.notes as string[]).map((n, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.55, marginBottom: 5 }}>
              <span style={{ color: 'var(--nd-text-3)', flexShrink: 0 }}>•</span><span>{n}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── The panel vote, reconstructed ── */}
      {vote.summary && (
        <div style={card}>
          <div style={label}>What the agents found</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.55, marginBottom: 10 }}>{vote.summary}</div>

          {/* Conviction split — BUY mass vs SELL mass vs abstentions */}
          <div style={{ display: 'flex', height: 7, borderRadius: 4, overflow: 'hidden', border: '1px solid var(--nd-border)', marginBottom: 4 }}>
            {[['buyMass', 'var(--nd-green)'], ['sellMass', 'var(--nd-red)'], ['holdMass', 'var(--nd-text-3)']].map(([k, c]) => {
              const total = (vote.buyMass ?? 0) + (vote.sellMass ?? 0) + (vote.holdMass ?? 0) || 1;
              return <div key={k as string} style={{ width: `${((vote[k as string] ?? 0) / total) * 100}%`, background: c as string, opacity: 0.75 }} />;
            })}
          </div>
          <div style={{ display: 'flex', gap: 12, fontSize: 10, color: 'var(--nd-text-3)', marginBottom: 10 }}>
            <span style={{ color: 'var(--nd-green)' }}>buy {vote.buyMass}</span>
            <span style={{ color: 'var(--nd-red)' }}>sell {vote.sellMass}</span>
            <span>abstained {vote.holdMass} ({vote.abstained} agents)</span>
          </div>

          {(vote.agents as any[] ?? []).map((a) => {
            const act = (a.action || '').toUpperCase();
            return (
              <div key={a.agent} style={{ borderTop: '1px solid var(--nd-border)', padding: '7px 0' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12, color: 'var(--nd-text-1)', textTransform: 'capitalize', minWidth: 92 }}>{a.agent}</span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: ACTION_COLOR[act] ?? 'var(--nd-text-3)' }}>{a.action}</span>
                  {a.contribution != null && (
                    <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>
                      pull {a.contribution}
                      {a.shareOfSide != null ? ` · ${(a.shareOfSide * 100).toFixed(0)}% of its side` : ''}
                    </span>
                  )}
                  {/* Was this agent right, on this trade? HOLD is an abstention,
                      not a correct call — scoring it as one is how SELL became
                      unlearnable on a long-only system. */}
                  {a.wasRight === true && <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--nd-green)' }}>RIGHT</span>}
                  {a.wasRight === false && <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--nd-red)' }}>WRONG</span>}
                  {a.decisive && (
                    <span style={{ fontSize: 9, fontWeight: 800, padding: '1px 5px', borderRadius: 4, background: 'var(--nd-red)22', color: 'var(--nd-red)' }}>DECISIVE</span>
                  )}
                  {(a.baselineVerdict === 'culprit' || a.baselineVerdict === 'protective') && (
                    <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 4, marginLeft: 'auto',
                      background: a.baselineVerdict === 'culprit' ? 'var(--nd-red)1f' : 'var(--nd-green)1f',
                      color: a.baselineVerdict === 'culprit' ? 'var(--nd-red)' : 'var(--nd-green)' }}>
                      {a.baselineVerdict} (t {a.baselineT})
                    </span>
                  )}
                </div>
                {a.reasoning && (
                  <div style={{ fontSize: 11, color: 'var(--nd-text-2)', lineHeight: 1.5, marginTop: 3 }}>
                    <span style={{ color: 'var(--nd-text-3)' }}>found: </span>{a.reasoning}
                  </div>
                )}
              </div>
            );
          })}
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 10, lineHeight: 1.5 }}>
            “Pull” is the agent's confidence times its effective weight — exactly what the
            ensemble tallies. “Decisive” means the entry would not have fired without it.
            HOLD is an abstention, so it is scored neither right nor wrong. {d.culpritNote}
          </div>
        </div>
      )}
    </div>
  );
};

export default TradePostmortem;
