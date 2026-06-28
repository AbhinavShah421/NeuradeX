import React, { useCallback, useEffect, useState } from 'react';
import apiService from '../services/api';

interface SrcStat    { source: string; count: number; winRate: number; }
interface ActionStat { action: string; count: number; winRate: number; avgPnl: number; }
interface SymStat    { symbol: string; count: number; }
interface MemStats {
  totalCases: number;
  bySource: SrcStat[];
  byAction: ActionStat[];
  topSymbols: SymStat[];
}

const accuracyColor = (acc: number) =>
  acc >= 0.55 ? 'var(--nd-green)' : acc >= 0.45 ? 'var(--nd-orange)' : '#e74c3c';

const accuracyBg = (acc: number) =>
  acc >= 0.55 ? 'rgba(0,179,134,0.12)' : acc >= 0.45 ? 'rgba(245,166,35,0.12)' : 'rgba(231,76,60,0.12)';

const SOURCE_META: Record<string, { icon: string }> = {
  BACKTEST: { icon: 'history_edu' },
  REPLAY:   { icon: 'replay'      },
  PAPER:    { icon: 'receipt_long'},
  LIVE:     { icon: 'bolt'        },
};

interface AgentMeta {
  icon:       string;
  label:      string;
  definition: string;
  sources:    string[];
  signals:    string[];
  edge:       string;
}

const AGENT_META: Record<string, AgentMeta> = {
  gbm: {
    icon:       'account_tree',
    label:      'Gradient Boosting Classifier',
    definition: 'A supervised machine-learning model trained on thousands of labelled candle sequences. For every new bar it computes a 19-dimensional pattern fingerprint and predicts the probability of an up-move using gradient-boosted decision trees.',
    sources:    ['OHLCV candles (intraday 1-min)', 'Pattern fingerprint (19 features)', 'Backtest outcome labels'],
    signals:    ['P(up) probability', 'Pattern fingerprint similarity', 'Historical win rate per fingerprint cluster'],
    edge:       'Best at catching high-conviction directional moves where a candlestick pattern repeatedly preceded a clear up or down leg in past data.',
  },
  meanrev: {
    icon:       'swap_vert',
    label:      'Mean Reversion (Ornstein-Uhlenbeck)',
    definition: 'Uses the Ornstein-Uhlenbeck process to estimate how fast a stock reverts to its mean. Entry thresholds (z-score cutoffs) adapt per symbol based on the fitted half-life — fast reverters are entered earlier, slow/trending ones are skipped entirely.',
    sources:    ['Closing prices (last 60 bars)', 'RSI (14-period)', 'OLS regression on ΔP vs P(t-1)'],
    signals:    ['Z-score vs rolling mean', 'OU half-life (bars to revert)', 'RSI extremes (adaptive thresholds)', 'Velocity damping (avoids falling-knife entries)'],
    edge:       'Profitable in range-bound, choppy sessions where price oscillates around a stable mean. Abstains when ADX indicates a trending market.',
  },
  rl: {
    icon:       'psychology',
    label:      'Reinforcement Learning (Q-Learning)',
    definition: 'A Q-learning agent with a 108-state discrete state space (RSI bucket × MACD sign × VWAP position × momentum tier × volatility regime). It updates its Q-table after every completed trade using the actual P&L as the reward signal, so it continuously improves without any labels.',
    sources:    ['Q-table stored in Redis (persisted 30 days)', 'RSI, MACD, VWAP (real-time candles)', 'Trade P&L outcomes (every session)'],
    signals:    ['Q-value for BUY / SELL / HOLD in current state', 'ε-greedy exploration (5% random)', 'Discount factor γ=0.90, learning rate α=0.10'],
    edge:       'Adapts to regime shifts over time without retraining. Gets stronger as more sessions complete and the Q-table accumulates real reward history.',
  },
  anomaly: {
    icon:       'warning_amber',
    label:      'Anomaly / Trap Detector',
    definition: 'Fits a scikit-learn IsolationForest on the stock\'s own recent bars (no pre-trained labels). When the current bar is a statistical outlier in return, range, candle body, or volume, the ensemble is forced to HOLD — preventing entries into news spikes and bull/bear traps.',
    sources:    ['OHLCV candles (last 40–200 bars per symbol)', 'Log-scaled volume', 'Bar return, candle range and body'],
    signals:    ['IsolationForest anomaly score', 'Deviation from symbol\'s own distribution', 'Volume log-ratio vs rolling baseline'],
    edge:       'Acts as a veto layer. Prevents the ensemble from being whipsawed on sharp gap-ups, illiquid spikes, or news-driven traps that look attractive to other indicators.',
  },
  technical: {
    icon:       'show_chart',
    label:      'Technical Analysis',
    definition: 'Aggregates five classic price indicators into a single weighted score. Each indicator votes independently; the votes are summed and mapped to a BUY/SELL/HOLD decision with confidence proportional to the consensus strength.',
    sources:    ['Intraday OHLCV candles (min 20 bars)', 'VWAP (volume-weighted average price)'],
    signals:    ['RSI-14 (extreme readings weighted 2× vs soft readings)', 'MACD line vs signal line + histogram', 'Bollinger Bands % (overbought/oversold breakout)', 'VWAP position (price above/below intraday fair value)', 'SMA-5 / SMA-20 golden & death cross'],
    edge:       'Reliable in liquid, trending markets where classic TA signals are respected by institutional flow. Works best when multiple indicators align. Confidence capped at 0.85 — requires strong multi-indicator consensus for high confidence.',
  },
  pattern: {
    icon:       'candlestick_chart',
    label:      'Candlestick Pattern Recognition (v2)',
    definition: 'Detects classic multi-bar candlestick formations and scores them with trend context. Reversal patterns (hammer, shooting star, morning/evening star) only receive full weight when the preceding 5–10-bar trend supports a reversal — a hammer forming in an uptrend gets reduced weight. Engulfing, doji and inside-bar patterns are trend-independent.',
    sources:    ['OHLCV candles (min 5 bars, last 10 used for trend context)'],
    signals:    ['Bullish/bearish engulfing', 'Hammer (only after downtrend)', 'Shooting star (only after uptrend)', 'Morning star / evening star (3-bar reversal with trend check)', 'Doji (reduces conviction 50%)', 'Inside bar (reduces conviction 30%)', 'Higher highs / lower lows (structure)', '5-bar and 10-bar trend direction'],
    edge:       'Fast and interpretable — works from just 5 candles. Adds structure signals (HH/LL, engulfing) that the indicator-based agents cannot see. Confidence capped at 0.85.',
  },
  sentiment: {
    icon:       'sentiment_satisfied',
    label:      'News Sentiment',
    definition: 'Queries a sentiment pipeline for the latest news catalyst on the symbol being analysed. A language-model derived sentiment score is mapped to directional confidence. Abstains when no strong catalyst is found for the current session.',
    sources:    ['News sentiment pipeline (internal LLM scorer)', 'Symbol-level news feed (intraday)'],
    signals:    ['Sentiment polarity (positive / negative / neutral)', 'Catalyst strength score', 'Recency of catalyst (stale news is discounted)'],
    edge:       'Catches sharp directional moves driven by earnings surprises, regulatory news, or broker upgrades that pure price-based agents cannot anticipate.',
  },
  regime: {
    icon:       'leaderboard',
    label:      'Market Regime Filter (HMM)',
    definition: 'Fits a 4-state Gaussian Hidden Markov Model on rolling bar features using Baum-Welch EM. Viterbi decoding prevents single-bar noise from flipping the regime. The decoded state is mapped to bull / bear / sideways / high-volatility and used to reweight all other agents before the final vote.',
    sources:    ['Intraday OHLCV candles (min 60 bars for HMM, else rule-based fallback)', 'ATR, ADX (Wilder-smoothed), 10-bar SMA slope, volume ratio'],
    signals:    ['HMM state (4 latent states)', 'ATR % (volatility proxy)', 'Wilder ADX (trend strength)', 'Volume vs EMA-10 (activity level)'],
    edge:       'Does not trade itself — multiplies the weight of agents that work well in the current regime. In high-volatility regimes it boosts the anomaly veto; in trending markets it boosts momentum and technical.',
  },
  momentum: {
    icon:       'trending_up',
    label:      'Momentum',
    definition: 'Measures the rate and sustainability of a price move using Rate-of-Change, volume surge, a stochastic oscillator and a price-acceleration check. Requires both price momentum AND volume confirmation before signalling — reducing false breakouts.',
    sources:    ['Intraday OHLCV candles (min 10 bars)'],
    signals:    ['ROC-5 (5-bar rate of change %)', 'Volume ratio vs 10-bar average', 'Stochastic %K and %D crossover', 'Price acceleration (2nd derivative of close)'],
    edge:       'Effective at catching continuation moves early in a trend. The volume confirmation filter keeps it quiet during low-conviction drifts.',
  },
  volatility: {
    icon:       'bolt',
    label:      'Volatility / Risk Monitor (v2)',
    definition: 'Risk oracle and squeeze-breakout detector. Scores current ATR and Bollinger Band width against their own rolling percentile history — so thresholds adapt to each symbol. Directional votes only fire on a Bollinger Band squeeze breakout (width in the bottom 25th percentile for ≥3 bars, then price closes outside the band). Always abstains from direction in the top 30th ATR percentile, just sets risk high.',
    sources:    ['OHLCV candles (min 30 bars)', 'ATR-14 (rolling series)', 'Bollinger Band width (20-period, 2σ rolling series)'],
    signals:    ['ATR percentile vs own history', 'BB width percentile vs own history', 'Risk score = 0.7×ATR_pctile + 0.3×BB_pctile', 'Vol trend: expanding / contracting / stable', 'Squeeze breakout (BUY/SELL only on this)'],
    edge:       'Protects capital in explosive sessions by setting risk_score high, which the ensemble uses to scale position size down. Only calls direction on BB squeeze breakouts — a setup with genuine statistical edge unlike generic vol-level signals.',
  },
  memory: {
    icon:       'memory',
    label:      'Pattern Memory (Case-Based) v2',
    definition: 'Retrieves genuinely similar historical setups using a progressive cosine similarity floor (0.65 → 0.55 → 0.45 → abstain). Only signals BUY/SELL when the nearest neighbours have ≥55% win rate AND positive avg PnL — both must hold. Abstains entirely when no sufficiently similar precedent exists rather than retrieving unrelated cases.',
    sources:    ['Pattern Memory Bank (PostgreSQL — up to 50k most recent cases)', 'In-process numpy cosine k-NN cache (TTL 3 min)', 'Backtest, paper trade and live session outcomes'],
    signals:    ['Progressive cosine similarity floor (0.65/0.55/0.45)', 'Per-action win rate of retrieved neighbours', 'Per-action avg PnL of retrieved neighbours', 'Evidence score = sim² × sample_mass', 'Expected value = win_rate × avg_pnl × evidence', 'Symbol-local and regime-match bonuses'],
    edge:       'Becomes more selective and accurate as the bank grows. Unlike other agents it adapts to each symbol\'s own history. The gate (55% win rate + positive PnL) prevents it from acting on noisy, barely-above-random retrievals that caused the v1 44% accuracy problem.',
  },
  day_structure: {
    icon:       'map',
    label:      'Day Structure',
    definition: 'Reads the full day\'s candle history from market open to identify where price sits in today\'s range, detect intraday swing support/resistance levels, and assess risk/reward for a long entry. Votes SELL when price is in the top tier of the day\'s range (near day high with little upside), BUY near confirmed swing support with favourable R/R, and HOLD in mid-range.',
    sources:    ['All intraday OHLCV candles from 09:15 IST', 'Swing pivot detection (4-bar window)', 'Morning range (first 45 min)'],
    signals:    ['Day range position (0 = day low, 1 = day high)', 'Nearest swing resistance above price (%)', 'Nearest swing support below price (%)', 'Risk/reward ratio (upside ÷ downside)', 'Morning range bias (above / inside / below)', 'Extended-move flag (>2% from open near day high/low)'],
    edge:       'The only agent that answers "where in today\'s chart are we?" — preventing the common mistake of buying near the day high where upside is exhausted and downside is the full day\'s range. Acts as a structural veto: if price is in the top 18% of the day\'s range with R/R below 0.4×, the entry gate is blocked regardless of other agent votes.',
  },
};

// ── Action trend mini-chart (used inside AgentDetailSheet) ───────────────────

const ActionTrendChart: React.FC<{ agentName: string; action: string; color: string }> = ({ agentName, action, color }) => {
  const [points, setPoints] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    apiService.agentActionTrend(agentName, action)
      .then((r: any) => setPoints(r?.data?.points ?? []))
      .catch(() => setPoints([]))
      .finally(() => setLoading(false));
  }, [agentName, action]);

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 100, color: 'var(--nd-text-3)', fontSize: 12 }}>
      <span className="material-icons nd-spin" style={{ fontSize: 18, marginRight: 6 }}>autorenew</span>Loading…
    </div>
  );

  if (points.length < 2) return (
    <div style={{ fontSize: 11, color: 'var(--nd-text-3)', fontStyle: 'italic', padding: '10px 0' }}>
      Not enough dated data yet — accuracy trend will appear once more training sessions complete.
    </div>
  );

  const W = 340, H = 110, PL = 34, PR = 10, PT = 10, PB = 22;
  const accs   = points.map((p: any) => p.accuracy * 100);
  const minA   = Math.max(0,   Math.min(...accs) - 8);
  const maxA   = Math.min(100, Math.max(...accs) + 8);
  const rangeA = maxA - minA || 1;
  const sx = (i: number) => PL + (i / (points.length - 1)) * (W - PL - PR);
  const sy = (v: number) => PT + (1 - (v - minA) / rangeA) * (H - PT - PB);
  const linePath = points.map((p: any, i: number) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(p.accuracy * 100).toFixed(1)}`).join(' ');
  const gradId = `atg_${agentName}_${action}`;

  // 7-day rolling average
  const rolling: number[] = points.map((_: any, i: number) => {
    const slice = points.slice(Math.max(0, i - 6), i + 1);
    return slice.reduce((s: number, p: any) => s + p.accuracy * 100, 0) / slice.length;
  });
  const rollingPath = rolling.map((v, i) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ');

  const refY = sy(50);
  const dates = points.map((p: any) => p.date as string);

  return (
    <div>
      {/* Legend */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 6, fontSize: 10, color: 'var(--nd-text-3)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <div style={{ width: 10, height: 2, background: color, opacity: 0.4, borderRadius: 1 }} />
          Daily accuracy
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <div style={{ width: 10, height: 2, background: color, borderRadius: 1 }} />
          7-day rolling
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <div style={{ width: 10, height: 2, background: '#64748b', borderRadius: 1, borderTop: '1px dashed #64748b' }} />
          50% baseline
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: 'block', overflow: 'visible' }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.18} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>

        {/* Y-axis labels */}
        {[minA, 50, maxA].filter((v, i, a) => a.indexOf(v) === i).map((v, i) => (
          <text key={i} x={PL - 3} y={sy(v) + 4} textAnchor="end" fontSize={8} fill="var(--nd-text-3)">{v.toFixed(0)}%</text>
        ))}

        {/* 50% reference line */}
        {refY >= PT && refY <= H - PB && (
          <line x1={PL} y1={refY} x2={W - PR} y2={refY} stroke="#64748b" strokeWidth={1} strokeDasharray="3 3" />
        )}

        {/* Area fill under daily line */}
        <path d={`${linePath} L${sx(points.length - 1).toFixed(1)},${H - PB} L${sx(0).toFixed(1)},${H - PB} Z`}
          fill={`url(#${gradId})`} />

        {/* Daily accuracy line (faint) */}
        <path d={linePath} fill="none" stroke={color} strokeWidth={1.2} strokeOpacity={0.4} strokeLinejoin="round" />

        {/* Dots on daily points */}
        {points.map((p: any, i: number) => (
          <circle key={i} cx={sx(i)} cy={sy(p.accuracy * 100)} r={2.5} fill={color} opacity={0.5} />
        ))}

        {/* 7-day rolling average (bold) */}
        <path d={rollingPath} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={sx(points.length - 1)} cy={sy(rolling[rolling.length - 1])} r={3.5} fill={color} />

        {/* X-axis: first and last date */}
        <text x={PL} y={H} textAnchor="start" fontSize={8} fill="var(--nd-text-3)">{dates[0]}</text>
        <text x={W - PR} y={H} textAnchor="end" fontSize={8} fill="var(--nd-text-3)">{dates[dates.length - 1]}</text>
      </svg>

      {/* Latest stats row */}
      <div style={{ display: 'flex', gap: 16, marginTop: 6, fontSize: 11, color: 'var(--nd-text-3)' }}>
        <span>{points.length} trading days</span>
        <span>·</span>
        <span>{points.reduce((s: number, p: any) => s + p.total, 0).toLocaleString()} total decisions</span>
        <span>·</span>
        <span style={{ color, fontWeight: 600 }}>
          {(rolling[rolling.length - 1]).toFixed(0)}% recent accuracy
        </span>
      </div>
    </div>
  );
};

interface ModelRow { name: string; label: string; kind: string; desc: string; enabled: boolean; weight: number | null; trained?: boolean; meta?: any; }


const PatternMemory: React.FC = () => {
  const [stats,     setStats]     = useState<MemStats | null>(null);
  const [loading,       setLoading]       = useState(true);
  const [seeding,       setSeeding]       = useState(false);
  const [seedMsg,       setSeedMsg]       = useState('');
  const [sweeping,      setSweeping]      = useState(false);
  const [lastSweep,     setLastSweep]     = useState<any>(null);
  const [learning,      setLearning]      = useState<any>(null);
  const [agentPopup,    setAgentPopup]    = useState<{ agent: any; rank: number } | null>(null);
  const [models,        setModels]        = useState<ModelRow[]>([]);
  const [modelBusy,     setModelBusy]     = useState<string | null>(null);
  const [training,      setTraining]      = useState(false);
  const [trainMsg,      setTrainMsg]      = useState<{ ok: boolean; text: string } | null>(null);
  const [dataset,       setDataset]       = useState<{ summary: any; rows: any[] } | null>(null);

  const loadDataset = useCallback(async () => {
    try {
      const r: any = await apiService.candleCoverage();
      setDataset({ summary: r.summary ?? {}, rows: r.data ?? [] });
    } catch {}
  }, []);

  const loadModels = useCallback(async () => {
    try { const r: any = await apiService.aiModels(); setModels(r.data?.models ?? []); } catch {}
  }, []);

  const [modelError, setModelError] = useState<string | null>(null);

  const toggleModel = async (m: ModelRow) => {
    const next = !m.enabled;
    setModels(prev => prev.map(x => x.name === m.name ? { ...x, enabled: next } : x));
    setModelBusy(m.name);
    setModelError(null);
    try {
      await apiService.setAiModel(m.name, { enabled: next });
      await loadModels();
    } catch (err: any) {
      setModels(prev => prev.map(x => x.name === m.name ? { ...x, enabled: m.enabled } : x));
      setModelError(`Failed to ${next ? 'enable' : 'disable'} ${m.name}: ${err?.response?.data?.detail ?? err?.message ?? 'unknown error'}`);
    } finally { setModelBusy(null); }
  };

  const setWeightOverride = async (m: ModelRow, weight: number | null) => {
    setModels(prev => prev.map(x => x.name === m.name ? { ...x, weight } : x));
    setModelBusy(m.name);
    setModelError(null);
    try {
      if (weight === null) await apiService.setAiModel(m.name, { clearWeight: true });
      else await apiService.setAiModel(m.name, { weight });
      await loadModels(); // re-read true server state, not just optimistic guess
    } catch (err: any) {
      setModels(prev => prev.map(x => x.name === m.name ? { ...x, weight: m.weight } : x));
      setModelError(`Failed to set weight for ${m.name}: ${err?.response?.data?.detail ?? err?.message ?? 'unknown error'}`);
    } finally { setModelBusy(null); }
  };

  const trainGbm = async () => {
    setTraining(true); setTrainMsg(null);
    try {
      const r: any = await apiService.trainGbm(250);
      const d = r.data || {};
      if (d.status === 'ok') {
        setTrainMsg({ ok: true, text: `GBM trained on ${d.samples} samples — accuracy ${(d.accuracy * 100).toFixed(1)}%, AUC ${d.auc ?? '—'}.` });
        loadModels();
      } else { setTrainMsg({ ok: false, text: `Training: ${d.status} (${d.samples ?? 0} samples).` }); }
    } catch { setTrainMsg({ ok: false, text: 'GBM training failed.' }); }
    finally { setTraining(false); }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.memoryStats();
      setStats((res as any).data ?? (res as any));
    } catch { setStats(null); }
    finally { setLoading(false); }
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
    } catch { return false; }
  }, []);

  useEffect(() => { load(); loadSweep(); loadModels(); loadDataset(); }, [load, loadSweep, loadModels, loadDataset]);

  const runSweep = async () => {
    setSweeping(true);
    try {
      await apiService.memorySweep();
      const poll = setInterval(async () => {
        const running = await loadSweep();
        if (!running) { clearInterval(poll); setSweeping(false); await load(); }
      }, 4000);
    } catch { setSweeping(false); }
  };

  const runSeed = async () => {
    setSeeding(true);
    setSeedMsg('Replaying historical candles — this can take a minute…');
    try {
      const res = await apiService.memorySeed({ lookback_days: 365, horizon: 3, stride: 1 });
      const d = (res as any).data ?? res;
      setSeedMsg(`✓ Seeded ${d.totalInserted?.toLocaleString() ?? 0} cases across ${d.symbolsProcessed ?? 0} stocks.`);
      await load();
    } catch (e: any) {
      setSeedMsg(`✗ Seeding failed: ${e?.message ?? 'unknown error'}`);
    } finally { setSeeding(false); }
  };

  const total = stats?.totalCases ?? 0;
  const overallWin =
    stats && stats.byAction.length
      ? stats.byAction.reduce((s, a) => s + a.winRate * a.count, 0) /
        Math.max(1, stats.byAction.reduce((s, a) => s + a.count, 0))
      : 0;

  const sortedAgents: any[] = Array.isArray(learning?.agents)
    ? [...learning.agents].sort((a: any, b: any) => b.weight - a.weight)
    : [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, paddingBottom: 8 }}>

      {/* ═══ 1. INTRO BANNER ════════════════════════════════════════════ */}
      <div className="nd-pm-card" style={{
        background: 'linear-gradient(135deg, rgba(0,179,134,0.08) 0%, var(--nd-surface) 55%)',
        borderColor: 'rgba(0,179,134,0.22)',
        display: 'flex', gap: 14, alignItems: 'flex-start',
      }}>
        <div style={{
          width: 40, height: 40, borderRadius: 11, flexShrink: 0,
          background: 'rgba(0,179,134,0.15)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <span className="material-icons" style={{ color: 'var(--nd-green)', fontSize: 22 }}>memory</span>
        </div>
        <div>
          <h2 style={{ margin: '0 0 6px', fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>
            Pattern Memory Bank
          </h2>
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.65, color: 'var(--nd-text-2)' }}>
            Every backtest, paper trade and live decision is fingerprinted and stored with
            its real outcome. When a new situation appears the engine retrieves similar past
            cases and only acts when their track record supports it.{' '}
            <span style={{ color: 'var(--nd-text-1)', fontWeight: 500 }}>
              The more the bank learns, the more selective and accurate it becomes.
            </span>
          </p>
        </div>
      </div>

      {/* ═══ 2. HEADLINE STATS ══════════════════════════════════════════ */}
      <div className="nd-pm-headline" style={{ gap: 16 }}>
        {[
          {
            label: 'Total Cases', value: loading ? '—' : total.toLocaleString(),
            color: 'var(--nd-text-1)', icon: 'dataset',
            iconColor: 'var(--nd-green)', iconBg: 'rgba(0,179,134,0.12)',
          },
          {
            label: 'Historical Win-Rate',
            value: loading || !total ? '—' : `${(overallWin * 100).toFixed(1)}%`,
            color: overallWin >= 0.5 ? 'var(--nd-green)' : '#e74c3c',
            icon: 'emoji_events',
            iconColor: overallWin >= 0.5 ? 'var(--nd-green)' : '#e74c3c',
            iconBg: overallWin >= 0.5 ? 'rgba(0,179,134,0.12)' : 'rgba(231,76,60,0.12)',
          },
          {
            label: 'Symbols', value: loading ? '—' : String(stats?.topSymbols.length ?? 0),
            color: 'var(--nd-text-1)', icon: 'candlestick_chart',
            iconColor: '#7c3aed', iconBg: 'rgba(124,58,237,0.12)',
          },
        ].map(s => (
          <div key={s.label} className="nd-pm-card" style={{
            display: 'flex', alignItems: 'center', gap: 12,
          }}>
            <div style={{
              width: 40, height: 40, borderRadius: 10, flexShrink: 0,
              background: s.iconBg,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <span className="material-icons" style={{ color: s.iconColor, fontSize: 21 }}>{s.icon}</span>
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{
                fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
                letterSpacing: '0.5px', color: 'var(--nd-text-3)', marginBottom: 4,
              }}>{s.label}</div>
              <div style={{ fontSize: 26, fontWeight: 700, color: s.color, lineHeight: 1 }}>{s.value}</div>
            </div>
          </div>
        ))}
      </div>

      {/* ═══ 2b. 1-SECOND DATASET ═══════════════════════════════════════ */}
      {dataset && (dataset.summary?.days ?? 0) >= 0 && (
        <div className="nd-pm-card" style={{ borderLeft: '3px solid #0ea5e9' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <span className="material-icons" style={{ fontSize: 18, color: '#0ea5e9' }}>dataset</span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>1-Second Dataset</h3>
            <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
              real tick data captured live from the Groww stream — backtests/replays read this first
            </span>
            <button onClick={loadDataset} title="Refresh" style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-3)', display: 'flex', padding: 2 }}>
              <span className="material-icons" style={{ fontSize: 15 }}>refresh</span>
            </button>
          </div>
          <div className="nd-pm-stats-grid">
            {[
              { label: 'Symbols',     value: (dataset.summary?.symbols ?? 0).toLocaleString() },
              { label: 'Days stored', value: (dataset.summary?.days ?? 0).toLocaleString() },
              { label: 'Total ticks', value: (dataset.summary?.totalTicks ?? 0).toLocaleString() },
              { label: 'Size',        value: `${(((dataset.summary?.totalBytes ?? 0) / 1024 / 1024)).toFixed(1)} MB` },
            ].map(s => (
              <div key={s.label} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '12px 14px' }}>
                <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--nd-text-3)', marginBottom: 6 }}>{s.label}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.value}</div>
              </div>
            ))}
          </div>
          {(dataset.rows?.length ?? 0) === 0 ? (
            <p style={{ margin: '12px 0 0', fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
              Empty so far — the dataset grows automatically as live/paper sessions run during market hours
              (1-second resolution). It's then resampled to any bar size for backtests and replays.
            </p>
          ) : (
            <div style={{ marginTop: 12, maxHeight: 160, overflowY: 'auto' }}>
              {dataset.rows.slice(0, 30).map((r: any, i: number) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: i < Math.min(30, dataset.rows.length) - 1 ? '1px solid var(--nd-border)' : 'none', fontSize: 11 }}>
                  <span style={{ fontWeight: 600, color: 'var(--nd-text-1)', minWidth: 90 }}>{r.symbol}</span>
                  <span style={{ color: 'var(--nd-text-3)' }}>{r.date}</span>
                  <span style={{ marginLeft: 'auto', color: 'var(--nd-text-2)' }}>{(r.ticks ?? 0).toLocaleString()} ticks</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ═══ 3. AGENT LEARNING ══════════════════════════════════════════ */}
      {learning && (
        <div className="nd-pm-card" style={{ borderLeft: '3px solid var(--nd-green)' }}>

          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
            <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-green)' }}>school</span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Agent Learning</h3>
            {(learning.totals?.recentOutcomes24h ?? 0) > 0 && (
              <span style={{
                fontSize: 10, fontWeight: 600,
                color: 'var(--nd-green)',
                background: 'rgba(0,179,134,0.1)',
                border: '1px solid rgba(0,179,134,0.25)',
                borderRadius: 20, padding: '2px 10px', whiteSpace: 'nowrap',
              }}>
                ● {learning.totals.recentOutcomes24h} trained in last 24h
              </span>
            )}
          </div>

          {/* 4 counters — 2×2 on mobile, 4×1 on desktop */}
          <div className="nd-pm-stats-grid" style={{ marginBottom: 20 }}>
            {[
              { label: 'Predictions', value: (learning.totals?.predictions ?? 0).toLocaleString(), color: 'var(--nd-text-1)' },
              { label: 'Outcomes learned', value: (learning.totals?.outcomes ?? 0).toLocaleString(), color: 'var(--nd-text-1)' },
              { label: 'Overall accuracy',
                value: `${((learning.overallAccuracy ?? 0) * 100).toFixed(1)}%`,
                color: (learning.overallAccuracy ?? 0) >= 0.5 ? 'var(--nd-green)' : '#e74c3c' },
              { label: 'Memory cases', value: (learning.memoryCases ?? 0).toLocaleString(), color: 'var(--nd-text-1)' },
            ].map(s => (
              <div key={s.label} style={{
                background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                borderRadius: 10, padding: '12px 14px',
              }}>
                <div style={{
                  fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
                  letterSpacing: '0.4px', color: 'var(--nd-text-3)', marginBottom: 6,
                }}>{s.label}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: s.color }}>{s.value}</div>
              </div>
            ))}
          </div>

          {/* Per-agent ranked list — 2-row compact cards, no fixed widths */}
          {sortedAgents.length > 0 && (
            <>
              <div style={{
                fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
                letterSpacing: '0.5px', color: 'var(--nd-text-3)', marginBottom: 10,
              }}>
                Agent Rankings — sorted by weight
              </div>
              {modelError && (
                <div style={{
                  marginBottom: 8, padding: '8px 12px',
                  background: 'rgba(231,76,60,0.1)', border: '1px solid rgba(231,76,60,0.3)',
                  borderRadius: 8, fontSize: 12, color: '#e74c3c',
                  display: 'flex', alignItems: 'center', gap: 8,
                }}>
                  <span className="material-icons" style={{ fontSize: 14, flexShrink: 0 }}>error_outline</span>
                  {modelError}
                  <button onClick={() => setModelError(null)} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: '#e74c3c', padding: 0 }}>
                    <span className="material-icons" style={{ fontSize: 14 }}>close</span>
                  </button>
                </div>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {sortedAgents.map((a: any, i: number) => {
                  const acc = a.accuracy as number;
                  const pct = Math.round(acc * 100);
                  const col = accuracyColor(acc);
                  const bgCol = accuracyBg(acc);
                  const mdl = models.find(m => m.name === a.agent);
                  const busy = modelBusy === a.agent;
                  const effectiveWeight = mdl?.weight ?? a.weight;
                  const hasPinnedWeight = mdl != null && mdl.weight != null;
                  return (
                    <div key={a.agent} style={{
                      background: mdl && !mdl.enabled ? 'rgba(100,116,139,0.06)' : 'var(--nd-bg)',
                      border: `1px solid ${mdl && !mdl.enabled ? '#475569' : 'var(--nd-border)'}`,
                      borderRadius: 10, overflow: 'hidden',
                      opacity: mdl && !mdl.enabled ? 0.7 : 1,
                      transition: 'opacity 0.2s',
                    }}>
                      {/* ── Clickable top area: opens detail popup ── */}
                      <div onClick={() => setAgentPopup({ agent: a, rank: i + 1 })}
                        style={{ padding: '10px 12px', cursor: 'pointer' }}>
                        {/* Top row: rank + name + accuracy badge + weight badge + chevron */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                          <span style={{
                            fontSize: 10, fontWeight: 700, color: 'var(--nd-text-3)',
                            flexShrink: 0, width: 18, textAlign: 'center',
                          }}>#{i + 1}</span>
                          <span style={{
                            fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)',
                            flex: 1, minWidth: 0, textTransform: 'capitalize',
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                          }}>{a.agent.replace(/_/g, ' ')}</span>
                          <span style={{
                            fontSize: 12, fontWeight: 700, color: col,
                            background: bgCol, borderRadius: 6, padding: '2px 8px',
                            flexShrink: 0, minWidth: 42, textAlign: 'center',
                          }}>{pct}%</span>
                          <span style={{
                            fontSize: 10, fontWeight: 600,
                            color: hasPinnedWeight ? 'var(--nd-orange, #f59e0b)' : 'var(--nd-text-2)',
                            background: 'var(--nd-surface)',
                            border: `1px solid ${hasPinnedWeight ? 'var(--nd-orange, #f59e0b)' : 'var(--nd-border)'}`,
                            borderRadius: 6, padding: '2px 8px',
                            flexShrink: 0, minWidth: 48, textAlign: 'center',
                          }} title={hasPinnedWeight ? `Manual override — auto would be w${a.weightLearned ?? '—'}` : 'Learned weight'}>
                            w{effectiveWeight}
                          </span>
                          <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)', flexShrink: 0 }}>chevron_right</span>
                        </div>
                        {/* Accuracy bar */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <div style={{ flex: 1, height: 5, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden' }}>
                            <div style={{ width: `${pct}%`, height: '100%', background: col, borderRadius: 3, transition: 'width 0.4s ease' }} />
                          </div>
                          <span style={{ fontSize: 10, color: 'var(--nd-text-3)', flexShrink: 0, whiteSpace: 'nowrap' }}>
                            {a.correct}/{a.total}
                          </span>
                        </div>
                      </div>

                      {/* ── Controls row: isolated from card click ── */}
                      {mdl && (
                        <div style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          padding: '8px 12px', borderTop: '1px solid var(--nd-border)',
                          background: 'var(--nd-surface)',
                        }}>
                          {/* Enable / disable toggle */}
                          <button
                            onClick={() => toggleModel(mdl)}
                            disabled={busy}
                            style={{
                              background: mdl.enabled ? 'rgba(0,179,134,0.15)' : 'rgba(100,116,139,0.15)',
                              border: `1px solid ${mdl.enabled ? 'rgba(0,179,134,0.5)' : '#475569'}`,
                              borderRadius: 7, padding: '5px 12px',
                              fontSize: 11, fontWeight: 700,
                              color: mdl.enabled ? 'var(--nd-green)' : '#94a3b8',
                              cursor: busy ? 'not-allowed' : 'pointer',
                              display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0,
                              transition: 'all 0.15s',
                            }}
                          >
                            <span className="material-icons" style={{ fontSize: 14 }}>
                              {mdl.enabled ? 'toggle_on' : 'toggle_off'}
                            </span>
                            {mdl.enabled ? 'On' : 'Off'}
                          </button>
                          {/* Weight number input — no max ceiling */}
                          <span style={{ fontSize: 11, color: 'var(--nd-text-3)', flexShrink: 0 }}>Weight</span>
                          <input
                            type="number"
                            min={0} step={0.1}
                            value={mdl.weight ?? a.weight}
                            onChange={e => {
                              const v = parseFloat(e.target.value);
                              if (!isNaN(v) && v >= 0) setWeightOverride(mdl, v);
                            }}
                            style={{
                              width: 64, padding: '4px 8px',
                              background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                              borderRadius: 7, fontSize: 12, fontWeight: 600,
                              color: 'var(--nd-text-1)', outline: 'none',
                            }}
                          />
                          {mdl.weight !== null && (
                            <button
                              onClick={() => setWeightOverride(mdl, null)}
                              disabled={busy}
                              title="Clear override — revert to learned weight"
                              style={{
                                background: 'none', border: '1px solid var(--nd-border)',
                                borderRadius: 7, padding: '4px 8px',
                                fontSize: 11, color: 'var(--nd-text-3)',
                                cursor: busy ? 'not-allowed' : 'pointer',
                              }}
                            >
                              Auto
                            </button>
                          )}
                          {busy && (
                            <span className="material-icons nd-spin" style={{ fontSize: 14, color: 'var(--nd-text-3)', marginLeft: 'auto' }}>
                              autorenew
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}

          <p style={{ margin: '14px 0 0', fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.65 }}>
            Every{' '}
            <strong style={{ color: 'var(--nd-text-2)' }}>backtest</strong>,{' '}
            <strong style={{ color: 'var(--nd-text-2)' }}>paper trade</strong> and{' '}
            <strong style={{ color: 'var(--nd-text-2)' }}>live session</strong>{' '}
            updates these weights, the RL policy, and the memory bank.
          </p>
        </div>
      )}

      {/* ═══ 4. REFRESH CONTROL ═════════════════════════════════════════ */}
      <div className="nd-pm-card">
        {/* Title row + button */}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 0 }}>
          <div style={{ flex: 1, minWidth: 180 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
              <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-2)' }}>autorenew</span>
              <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Refresh from latest data</span>
            </div>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
              Replays real backtests and rebuilds the bank from the freshest candles.
              Runs <strong style={{ color: 'var(--nd-text-2)' }}>automatically every night (~02:00 IST)</strong> — or trigger it now.
            </p>
          </div>
          <button
            className="nd-btn nd-btn-primary"
            onClick={runSweep}
            disabled={sweeping}
            style={{ borderRadius: 9, padding: '10px 18px', fontSize: 13, fontWeight: 600, gap: 7, flexShrink: 0 }}
          >
            <span className="material-icons" style={{ fontSize: 15, animation: sweeping ? 'nd-spin 0.9s linear infinite' : 'none' }}>
              refresh
            </span>
            {sweeping ? 'Refreshing…' : 'Refresh Now'}
          </button>
        </div>

        {sweeping && (
          <div style={{
            marginTop: 12, display: 'flex', alignItems: 'center', gap: 8,
            fontSize: 12, color: 'var(--nd-text-2)',
            background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
            borderRadius: 8, padding: '10px 12px',
          }}>
            <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-green)', animation: 'nd-spin 0.9s linear infinite' }}>autorenew</span>
            Running real backtests across the watchlist — this takes a minute or two…
          </div>
        )}

        {!sweeping && lastSweep && (
          <div style={{
            marginTop: 12,
            display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap',
            fontSize: 11, color: 'var(--nd-text-3)',
            background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
            borderRadius: 8, padding: '8px 12px',
          }}>
            <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-green)' }}>check_circle</span>
            Last refresh:
            <span style={{ color: 'var(--nd-text-2)' }}>{new Date(lastSweep.finishedAt).toLocaleString()}</span>
            <span style={{ color: 'var(--nd-border)' }}>·</span>
            {(lastSweep.casesInserted ?? 0).toLocaleString()} cases
            <span style={{ color: 'var(--nd-border)' }}>·</span>
            {lastSweep.backtestsOk ?? 0} backtests
            {lastSweep.durationSecs != null && <>{' · '}{lastSweep.durationSecs}s</>}
          </div>
        )}

        {/* Dense seed — secondary action */}
        <div style={{
          marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--nd-border)',
          display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        }}>
          <button
            className="nd-btn nd-btn-outline"
            onClick={runSeed}
            disabled={seeding}
            style={{ fontSize: 12, padding: '7px 14px', borderRadius: 8, gap: 6 }}
          >
            <span className="material-icons" style={{ fontSize: 14, animation: seeding ? 'nd-spin 0.9s linear infinite' : 'none' }}>
              {seeding ? 'autorenew' : 'download'}
            </span>
            {seeding ? 'Seeding…' : 'Dense seed (forward-return labels)'}
          </button>
          {seedMsg && (
            <span style={{
              fontSize: 11,
              color: seedMsg.startsWith('✓') ? 'var(--nd-green)'
                   : seedMsg.startsWith('✗') ? '#e74c3c'
                   : 'var(--nd-text-3)',
            }}>{seedMsg}</span>
          )}
        </div>
      </div>

      {/* ═══ 5. BREAKDOWN ═══════════════════════════════════════════════ */}
      <div className="nd-pm-breakdown" style={{ gap: 20 }}>

        {/* By Action */}
        <div className="nd-pm-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 14 }}>
            <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-text-3)' }}>swap_vert</span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>By Action</h3>
          </div>
          {!stats || !stats.byAction.length ? (
            <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-3)' }}>No cases yet — seed the bank to begin.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {stats.byAction.map(a => {
                const wr = Math.round(a.winRate * 100);
                const col = a.action === 'BUY' ? 'var(--nd-green)'
                           : a.action === 'SELL' ? '#e74c3c'
                           : 'var(--nd-text-3)';
                const colAlpha = a.action === 'BUY' ? 'rgba(0,179,134,0.12)' : 'rgba(231,76,60,0.12)';
                return (
                  <div key={a.action} style={{
                    background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                    borderRadius: 10, padding: '12px 14px',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{
                          fontSize: 11, fontWeight: 700, color: col,
                          background: colAlpha, borderRadius: 5,
                          padding: '2px 8px', letterSpacing: '0.5px',
                        }}>{a.action}</span>
                        <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{a.count.toLocaleString()} cases</span>
                      </div>
                      <span style={{ fontSize: 18, fontWeight: 700, color: wr >= 50 ? 'var(--nd-green)' : 'var(--nd-text-2)' }}>
                        {wr}%
                      </span>
                    </div>
                    <div style={{ height: 6, background: 'var(--nd-border)', borderRadius: 4, overflow: 'hidden', marginBottom: 6 }}>
                      <div style={{ width: `${wr}%`, height: '100%', borderRadius: 4, background: wr >= 50 ? 'var(--nd-green)' : 'var(--nd-text-3)' }} />
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Win rate</span>
                      <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{100 - wr}% loss</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* By Source + Top Symbols stacked */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

          {/* By Source */}
          <div className="nd-pm-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 14 }}>
              <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-text-3)' }}>account_tree</span>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>By Source</h3>
            </div>
            {!stats || !stats.bySource.length ? (
              <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-3)' }}>No cases yet.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {stats.bySource.map(s => {
                  const meta = SOURCE_META[s.source] ?? { icon: 'storage' };
                  const wr = Math.round(s.winRate * 100);
                  return (
                    <div key={s.source} style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                      borderRadius: 10, padding: '10px 12px',
                    }}>
                      <div style={{
                        width: 32, height: 32, borderRadius: 8, flexShrink: 0,
                        background: 'var(--nd-surface)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-text-2)' }}>{meta.icon}</span>
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 5 }}>
                          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--nd-text-1)', textTransform: 'capitalize' }}>
                            {s.source.charAt(0) + s.source.slice(1).toLowerCase()}
                          </span>
                          <span style={{ fontSize: 12, fontWeight: 700, color: wr >= 50 ? 'var(--nd-green)' : 'var(--nd-text-2)' }}>
                            {wr}% win
                          </span>
                        </div>
                        <div style={{ height: 4, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden', marginBottom: 4 }}>
                          <div style={{ width: `${wr}%`, height: '100%', borderRadius: 3, background: wr >= 50 ? 'var(--nd-green)' : 'var(--nd-text-3)' }} />
                        </div>
                        <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{s.count.toLocaleString()} cases</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Top Symbols */}
          <div className="nd-pm-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 14 }}>
              <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-text-3)' }}>bar_chart</span>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>Top Symbols</h3>
            </div>
            {!stats || !stats.topSymbols.length ? (
              <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-3)' }}>—</p>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {stats.topSymbols.map((s, i) => (
                  <div key={s.symbol} style={{
                    display: 'flex', alignItems: 'center', gap: 5,
                    background: i < 3 ? 'rgba(0,179,134,0.08)' : 'var(--nd-bg)',
                    border: `1px solid ${i < 3 ? 'rgba(0,179,134,0.22)' : 'var(--nd-border)'}`,
                    borderRadius: 7, padding: '4px 9px',
                  }}>
                    <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-1)' }}>{s.symbol}</span>
                    <span style={{
                      fontSize: 10, fontWeight: 700,
                      color: i < 3 ? 'var(--nd-green)' : 'var(--nd-text-3)',
                      background: i < 3 ? 'rgba(0,179,134,0.15)' : 'var(--nd-surface)',
                      borderRadius: 4, padding: '1px 5px',
                    }}>{s.count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ═══ 6. GBM TRAINER ════════════════════════════════════════════ */}
      {models.some(m => m.name === 'gbm') && (
        <div className="nd-pm-card" style={{ borderLeft: '3px solid #22c55e' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <span className="material-icons" style={{ fontSize: 18, color: '#22c55e' }}>account_tree</span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>GBM Trainer</h3>
            {models.find(m => m.name === 'gbm')?.trained && (
              <span style={{
                fontSize: 10, fontWeight: 600, color: '#22c55e',
                background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)',
                borderRadius: 20, padding: '2px 10px',
              }}>Trained</span>
            )}
          </div>
          <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
            Train the Gradient Boosting Classifier on your latest memory bank data. Requires ≥250 labelled cases.
            Runs in the background (~30 s) — accuracy and AUC will update once complete.
          </p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <button
              className="nd-btn nd-btn-primary"
              onClick={trainGbm}
              disabled={training}
              style={{ borderRadius: 9, padding: '9px 18px', fontSize: 13, fontWeight: 600, gap: 7, background: '#22c55e', borderColor: '#22c55e' }}
            >
              <span className="material-icons" style={{ fontSize: 15, animation: training ? 'nd-spin 0.9s linear infinite' : 'none' }}>
                {training ? 'autorenew' : 'play_arrow'}
              </span>
              {training ? 'Training…' : 'Train GBM'}
            </button>
            {trainMsg && (
              <span style={{ fontSize: 12, color: trainMsg.ok ? 'var(--nd-green)' : '#e74c3c', lineHeight: 1.5 }}>
                {trainMsg.text}
              </span>
            )}
          </div>
        </div>
      )}

      {/* ═══ AGENT DETAIL POPUP ═════════════════════════════════════════ */}
      {agentPopup && (
        <AgentDetailSheet agent={agentPopup.agent} rank={agentPopup.rank} onClose={() => setAgentPopup(null)} />
      )}
    </div>
  );
};

// ── Agent detail bottom-sheet (proper component so it can hold state) ─────────

const AgentDetailSheet: React.FC<{ agent: any; rank: number; onClose: () => void }> = ({ agent: a, rank, onClose }) => {
  const [expandedAction, setExpandedAction] = useState<string | null>(null);

  const acc   = a.accuracy as number;
  const pct   = Math.round(acc * 100);
  const col   = accuracyColor(acc);
  const bgCol = accuracyBg(acc);
  const wrong = (a.total ?? 0) - (a.correct ?? 0);
  const meta  = AGENT_META[a.agent?.toLowerCase()] ?? {
    icon: 'smart_toy', label: a.agent, sources: [], signals: [],
    definition: 'AI agent contributing to the ensemble consensus.',
    edge: '',
  };

  const ACTION_COLOR: Record<string, string> = { BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b' };
  const ACTION_BG:    Record<string, string> = { BUY: 'rgba(34,197,94,0.08)', SELL: 'rgba(239,68,68,0.08)', HOLD: 'rgba(245,158,11,0.08)' };

  const byAction: any[] = a.byAction ?? a.by_action ?? [];
  const sortedActions   = [...byAction].sort((x, y) => ['BUY','SELL','HOLD'].indexOf(x.action) - ['BUY','SELL','HOLD'].indexOf(y.action));
  const totalVotes      = sortedActions.reduce((s: number, x: any) => s + (x.total || 0), 0);

  return (
    <>
      {/* Backdrop */}
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.75)', touchAction: 'none' }} />

      {/* Sheet */}
      <div
        onClick={e => e.stopPropagation()}
        style={{
          position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)',
          zIndex: 1001, width: '100%', maxWidth: 540,
          background: 'var(--nd-surface)', borderRadius: '20px 20px 0 0',
          border: '1px solid var(--nd-border)', borderBottom: 'none',
          paddingBottom: 'calc(20px + env(safe-area-inset-bottom, 0px))',
          maxHeight: '88vh', overflowY: 'auto',
          WebkitOverflowScrolling: 'touch' as any, touchAction: 'pan-y',
        }}
      >
        {/* Drag handle */}
        <div style={{ position: 'sticky', top: 0, zIndex: 2, background: 'var(--nd-surface)', display: 'flex', justifyContent: 'center', padding: '10px 0 6px' }}>
          <div style={{ width: 36, height: 4, borderRadius: 2, background: 'var(--nd-border)' }} />
        </div>

        {/* Header */}
        <div style={{ position: 'sticky', top: 30, zIndex: 2, background: 'var(--nd-surface)', display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px 14px', borderBottom: '1px solid var(--nd-border)' }}>
          <div style={{ width: 44, height: 44, borderRadius: 12, flexShrink: 0, background: bgCol, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span className="material-icons" style={{ color: col, fontSize: 22 }}>{meta.icon}</span>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3 }}>
              <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)', textTransform: 'capitalize', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.agent}</span>
              <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--nd-text-3)', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 5, padding: '1px 6px', flexShrink: 0 }}>#{rank}</span>
            </div>
            <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{meta.label}</span>
          </div>
          <button onClick={onClose} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, width: 32, height: 32, cursor: 'pointer', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-2)' }}>close</span>
          </button>
        </div>

        {/* Scrollable body */}
        <div style={{ padding: '16px 16px 0', display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* Accuracy hero */}
          <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 14, padding: '14px 16px' }}>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--nd-text-3)', marginBottom: 6 }}>Live Accuracy</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
              <span style={{ fontSize: 38, fontWeight: 800, color: col, lineHeight: 1 }}>{pct}%</span>
              <span style={{ fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>{a.correct} correct<br />{a.total} predictions</span>
            </div>
            <div style={{ height: 8, background: 'var(--nd-border)', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{ width: `${pct}%`, height: '100%', background: col, borderRadius: 4 }} />
            </div>
          </div>

          {/* 2×2 stats */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {[
              { label: 'Weight',  value: String(a.weight),       icon: 'balance',      iconCol: 'var(--nd-text-2)' },
              { label: 'Rank',    value: `#${rank}`,              icon: 'leaderboard',  iconCol: 'var(--nd-text-2)' },
              { label: 'Correct', value: String(a.correct ?? 0), icon: 'check_circle', iconCol: 'var(--nd-green)'  },
              { label: 'Wrong',   value: String(wrong),           icon: 'cancel',       iconCol: '#e74c3c'          },
            ].map(s => (
              <div key={s.label} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
                <span className="material-icons" style={{ fontSize: 22, color: s.iconCol, flexShrink: 0 }}>{s.icon}</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-text-1)', lineHeight: 1, marginBottom: 4 }}>{s.value}</div>
                  <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--nd-text-3)' }}>{s.label}</div>
                </div>
              </div>
            ))}
          </div>

          {/* ── BUY / SELL contribution ── */}
          {sortedActions.length > 0 && (
            <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '14px 16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
                <span className="material-icons" style={{ fontSize: 14, color: '#a855f7' }}>pie_chart</span>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#a855f7', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Vote Contribution to Training
                </span>
              </div>

              {/* Stacked proportion bar */}
              {totalVotes > 0 && (
                <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', marginBottom: 12, gap: 1 }}>
                  {sortedActions.map((x: any) => (
                    <div key={x.action} title={`${x.action}: ${x.total} (${((x.total / totalVotes) * 100).toFixed(0)}%)`}
                      style={{ flex: x.total, background: ACTION_COLOR[x.action] ?? '#64748b', minWidth: x.total > 0 ? 2 : 0 }} />
                  ))}
                </div>
              )}

              {/* Per-action cards — clickable for BUY/SELL */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {sortedActions.map((x: any) => {
                  const sharePct  = totalVotes > 0 ? ((x.total / totalVotes) * 100).toFixed(0) : '0';
                  const correct   = x.correct ?? 0;
                  const wrongCnt  = x.total - correct;
                  const ratePct   = x.total > 0 ? Math.round(x.rate * 100) : null;
                  const avgPnl    = x.avgPnl ?? x.avg_pnl;
                  const acCol     = ACTION_COLOR[x.action] ?? '#94a3b8';
                  const acBg      = ACTION_BG[x.action]    ?? 'var(--nd-surface)';
                  const isExpanded = expandedAction === x.action;
                  const canExpand  = x.action !== 'HOLD';
                  return (
                    <div key={x.action}
                      onClick={() => canExpand && setExpandedAction(isExpanded ? null : x.action)}
                      style={{ background: acBg, border: `1px solid ${acCol}${isExpanded ? '60' : '30'}`, borderRadius: 10, padding: '10px 14px', cursor: canExpand ? 'pointer' : 'default', transition: 'border-color 0.15s' }}
                    >
                      {/* Row 1: badge + total + share + avg pnl + chevron */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                        <span style={{ fontSize: 11, fontWeight: 800, padding: '2px 8px', borderRadius: 5, background: `${acCol}20`, color: acCol, border: `1px solid ${acCol}40` }}>{x.action}</span>
                        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>{x.total.toLocaleString()}</span>
                        <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>decisions ({sharePct}%)</span>
                        {avgPnl != null && x.action !== 'HOLD' && (
                          <span style={{ marginLeft: 'auto', fontSize: 12, fontWeight: 600, color: avgPnl >= 0 ? '#22c55e' : '#ef4444' }}>
                            {avgPnl >= 0 ? '+' : ''}{avgPnl}% avg P&L
                          </span>
                        )}
                        {canExpand && (
                          <span className="material-icons" style={{ fontSize: 16, color: acCol, flexShrink: 0, marginLeft: avgPnl != null ? 4 : 'auto' }}>
                            {isExpanded ? 'expand_less' : 'show_chart'}
                          </span>
                        )}
                      </div>

                      {/* Row 2: correct / wrong / accuracy */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <span className="material-icons" style={{ fontSize: 13, color: '#22c55e' }}>check_circle</span>
                          <span style={{ fontSize: 13, fontWeight: 700, color: '#22c55e' }}>{correct.toLocaleString()}</span>
                          <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>correct</span>
                        </div>
                        <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>·</span>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <span className="material-icons" style={{ fontSize: 13, color: '#ef4444' }}>cancel</span>
                          <span style={{ fontSize: 13, fontWeight: 700, color: '#ef4444' }}>{wrongCnt.toLocaleString()}</span>
                          <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>wrong</span>
                        </div>
                        {ratePct != null && (
                          <div style={{ marginLeft: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
                            <span style={{ fontSize: 16, fontWeight: 800, color: ratePct >= 50 ? '#22c55e' : '#ef4444', lineHeight: 1 }}>{ratePct}%</span>
                            <div style={{ width: 72, height: 4, background: 'var(--nd-border)', borderRadius: 2 }}>
                              <div style={{ height: '100%', width: `${ratePct}%`, background: ratePct >= 50 ? '#22c55e' : '#ef4444', borderRadius: 2 }} />
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Expanded trend chart */}
                      {isExpanded && (
                        <div style={{ marginTop: 14, paddingTop: 14, borderTop: `1px solid ${acCol}30` }}
                          onClick={e => e.stopPropagation()}>
                          <div style={{ fontSize: 11, fontWeight: 700, color: acCol, marginBottom: 10 }}>
                            Accuracy over time — {x.action} decisions
                          </div>
                          <ActionTrendChart agentName={a.agent} action={x.action} color={acCol} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 8, lineHeight: 1.5 }}>
                Tap BUY or SELL to see the accuracy trend over time. "Correct" = vote direction matched the actual trade result.
              </div>
            </div>
          )}

          {/* ── Definition ── */}
                <div style={{
                  background: 'rgba(0,179,134,0.05)',
                  border: '1px solid rgba(0,179,134,0.18)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                    <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-green)' }}>auto_stories</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-green)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      What it is
                    </span>
                  </div>
                  <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-1)', lineHeight: 1.7 }}>{meta.definition}</p>
                </div>

                {/* ── Data Sources ── */}
                <div style={{
                  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                    <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-2)' }}>database</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-2)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      Data Sources
                    </span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {meta.sources.map((src, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                        <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-text-3)', marginTop: 2, flexShrink: 0 }}>fiber_manual_record</span>
                        <span style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.5 }}>{src}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* ── Signals it reads ── */}
                <div style={{
                  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                    <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-2)' }}>sensors</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-2)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      Signals &amp; Indicators
                    </span>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {meta.signals.map((sig, i) => (
                      <span key={i} style={{
                        fontSize: 11, color: 'var(--nd-text-1)',
                        background: 'var(--nd-surface)', border: '1px solid var(--nd-border)',
                        borderRadius: 6, padding: '4px 10px', lineHeight: 1.4,
                      }}>{sig}</span>
                    ))}
                  </div>
                </div>

                {/* ── Best used for ── */}
                <div style={{
                  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                    <span className="material-icons" style={{ fontSize: 14, color: '#f59e0b' }}>lightbulb</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: '#f59e0b', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      When it shines
                    </span>
                  </div>
                  <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-2)', lineHeight: 1.65 }}>{meta.edge}</p>
                </div>

                {/* ── Ensemble influence ── */}
                <div style={{
                  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)' }}>workspaces</span>
                      <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                        Ensemble influence
                      </span>
                    </div>
                    <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)' }}>w{a.weight}</span>
                  </div>
                  <div style={{ height: 8, background: 'var(--nd-border)', borderRadius: 4, overflow: 'hidden', marginBottom: 8 }}>
                    <div style={{
                      width: `${Math.min(100, (a.weight / 5) * 100)}%`,
                      height: '100%', background: 'var(--nd-green)', borderRadius: 4,
                    }} />
                  </div>
                  <p style={{ margin: 0, fontSize: 11, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
                    Weight is updated after every completed session. Higher accuracy → higher weight → more influence on the final trade decision.
                  </p>
                </div>

              </div>
            </div>
    </>
  );
};

export default PatternMemory;
