export interface Stock {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  high: number;
  low: number;
  volume: number;
  marketCap?: string;
  peRatio?: number;
  sector?: string;
}

export interface Prediction {
  symbol: string;
  prediction: 'UP' | 'DOWN' | 'NEUTRAL';
  confidence: number;
  targetPrice: number;
  currentPrice: number;
  stopLoss: number;
  upsidePotential: number;
  riskRewardRatio: number;
  timeframe: string;
  reasoning: string;
  factors: string[];
  timestamp: string;
}

export interface PortfolioStock {
  symbol: string;
  quantity: number;
  purchasePrice: number;
  currentPrice: number;
  value: number;
  gain: number;
  gainPercent: number;
}

export interface Portfolio {
  totalValue: number;
  totalInvested: number;
  totalGain: number;
  gainPercent: number;
  dayChange?: number;
  dayChangePercent?: number;
  stocks: PortfolioStock[];
  cashAvailable: number;
  updatedAt: string;
}

export interface Alert {
  id: number;
  symbol: string;
  alertType: 'price' | 'pattern' | 'sentiment';
  condition: string;
  enabled: boolean;
  createdAt: string;
}

export interface Candlestick {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SentimentData {
  symbol: string;
  overallSentiment: number;
  newsSentiment: number;
  socialMediaSentiment: number;
  analystRating: number;
  buyCount: number;
  sellCount: number;
  holdCount: number;
  updatedAt: string;
}

export interface Performance {
  dailyReturn: number;
  weeklyReturn: number;
  monthlyReturn: number;
  yearlyReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  winRate: number;
  averageTradeReturn: number;
  updatedAt: string;
}

export interface ApiResponse<T> {
  status: 'success' | 'error';
  data?: T;
  message?: string;
}

// ── Auth ─────────────────────────────────────────────────────────────────────

export type BrokerType = 'groww' | 'zerodha' | 'angelone' | 'upstox';

export interface LoginRequest {
  identifier: string;
  password: string;
}

export interface AuthResponse {
  token: string;
  broker: BrokerType;
  expires_at: string;
  user_id: number;
  name: string;
  email: string;
}

/** @deprecated kept for type compatibility — use AuthResponse */
export type LoginResponse = AuthResponse;

export interface BrokerInfo {
  id: BrokerType;
  name: string;
  logo: string;
  color: string;
  available: boolean;
  tagline: string;
}

// ── Signup ────────────────────────────────────────────────────────────────────

export interface SignupSendOtpRequest {
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  password: string;
  confirm_password: string;
}

export interface SignupVerifyOtpRequest {
  email: string;
  otp: string;
}

export interface SignupCompleteRequest {
  email: string;
  broker: BrokerType;
  api_key: string;
  api_secret: string;
}

// ── Risk Analytics ──────────────────────────────────────────────────────────

export interface HoldingVaR {
  symbol: string;
  name: string;
  weight: number;
  beta: number;
  varContribution: number;
}

export interface RiskMetrics {
  portfolioValue: number;
  asOf: string;
  var951Day: number;
  var991Day: number;
  var9510Day: number;
  var9910Day: number;
  cvar95: number;
  cvar99: number;
  portfolioBeta: number;
  annualizedVolatility: number;
  sharpeRatio: number;
  sortinoRatio: number;
  maxDrawdown: number;
  trackingError: number;
  informationRatio: number;
  holdingsVar: HoldingVaR[];
}

export interface HoldingImpact {
  symbol: string;
  return: number;
  pnl: number;
}

export interface StressScenario {
  name: string;
  period: string;
  durationDays: number;
  description: string;
  marketReturn: number;
  portfolioReturn: number;
  portfolioPnl: number;
  severity: 'moderate' | 'severe' | 'extreme';
  holdingsImpact: HoldingImpact[];
}

export interface StressTestResult {
  portfolioValue: number;
  asOf: string;
  scenarios: StressScenario[];
}

export interface FactorExposures {
  marketBeta: number;
  sizeSmb: number;
  valueHml: number;
  momentumMom: number;
  quality: number;
}

export interface FactorContributions {
  [key: string]: number;
  market: number;
  size: number;
  value: number;
  momentum: number;
  idiosyncratic: number;
}

export interface HoldingFactors {
  symbol: string;
  name: string;
  weight: number;
  beta: number;
  size: number;
  value: number;
  momentum: number;
  quality: number;
}

export interface FactorAnalysis {
  asOf: string;
  factorExposures: FactorExposures;
  factorContributions: FactorContributions;
  holdingsFactors: HoldingFactors[];
}

export interface OptimizationPortfolio {
  weights: Record<string, number>;
  expectedReturn: number;
  volatility: number;
  sharpeRatio: number;
}

export interface EfficientFrontierPoint {
  return: number;
  volatility: number;
  sharpe: number;
}

export interface RebalancingAction {
  symbol: string;
  currentWeight: number;
  targetWeight: number;
  weightDelta: number;
  action: 'BUY' | 'SELL' | 'HOLD';
  sharesDelta: number;
  estimatedValue: number;
}

// ── Portfolio actions: order placement, baskets, rebalancing ─────────────────

/** A single buy/sell leg the UI is about to submit to /api/orders or /api/portfolio. */
export interface TradeLeg {
  kind?: 'single';
  symbol: string;
  transactionType: 'BUY' | 'SELL';
  quantity: number;
  orderType?: string;
  price?: number;
  exchange?: string;
  product?: string;
  estValue?: number;
}

export interface PendingBasketOrder { kind: 'basket'; legs: TradeLeg[]; label: string; }
export interface PendingSwapOrder {
  kind: 'swap';
  sell: TradeLeg;
  buy: TradeLeg;
  basis: { sell: Record<string, unknown>; buy: Record<string, unknown> };
}
export type PendingOrder = TradeLeg | PendingBasketOrder | PendingSwapOrder;

/** Minimal shape needed to invest a lump sum into an AI fund basket or theme basket. */
export interface InvestableBasket { id: string; name: string; [key: string]: unknown; }

/** One row of a Quick-Invest allocation plan (/api/portfolio/invest-plan). */
export interface InvestPick {
  symbol: string;
  order: { quantity: number; orderType?: string; limitPrice?: number; exchange?: string; product?: string; estValue?: number };
  [key: string]: unknown;
}

/** One row of a fund/theme basket allocation (/api/portfolio/fund-baskets/invest). */
export interface BasketInvestPick {
  symbol: string;
  trade?: { quantity: number; orderType?: string; limitPrice?: number; exchange?: string; product?: string; estValue?: number };
  [key: string]: unknown;
}

/** A trade leg as the AI optimizer returns it — note the backend uses `limitPrice`, not `price`. */
export interface OptimizerTradeLeg {
  quantity: number;
  orderType?: string;
  limitPrice?: number;
  exchange?: string;
  product?: string;
  estValue?: number;
}

/** One row of an AI rebalancing plan (/api/portfolio/optimize), enough to drive a swap/order. */
export interface RebalanceActionLike {
  symbol: string;
  action: string;
  reason?: string;
  currentWeightPct?: number;
  targetWeightPct?: number;
  pnlPct?: number;
  trade?: OptimizerTradeLeg;
  alternative?: {
    symbol: string;
    reason?: string;
    grade?: string;
    winProbability?: number;
    sector?: string;
    sameSector?: boolean;
    scannerReasoning?: string;
    price?: number;
    order?: OptimizerTradeLeg;
    buyQty?: number;
  };
}

export interface AiSignalLike {
  signal?: string;
  health?: string;
  rsi?: number;
  momentumPct?: number;
  smaTrend?: string;
  atrPct?: number;
}

// ── Orders ───────────────────────────────────────────────────────────────────

export interface OrderRequest {
  symbol: string;
  quantity: number;
  transactionType: 'BUY' | 'SELL';
  orderType: 'MARKET' | 'LIMIT';
  price?: number;
  product?: 'CNC' | 'INTRADAY';
  exchange?: string;
}

/** A row from /api/orders/ (today's Groww order book) — looser than OrderResponse
 *  since Groww's own field names (referenceId, segment, status) leak through. */
export interface OrderRecord {
  orderId?: string;
  referenceId?: string;
  symbol: string;
  transactionType: string;
  quantity: number;
  orderType: string;
  status: string;
  segment?: string;
  [key: string]: unknown;
}

export interface OrderResponse {
  orderId: string;
  symbol: string;
  quantity: number;
  transactionType: string;
  orderType: string;
  product: string;
  exchange: string;
  price?: number;
  status: string;
  timestamp: string;
}

// ── AI Agent types ─────────────────────────────────────────────────────────────

export interface AgentStock {
  symbol: string;
  name: string;
  inPortfolio: boolean;
}

export interface TechnicalIndicators {
  currentPrice: number;
  high52w: number;
  low52w: number;
  sma20: number | null;
  sma50: number | null;
  ema12: number | null;
  ema26: number | null;
  macd: number | null;
  macdSignal: number | null;
  macdHistogram: number | null;
  rsi: number | null;
  bbUpper: number | null;
  bbLower: number | null;
  bbMiddle: number | null;
  bbPctB: number | null;
  atr: number | null;
  stochK: number | null;
  volCurrent: number;
  volAvg20: number;
  priceVsSma20: number | null;
  priceVsSma50: number | null;
  candleCount: number;
}

export interface OHLCVCandle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface AIAnalysis {
  symbol: string;
  name: string;
  dataSource: 'groww' | 'simulated';
  candleCount: number;
  indicators: TechnicalIndicators;
  recentCandles: OHLCVCandle[];
  analysis: string;
  modelUsed: string;
  generatedAt: string;
}

// ── Backtest types ─────────────────────────────────────────────────────────────

export interface StrategyParam {
  label: string;
  default: number;
  min: number;
  max: number;
  step: number;
  type: 'int' | 'float';
}

export interface BacktestMetrics {
  initialCapital: number;
  finalValue: number;
  totalReturnPct: number;
  buyHoldReturnPct: number;
  cagr: number;
  sharpeRatio: number;
  maxDrawdownPct: number;
  winRate: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  profitFactor: number;
  avgHoldingDays: number;
  grossProfit: number;
  grossLoss: number;
}

export interface BacktestTrade {
  entryDate: string;
  exitDate: string;
  entryPrice: number;
  exitPrice: number;
  shares: number;
  pnl: number;
  pnlPct: number;
  holdingDays: number;
  type: 'WIN' | 'LOSS';
}

export interface EquityPoint {
  date: string;
  portfolio: number;
  benchmark: number;
}

export interface BacktestResult {
  symbol: string;
  strategy: string;
  strategyName: string;
  startDate: string;
  endDate: string;
  dataSource: 'groww' | 'simulated';
  initialCapital: number;
  commissionPct: number;
  params: Record<string, number>;
  metrics: BacktestMetrics;
  trades: BacktestTrade[];
  equityCurve: EquityPoint[];
  openPosition: boolean;
  candleCount: number;
  generatedAt: string;
}

export interface LiveSignal {
  symbol: string;
  strategy: string;
  signal: 'BUY' | 'SELL' | 'HOLD';
  lastPrice: number;
  indicators: Record<string, number | null>;
  recentSignals: { date: string; signal: string; close: number }[];
  candleCount: number;
  generatedAt: string;
}

// ── AI Watchlist (self-running scanner) ────────────────────────────────────────

export interface WatchlistNews {
  action: string;
  catalyst?: string;
  summary?: string;
  confidence?: number;
  topHeadlines?: string[];
  headlinesCount?: number;
  updatedAt?: string;
}

export interface WatchlistMetrics {
  avgVolume: number;
  relVolume?: number;
  atrPct: number;
  rangePct: number;
  smaTrend?: string;
  sma20?: number;
  macdHist?: number;
  rsi: number;
  momentumPct: number;
  gapPct?: number;
  distFromHighPct?: number;
  marketRegime?: string;
  liquidityScore: number;
  volatilityScore: number;
}

export interface WatchlistAgentVote {
  agent: string;
  action: string;
  reasoning: string;
}

export interface FnoRecommendation {
  optionType: string;
  strike: number | string;
  expiry: string;
  safeDays: number;
  rationale?: string;
}

export interface WatchlistStock {
  symbol: string;
  name?: string;
  price?: number;
  grade?: string;
  winProbability?: number;
  confidence: number;
  action: string;
  deliveryWeeks?: number;
  fnoRecommendation?: FnoRecommendation;
  news?: WatchlistNews;
  metrics?: WatchlistMetrics;
  agents?: WatchlistAgentVote[];
  signalScore?: number;
  reasoning?: string;
}

export interface WatchlistData {
  intraday?: WatchlistStock[];
  delivery?: WatchlistStock[];
  fno?: WatchlistStock[];
  items?: WatchlistStock[];
  gradeCounts?: { A?: number; B?: number; C?: number; D?: number };
  updatedAt?: string;
}

export interface ScanEvalResult {
  symbol: string;
  correct: boolean;
  action: string;
  dayReturnPct: number;
}

export interface ScanEvaluation {
  latest?: {
    date?: string;
    accuracy?: number;
    picks?: number;
    results?: ScanEvalResult[];
    avgRealizedReturnPct?: number;
  };
  overall?: { accuracy?: number; days?: number; picks?: number };
}

export interface ScanDiffMove {
  symbol: string;
  direction: 'up' | 'down';
  delta: number;
  prevRank?: number;
  rank?: number;
  reason?: string;
}

export interface ScanDiffEntry {
  symbol: string;
  rank?: number;
  prevRank?: number;
  reason?: string;
}

export interface ScanDiff {
  available: boolean;
  message?: string;
  moved?: ScanDiffMove[];
  entered?: ScanDiffEntry[];
  dropped?: ScanDiffEntry[];
  counts?: { moved: number; entered: number; dropped: number };
}

export interface OptimizationResult {
  portfolioValue: number;
  asOf: string;
  currentPortfolio: OptimizationPortfolio;
  minVariancePortfolio: OptimizationPortfolio;
  maxSharpePortfolio: OptimizationPortfolio;
  efficientFrontier: EfficientFrontierPoint[];
  rebalancingActions: RebalancingAction[];
}
