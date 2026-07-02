// Shared types, constants and helpers used by PatternMemory and its extracted components.

export interface SrcStat    { source: string; count: number; winRate: number; }
export interface ActionStat { action: string; count: number; winRate: number; avgPnl: number; }
export interface SymStat    { symbol: string; count: number; }
export interface MemStats {
  totalCases: number;
  bySource: SrcStat[];
  byAction: ActionStat[];
  topSymbols: SymStat[];
}

export const accuracyColor = (acc: number) =>
  acc >= 0.55 ? 'var(--nd-green)' : acc >= 0.45 ? 'var(--nd-orange)' : '#e74c3c';

export const accuracyBg = (acc: number) =>
  acc >= 0.55 ? 'rgba(0,179,134,0.12)' : acc >= 0.45 ? 'rgba(245,166,35,0.12)' : 'rgba(231,76,60,0.12)';

export const SOURCE_META: Record<string, { icon: string }> = {
  BACKTEST: { icon: 'history_edu' },
  REPLAY:   { icon: 'replay'      },
  PAPER:    { icon: 'receipt_long'},
  LIVE:     { icon: 'bolt'        },
};

export interface AgentMeta {
  icon:       string;
  label:      string;
  definition: string;
  sources:    string[];
  signals:    string[];
  edge:       string;
}

export const AGENT_META: Record<string, AgentMeta> = {
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

export interface ModelRow { name: string; label: string; kind: string; desc: string; enabled: boolean; weight: number | null; trained?: boolean; meta?: any; }
