import React, { useCallback, useEffect, useState } from 'react';
import apiService from '../services/api';

// The axios interceptor converts snake_case → camelCase, so we read camelCase here.
interface SrcStat    { source: string; count: number; winRate: number; }
interface ActionStat { action: string; count: number; winRate: number; avgPnl: number; }
interface SymStat    { symbol: string; count: number; }
interface MemStats {
  totalCases: number;
  bySource: SrcStat[];
  byAction: ActionStat[];
  topSymbols: SymStat[];
}

const card: React.CSSProperties = {
  background: 'var(--nd-surface)', border: '1px solid var(--nd-border)',
  borderRadius: 12, padding: 20,
};

const LStat: React.FC<{ label: string; value: string; color?: string }> = ({ label, value, color }) => (
  <div>
    <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, color: color || 'var(--nd-text-1)' }}>{value}</div>
  </div>
);

const actionColor = (a: string) =>
  a === 'BUY' ? 'var(--nd-green)' : a === 'SELL' ? 'var(--nd-red)' : 'var(--nd-text-3)';

const PatternMemory: React.FC = () => {
  const [stats, setStats]   = useState<MemStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const [seedMsg, setSeedMsg] = useState('');
  const [sweeping, setSweeping] = useState(false);
  const [lastSweep, setLastSweep] = useState<any>(null);
  const [learning, setLearning] = useState<any>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.memoryStats();
      setStats((res as any).data ?? (res as any));
    } catch {
      setStats(null);
    } finally {
      setLoading(false);
    }
    try {
      const ls = await apiService.learningSummary();
      setLearning((ls as any).data ?? null);
    } catch { /* ignore */ }
  }, []);

  const loadSweep = useCallback(async () => {
    try {
      const s = await apiService.memorySweepStatus();
      setLastSweep(s.last ?? null);
      return !!s.running;
    } catch {
      return false;
    }
  }, []);

  useEffect(() => { load(); loadSweep(); }, [load, loadSweep]);

  const runSweep = async () => {
    setSweeping(true);
    try {
      await apiService.memorySweep();
      // Poll until the background sweep finishes, then refresh stats
      const poll = setInterval(async () => {
        const running = await loadSweep();
        if (!running) {
          clearInterval(poll);
          setSweeping(false);
          await load();
        }
      }, 4000);
    } catch {
      setSweeping(false);
    }
  };

  const runSeed = async () => {
    setSeeding(true);
    setSeedMsg('Replaying historical candles across the stock universe — this can take a minute…');
    try {
      const res = await apiService.memorySeed({ lookback_days: 365, horizon: 3, stride: 1 });
      const d = (res as any).data ?? res;
      setSeedMsg(`✓ Seeded ${d.totalInserted?.toLocaleString() ?? 0} cases across ${d.symbolsProcessed ?? 0} stocks.`);
      await load();
    } catch (e: any) {
      setSeedMsg(`✗ Seeding failed: ${e?.message ?? 'unknown error'}`);
    } finally {
      setSeeding(false);
    }
  };

  const total = stats?.totalCases ?? 0;
  const overallWin =
    stats && stats.byAction.length
      ? stats.byAction.reduce((s, a) => s + a.winRate * a.count, 0) /
        Math.max(1, stats.byAction.reduce((s, a) => s + a.count, 0))
      : 0;

  return (
    <div>
      {/* Intro / explainer */}
      <div style={{ ...card, marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span className="material-icons" style={{ color: 'var(--nd-green)' }}>memory</span>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>
            Pattern Memory Bank
          </h2>
        </div>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--nd-text-2)' }}>
          Every backtest, paper trade and live decision is fingerprinted into a vector and stored
          with its real outcome. When a new situation appears, the engine retrieves the most
          similar past cases and only acts when their track record supports it — otherwise it
          abstains. The more the bank learns, the more selective and accurate decisions become.
        </p>
      </div>

      {/* Learning status — proves every backtest/paper trade trains the agents */}
      {learning && (
        <div style={{ ...card, marginBottom: 16, borderLeft: '3px solid var(--nd-green)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <span className="material-icons" style={{ color: 'var(--nd-green)', fontSize: 20 }}>school</span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Agent Learning</h3>
            {learning.totals?.recentOutcomes24h > 0 && (
              <span style={{ fontSize: 11, color: 'var(--nd-green)', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 20, padding: '2px 10px' }}>
                ● {learning.totals.recentOutcomes24h} trained in last 24h
              </span>
            )}
          </div>
          <div className="nd-grid-4">
            <LStat label="Predictions made" value={(learning.totals?.predictions ?? 0).toLocaleString()} />
            <LStat label="Outcomes learned" value={(learning.totals?.outcomes ?? 0).toLocaleString()} />
            <LStat label="Overall accuracy" value={`${((learning.overallAccuracy ?? 0) * 100).toFixed(1)}%`} color={(learning.overallAccuracy ?? 0) >= 0.5 ? 'var(--nd-green)' : 'var(--nd-text-1)'} />
            <LStat label="Memory cases" value={(learning.memoryCases ?? 0).toLocaleString()} />
          </div>
          {/* Per-agent weights/accuracy */}
          {Array.isArray(learning.agents) && learning.agents.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
              {learning.agents.map((a: any) => (
                <div key={a.agent} style={{ fontSize: 11, color: 'var(--nd-text-2)', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '6px 10px' }}>
                  <span style={{ fontWeight: 600, color: 'var(--nd-text-1)', textTransform: 'capitalize' }}>{a.agent}</span>
                  <span style={{ color: 'var(--nd-text-3)' }}> · w{a.weight} · {(a.accuracy * 100).toFixed(0)}% ({a.correct}/{a.total})</span>
                </div>
              ))}
            </div>
          )}
          <div style={{ marginTop: 12, fontSize: 12, color: 'var(--nd-text-3)' }}>
            Every <strong>backtest</strong>, <strong>paper trade</strong> and <strong>live session</strong> updates these agent
            weights, the RL policy, and the memory bank — so future predictions get more accurate over time.
          </div>
        </div>
      )}

      {/* Headline stats */}
      <div className="nd-grid-3" style={{ marginBottom: 16 }}>
        <div style={card}>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginBottom: 6 }}>Total Cases Remembered</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--nd-text-1)' }}>
            {loading ? '…' : total.toLocaleString()}
          </div>
        </div>
        <div style={card}>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginBottom: 6 }}>Historical Win-Rate</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: overallWin >= 0.5 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
            {loading || !total ? '—' : `${(overallWin * 100).toFixed(1)}%`}
          </div>
        </div>
        <div style={card}>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginBottom: 6 }}>Distinct Symbols</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--nd-text-1)' }}>
            {loading ? '…' : (stats?.topSymbols.length ?? 0)}
          </div>
        </div>
      </div>

      {/* Refresh control — nightly auto + manual */}
      <div style={{ ...card, marginBottom: 16 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 220, flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)', marginBottom: 4 }}>
              Refresh from latest data
            </div>
            <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
              Replays real backtests across the watchlist and rebuilds the bank from the freshest
              candles. Runs <strong>automatically every night (~02:00 IST)</strong> — or trigger it now.
            </div>
          </div>
          <button
            className="nd-btn"
            onClick={runSweep}
            disabled={sweeping}
            style={{
              background: 'var(--nd-green)', color: '#fff', border: 'none',
              borderRadius: 8, padding: '10px 18px', fontSize: 14, fontWeight: 600,
              cursor: sweeping ? 'wait' : 'pointer', opacity: sweeping ? 0.7 : 1, whiteSpace: 'nowrap',
            }}
          >
            {sweeping ? 'Refreshing…' : 'Refresh Now'}
          </button>
        </div>

        {sweeping && (
          <div style={{ marginTop: 12, fontSize: 12, color: 'var(--nd-text-2)',
                        background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                        borderRadius: 8, padding: '8px 12px' }}>
            Running real backtests across all watchlist stocks — this takes a minute or two…
          </div>
        )}
        {!sweeping && lastSweep && (
          <div style={{ marginTop: 12, fontSize: 12, color: 'var(--nd-text-3)',
                        background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                        borderRadius: 8, padding: '8px 12px' }}>
            Last refresh: <span style={{ color: 'var(--nd-text-2)' }}>{new Date(lastSweep.finishedAt).toLocaleString()}</span>
            {' · '}{(lastSweep.casesInserted ?? 0).toLocaleString()} cases
            {' · '}{lastSweep.backtestsOk ?? 0} backtests
            {lastSweep.durationSecs != null ? ` · ${lastSweep.durationSecs}s` : ''}
            {' · '}<span style={{ color: 'var(--nd-text-3)' }}>{lastSweep.trigger}</span>
          </div>
        )}

        {/* Secondary: one-off historical seed (denser, synthetic-labelled) */}
        <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <button
            onClick={runSeed}
            disabled={seeding}
            style={{
              background: 'transparent', color: 'var(--nd-text-2)', border: '1px solid var(--nd-border)',
              borderRadius: 8, padding: '7px 14px', fontSize: 12, fontWeight: 500,
              cursor: seeding ? 'wait' : 'pointer', opacity: seeding ? 0.7 : 1, whiteSpace: 'nowrap',
            }}
          >
            {seeding ? 'Seeding…' : 'Dense seed (forward-return labels)'}
          </button>
          {seedMsg && <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{seedMsg}</span>}
        </div>
      </div>

      {/* Breakdown tables */}
      <div className="nd-grid-2">
        {/* By action */}
        <div style={card}>
          <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>
            By Action
          </h3>
          {!stats || !stats.byAction.length ? (
            <div style={{ fontSize: 13, color: 'var(--nd-text-3)' }}>No cases yet — seed the bank to begin.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {stats.byAction.map(a => (
                <div key={a.action} style={{ display: 'flex', alignItems: 'center', gap: 10,
                     background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                     borderRadius: 8, padding: '10px 12px' }}>
                  <span style={{ fontWeight: 700, color: actionColor(a.action), width: 52 }}>{a.action}</span>
                  <span style={{ fontSize: 12, color: 'var(--nd-text-3)', width: 90 }}>{a.count.toLocaleString()} cases</span>
                  <div style={{ flex: 1, height: 6, background: 'var(--nd-border)', borderRadius: 4, overflow: 'hidden' }}>
                    <div style={{ width: `${Math.round(a.winRate * 100)}%`, height: '100%',
                                  background: a.winRate >= 0.5 ? 'var(--nd-green)' : 'var(--nd-red)' }} />
                  </div>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--nd-text-1)', width: 44, textAlign: 'right' }}>
                    {(a.winRate * 100).toFixed(0)}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* By source + top symbols */}
        <div style={card}>
          <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>
            By Source
          </h3>
          {!stats || !stats.bySource.length ? (
            <div style={{ fontSize: 13, color: 'var(--nd-text-3)' }}>No cases yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
              {stats.bySource.map(s => (
                <div key={s.source} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                     background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px' }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-2)' }}>{s.source}</span>
                  <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
                    {s.count.toLocaleString()} · {(s.winRate * 100).toFixed(0)}% win
                  </span>
                </div>
              ))}
            </div>
          )}

          <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>
            Top Symbols
          </h3>
          {!stats || !stats.topSymbols.length ? (
            <div style={{ fontSize: 13, color: 'var(--nd-text-3)' }}>—</div>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {stats.topSymbols.map(s => (
                <span key={s.symbol} style={{ fontSize: 12, color: 'var(--nd-text-2)',
                      background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                      borderRadius: 6, padding: '4px 8px' }}>
                  {s.symbol} <span style={{ color: 'var(--nd-text-3)' }}>{s.count}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PatternMemory;
