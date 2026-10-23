/**
 * Session post-mortem — why each trade lost, and what the agents voted.
 *
 * Two things are shown side by side and they are NOT the same kind of claim,
 * which the layout has to make obvious or the panel becomes actively
 * misleading:
 *
 *   • Per trade — the setup at entry, who voted BUY, and the mechanical exit
 *     that booked the loss. A factual record of this session.
 *   • Per agent — whether it systematically pushes BUY into losers, measured
 *     across the whole counterfactual corpus and day-clustered.
 *
 * The per-session BUY counts are deliberately rendered as small grey context,
 * never as a ranking. With 1-5 trades they are noise, and a per-agent ranking
 * taken from executed trades is biased by the entry gate that allowed them —
 * that framing once produced a striking, entirely false split which collapsed
 * to t = -0.44 on the unbiased corpus. The verdict column is the only thing on
 * screen that carries evidence.
 *
 * NOTE: the app-wide axios instance camelCases every response key, so this
 * reads `pnlPct` / `votedBuy` / `setupEdgePp`, not the snake_case the API
 * actually returns.
 */
import React, { useCallback, useEffect, useState } from 'react';
import apiService from '../services/api';

interface Props { sessionId: string }

const box: React.CSSProperties = {
  background: 'var(--nd-bg)',
  border: '1px solid var(--nd-border)',
  borderRadius: 10,
  padding: '10px 12px',
  marginBottom: 12,
};

const chip = (color: string): React.CSSProperties => ({
  fontSize: 9.5, fontWeight: 700, padding: '1px 6px', borderRadius: 4,
  background: `${color}1f`, color, border: `1px solid ${color}55`,
  whiteSpace: 'nowrap',
});

const th: React.CSSProperties = { padding: '5px 8px', fontWeight: 500, textAlign: 'left' };
const td: React.CSSProperties = { padding: '5px 8px', color: 'var(--nd-text-1)' };

const VERDICT_COLOR: Record<string, string> = {
  culprit: 'var(--nd-red)',
  protective: 'var(--nd-green)',
};

/** Only an established verdict gets a colour. "leans ..." and "no signal" stay
 *  grey — colouring a sub-threshold t is how a panel invents a culprit. */
function verdictColor(v: string): string {
  return VERDICT_COLOR[v] ?? 'var(--nd-text-3)';
}

const SessionPostmortem: React.FC<Props> = ({ sessionId }) => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [narrating, setNarrating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async (withNarrative: boolean) => {
    withNarrative ? setNarrating(true) : setLoading(true);
    setError(null);
    try {
      const r = await apiService.sessionPostmortem(sessionId, withNarrative);
      setData(r);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'Could not load post-mortem');
    } finally {
      withNarrative ? setNarrating(false) : setLoading(false);
    }
  }, [sessionId]);

  // Facts load as soon as the section is opened; the LLM write-up costs an 8B
  // call and stays behind an explicit click.
  useEffect(() => { setData(null); setError(null); setOpen(false); }, [sessionId]);
  useEffect(() => { if (open && !data && !loading) load(false); }, [open, data, loading, load]);

  const trades: any[] = data?.trades ?? [];
  const attribution: any[] = data?.agentAttribution ?? [];
  const established = attribution.filter(
    (a) => a.verdict === 'culprit' || a.verdict === 'protective');

  return (
    <details style={{ marginBottom: 12 }} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600, color: 'var(--nd-text-2)', padding: '6px 0' }}>
        Post-mortem — why it lost, and who voted for it
      </summary>

      {loading && <div style={{ fontSize: 12, color: 'var(--nd-text-3)', padding: '8px 0' }}>Analysing…</div>}
      {error && <div style={{ fontSize: 12, color: 'var(--nd-red)', padding: '8px 0' }}>{error}</div>}

      {data && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 8 }}>
            {data.symbol} · {data.date} · {data.nTrades} trade{data.nTrades === 1 ? '' : 's'}, {data.nLosses} loss{data.nLosses === 1 ? '' : 'es'}
          </div>

          {trades.length === 0 && (
            <div style={{ ...box, fontSize: 12, color: 'var(--nd-text-3)' }}>
              No closed trades recorded for this session.
            </div>
          )}

          {/* ── Per trade: what happened ── */}
          {trades.map((t, i) => (
            <div key={i} style={box}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                <span style={chip(t.isLoss ? 'var(--nd-red)' : 'var(--nd-green)')}>
                  {t.isLoss ? 'LOSS' : 'WIN'}
                </span>
                {t.pnlPct != null && (
                  <strong style={{ fontSize: 12, color: t.isLoss ? 'var(--nd-red)' : 'var(--nd-green)' }}>
                    {t.pnlPct > 0 ? '+' : ''}{t.pnlPct}%
                  </strong>
                )}
                <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                  {t.entryTime} → {t.exitTime}
                </span>
                {t.setup && (
                  <span style={chip(t.setupEdgePp < 0 ? 'var(--nd-red)' : 'var(--nd-text-3)')}>
                    {t.setup}
                  </span>
                )}
                {/* Only a Bonferroni-clearing setup edge is asserted. */}
                {t.setup && t.setupEdgePp != null && (
                  <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)' }}>
                    {t.setupEdgePp > 0 ? '+' : ''}{t.setupEdgePp}pp vs same-day entries
                    {t.setupEstablished ? '' : ' (not established)'}
                  </span>
                )}
                {!t.setup && t.setupNote && (
                  <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)', fontStyle: 'italic' }}>
                    setup unknown — {t.setupNote}
                  </span>
                )}
              </div>

              <div style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.5, marginBottom: 6 }}>
                {t.lossReason}
              </div>

              {(t.votedBuy ?? []).length > 0 && (
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                  Voted BUY at entry:{' '}
                  {(t.votedBuy as string[]).map((a) => (
                    <span key={a} style={{ color: 'var(--nd-text-1)', marginRight: 6 }}>{a}</span>
                  ))}
                </div>
              )}
            </div>
          ))}

          {/* ── Per agent: what is actually established ── */}
          {attribution.length > 0 && (
            <div style={box}>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 2 }}>
                Which agent is the culprit?
              </div>
              <div style={{ fontSize: 11, color: 'var(--nd-text-3)', lineHeight: 1.5, marginBottom: 8 }}>
                {data.culpritVerdict}
              </div>

              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', minWidth: 460, borderCollapse: 'collapse', fontSize: 11.5 }}>
                  <thead>
                    <tr style={{ color: 'var(--nd-text-3)' }}>
                      <th style={th}>Agent</th>
                      <th style={th}>Verdict</th>
                      <th style={th}>Lift</th>
                      <th style={th}>t</th>
                      <th style={th}>This session</th>
                    </tr>
                  </thead>
                  <tbody>
                    {attribution.map((a) => {
                      const isEstablished = a.verdict === 'culprit' || a.verdict === 'protective';
                      return (
                        <tr key={a.agent} style={{ borderTop: '1px solid var(--nd-border)' }}>
                          <td style={{ ...td, fontWeight: isEstablished ? 700 : 400 }}>{a.agent}</td>
                          <td style={{ ...td, color: verdictColor(a.verdict) }}>{a.verdict}</td>
                          <td style={{ ...td, color: 'var(--nd-text-2)' }}>
                            {a.baselineLift > 0 ? '+' : ''}{a.baselineLift}
                          </td>
                          <td style={{ ...td, color: 'var(--nd-text-2)' }}>{a.baselineT ?? '—'}</td>
                          {/* Descriptive only — never a ranking. */}
                          <td style={{ ...td, color: 'var(--nd-text-3)' }}>
                            bought {a.boughtLosers} losers, {a.boughtWinners} winners
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 8, lineHeight: 1.5 }}>
                Lift = share of losing decisions this agent voted BUY on, minus the same share
                among winners. Measured across {attribution[0]?.baselineDays ?? '—'} days on every
                decision, taken and rejected, so the entry gate cannot bias it. An agent that votes
                BUY on everything scores ~0 — loud, not culpable. The
                “this session” column is context, not evidence: {trades.length} trade
                {trades.length === 1 ? '' : 's'} cannot establish anything.
                {established.length === 0 && ' No agent currently clears the significance bar as a culprit.'}
              </div>
            </div>
          )}

          {/* ── LLM narrative, on demand ── */}
          <div style={box}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: data.narrative ? 8 : 0 }}>
              <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-accent)' }}>auto_awesome</span>
              <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI write-up</span>
              {!data.narrative && (
                <button className="nd-btn" onClick={() => load(true)} disabled={narrating}
                  style={{ marginLeft: 'auto', fontSize: 11, padding: '3px 10px' }}>
                  {narrating ? 'Writing…' : 'Explain this session'}
                </button>
              )}
            </div>
            {data.narrative
              ? (
                <>
                  <div style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                    {data.narrative}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 8, lineHeight: 1.5 }}>
                    Prose over the facts above. The model assigns no tag, ranks no agent and
                    produces no number — those are all measured. Treat the tables as the source
                    of truth.
                  </div>
                </>
              )
              : !narrating && (
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                  Optional — costs a local LLM call and adds nothing the tables above do not
                  already state.
                </div>
              )}
          </div>
        </div>
      )}
    </details>
  );
};

export default SessionPostmortem;
