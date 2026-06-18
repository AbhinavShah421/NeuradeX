import axios, { AxiosInstance } from 'axios';
import { Stock, Prediction, Portfolio, ApiResponse, RiskMetrics, StressTestResult, FactorAnalysis, OptimizationResult, OrderRequest, OrderResponse, AIAnalysis, AgentStock, BacktestResult, LiveSignal, LoginRequest, AuthResponse, SignupSendOtpRequest, SignupVerifyOtpRequest, SignupCompleteRequest } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || '';

function snakeToCamel(str: string): string {
  return str.replace(/_([a-z0-9])/g, (_, char) => char.toUpperCase());
}

function convertKeys(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(convertKeys);
  if (obj !== null && typeof obj === 'object') {
    return Object.fromEntries(
      Object.entries(obj).map(([k, v]) => [snakeToCamel(k), convertKeys(v)])
    );
  }
  return obj;
}

class ApiService {
  private api: AxiosInstance;

  constructor() {
    this.api = axios.create({
      baseURL: API_BASE_URL,
      headers: { 'Content-Type': 'application/json' },
    });

    // Attach JWT from localStorage on every request
    this.api.interceptors.request.use(config => {
      try {
        const raw = localStorage.getItem('neuradex-auth');
        if (raw) {
          const stored = JSON.parse(raw);
          const token = stored?.state?.token;
          if (token) config.headers['Authorization'] = `Bearer ${token}`;
        }
      } catch {}
      return config;
    });

    this.api.interceptors.response.use(response => {
      response.data = convertKeys(response.data);
      return response;
    });
  }

  // Auth API
  async login(req: LoginRequest): Promise<ApiResponse<AuthResponse>> {
    const response = await this.api.post('/api/auth/login', req);
    return response.data;
  }

  async signupSendOtp(req: SignupSendOtpRequest): Promise<ApiResponse<null>> {
    const response = await this.api.post('/api/auth/signup/send-otp', req);
    return response.data;
  }

  async signupVerifyOtp(req: SignupVerifyOtpRequest): Promise<ApiResponse<null>> {
    const response = await this.api.post('/api/auth/signup/verify-otp', req);
    return response.data;
  }

  async signupComplete(req: SignupCompleteRequest): Promise<ApiResponse<AuthResponse>> {
    const response = await this.api.post('/api/auth/signup/complete', req);
    return response.data;
  }

  async getMe(): Promise<ApiResponse<{ broker: string; email: string; authenticated: boolean }>> {
    const response = await this.api.get('/api/auth/me');
    return response.data;
  }

  async getProfile(): Promise<ApiResponse<{ name: string; email: string; initials: string; accountId: string; broker: string }>> {
    const response = await this.api.get('/api/auth/profile');
    return response.data;
  }

  async getGrowwStatus(): Promise<ApiResponse<{
    status: string; tokenExpiry: string | null; timeRemainingSeconds: number | null;
    failureCount: number; failureReason: string; lastAttempt: string | null; hasToken: boolean;
  }>> {
    const response = await this.api.get('/api/auth/groww/status');
    return response.data;
  }

  async refreshGrowwToken(): Promise<ApiResponse<{ success: boolean; expires: string | null; error?: string }>> {
    const response = await this.api.post('/api/auth/groww/refresh');
    return response.data;
  }

  async updateGrowwCredentials(apiKey: string, apiSecret: string): Promise<ApiResponse<{ success: boolean; expires: string | null; error?: string }>> {
    const response = await this.api.put('/api/auth/groww/credentials', { api_key: apiKey, api_secret: apiSecret });
    return response.data;
  }

  // Stocks API
  async getStocks(): Promise<ApiResponse<Stock[]>> {
    try {
      const response = await this.api.get('/api/stocks/');
      return response.data;
    } catch (error) {
      console.error('Error fetching stocks:', error);
      throw error;
    }
  }

  async getStock(symbol: string): Promise<ApiResponse<Stock>> {
    try {
      const response = await this.api.get(`/api/stocks/${symbol}`);
      return response.data;
    } catch (error) {
      console.error(`Error fetching stock ${symbol}:`, error);
      throw error;
    }
  }

  async getCandlesticks(
    symbol: string,
    period: string = '1h',
    limit: number = 100
  ): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get(
        `/api/stocks/${symbol}/candlesticks`,
        {
          params: { period, limit },
        }
      );
      return response.data;
    } catch (error) {
      console.error(`Error fetching candlesticks for ${symbol}:`, error);
      throw error;
    }
  }

  async getDirectoryList(params: {
    page?: number; limit?: number; q?: string; sector?: string; exchange?: string;
  }): Promise<any> {
    const response = await this.api.get('/api/stocks/directory/list', { params });
    return response.data;
  }

  async getDirectoryPrices(symbols: string[]): Promise<any> {
    const response = await this.api.post('/api/stocks/directory/prices', { symbols });
    return response.data;
  }

  async getSentiment(symbol: string): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get(`/api/stocks/${symbol}/sentiment`);
      return response.data;
    } catch (error) {
      console.error(`Error fetching sentiment for ${symbol}:`, error);
      throw error;
    }
  }

  // Predictions API
  async getPrediction(symbol: string): Promise<ApiResponse<Prediction>> {
    try {
      const response = await this.api.get(`/api/predictions/${symbol}`);
      return response.data;
    } catch (error) {
      console.error(`Error fetching prediction for ${symbol}:`, error);
      throw error;
    }
  }

  async getCustomAnalysis(
    symbol: string,
    timeframe?: string
  ): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post(
        `/api/predictions/${symbol}/custom-analysis`,
        { timeframe }
      );
      return response.data;
    } catch (error) {
      console.error(`Error getting custom analysis for ${symbol}:`, error);
      throw error;
    }
  }

  async getPredictionHistory(
    symbol: string,
    limit: number = 10
  ): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get(
        `/api/predictions/${symbol}/history`,
        { params: { limit } }
      );
      return response.data;
    } catch (error) {
      console.error(`Error fetching prediction history for ${symbol}:`, error);
      throw error;
    }
  }

  async getAccuracyStats(): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get('/api/predictions/accuracy/stats');
      return response.data;
    } catch (error) {
      console.error('Error fetching accuracy stats:', error);
      throw error;
    }
  }

  // Portfolio API
  async getPortfolio(): Promise<ApiResponse<Portfolio>> {
    try {
      const response = await this.api.get('/api/portfolio/');
      return response.data;
    } catch (error) {
      console.error('Error fetching portfolio:', error);
      throw error;
    }
  }

  async addToPortfolio(
    symbol: string,
    quantity: number,
    purchasePrice: number
  ): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/portfolio/add', {
        symbol,
        quantity,
        purchase_price: purchasePrice,
      });
      return response.data;
    } catch (error) {
      console.error('Error adding to portfolio:', error);
      throw error;
    }
  }

  async getPerformance(): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get('/api/portfolio/performance');
      return response.data;
    } catch (error) {
      console.error('Error fetching performance:', error);
      throw error;
    }
  }

  async listOrders(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/orders/', { timeout: 20000 });
    return response.data;
  }

  async cancelOrder(orderId: string, segment = 'CASH'): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/orders/cancel', { order_id: orderId, segment });
    return response.data;
  }

  async investPlan(amount: number, maxStocks = 6): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/invest-plan', {
      params: { amount, max_stocks: maxStocks }, timeout: 20000,
    });
    return response.data;
  }

  async optimizePortfolio(refresh = false): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/optimize', {
      params: { refresh }, timeout: 90000,
    });
    return response.data;
  }

  async sectorExposure(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/sector-exposure', { timeout: 30000 });
    return response.data;
  }

  async fundBaskets(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/fund-baskets', { timeout: 30000 });
    return response.data;
  }

  async investBasket(basket: string, amount: number): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/fund-baskets/invest', {
      params: { basket, amount }, timeout: 30000,
    });
    return response.data;
  }

  async portfolioHealth(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/health', { timeout: 30000 });
    return response.data;
  }
  async sipPlanner(p: { goalAmount?: number; years?: number; risk?: string; currentCorpus?: number; monthly?: number }): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/sip-planner', { params: {
      goal_amount: p.goalAmount ?? 0, years: p.years ?? 10, risk: p.risk ?? 'moderate',
      current_corpus: p.currentCorpus ?? 0, monthly: p.monthly ?? 0,
    } });
    return response.data;
  }
  async taxHarvest(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/tax-harvest', { timeout: 30000 });
    return response.data;
  }
  async portfolioBenchmark(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/benchmark', { timeout: 60000 });
    return response.data;
  }
  async portfolioAdvisor(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/advisor', { timeout: 90000 });
    return response.data;
  }
  async riskLab(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/portfolio/risk-lab', { timeout: 90000 });
    return response.data;
  }

  // ── Mutual Funds (real NAV/returns via AMFI/mfapi) ──
  async mfSearch(q: string): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/mutual-funds/search', { params: { q }, timeout: 25000 });
    return response.data;
  }
  async mfScheme(code: number): Promise<ApiResponse<any>> {
    const response = await this.api.get(`/api/mutual-funds/scheme/${code}`, { timeout: 20000 });
    return response.data;
  }
  async mfHoldings(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/mutual-funds/holdings', { timeout: 40000 });
    return response.data;
  }
  async mfAddHolding(body: { schemeCode: number; units?: number; invested?: number }): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/mutual-funds/holdings', {
      scheme_code: body.schemeCode, units: body.units, invested: body.invested,
    });
    return response.data;
  }
  async mfRemoveHolding(code: number): Promise<ApiResponse<any>> {
    const response = await this.api.delete(`/api/mutual-funds/holdings/${code}`);
    return response.data;
  }
  async mfCategories(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/mutual-funds/categories');
    return response.data;
  }
  async mfScreener(category: string, limit = 20, sort: 'return' | 'risk' = 'return'): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/mutual-funds/screener', { params: { category, limit, sort }, timeout: 60000 });
    return response.data;
  }
  async mfScan(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/mutual-funds/scan', { timeout: 60000 });
    return response.data;
  }
  async mfOptimize(risk = 'moderate'): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/mutual-funds/optimize', { params: { risk }, timeout: 90000 });
    return response.data;
  }
  async mfAll(q = '', page = 1, limit = 25): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/mutual-funds/all', { params: { q, page, limit }, timeout: 60000 });
    return response.data;
  }

  async getAlerts(): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get('/api/portfolio/alerts');
      return response.data;
    } catch (error) {
      console.error('Error fetching alerts:', error);
      throw error;
    }
  }

  async createAlert(
    symbol: string,
    alertType: string,
    condition: string
  ): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/portfolio/alerts', {
        symbol,
        alert_type: alertType,
        condition,
        enabled: true,
      });
      return response.data;
    } catch (error) {
      console.error('Error creating alert:', error);
      throw error;
    }
  }

  // Risk Analytics API
  async getRiskVar(): Promise<ApiResponse<RiskMetrics>> {
    try {
      const response = await this.api.get('/api/risk/var');
      return response.data;
    } catch (error) {
      console.error('Error fetching risk metrics:', error);
      throw error;
    }
  }

  async getStressTest(): Promise<ApiResponse<StressTestResult>> {
    try {
      const response = await this.api.get('/api/risk/stress-test');
      return response.data;
    } catch (error) {
      console.error('Error fetching stress test:', error);
      throw error;
    }
  }

  async getFactorAnalysis(): Promise<ApiResponse<FactorAnalysis>> {
    try {
      const response = await this.api.get('/api/risk/factors');
      return response.data;
    } catch (error) {
      console.error('Error fetching factor analysis:', error);
      throw error;
    }
  }

  async getOptimization(): Promise<ApiResponse<OptimizationResult>> {
    try {
      const response = await this.api.get('/api/risk/optimization');
      return response.data;
    } catch (error) {
      console.error('Error fetching optimization:', error);
      throw error;
    }
  }

  async getOptimizationAnalysis(model?: string): Promise<ApiResponse<{ analysis: string; modelUsed: string; generatedAt: string }>> {
    try {
      const params = model ? { model } : {};
      const response = await this.api.get('/api/risk/optimization/analyze', { params });
      return response.data;
    } catch (error) {
      console.error('Error fetching optimization analysis:', error);
      throw error;
    }
  }

  // Orders API
  async placeOrder(order: OrderRequest): Promise<ApiResponse<OrderResponse>> {
    try {
      const response = await this.api.post('/api/orders/', {
        symbol: order.symbol,
        quantity: order.quantity,
        transaction_type: order.transactionType,
        order_type: order.orderType,
        price: order.price,
        product: order.product ?? 'CNC',
        exchange: order.exchange ?? 'NSE',
      });
      return response.data;
    } catch (error) {
      console.error('Error placing order:', error);
      throw error;
    }
  }

  // AI Agent API
  async getAgentStocks(): Promise<ApiResponse<AgentStock[]>> {
    try {
      const response = await this.api.get('/api/agent/stocks');
      return response.data;
    } catch (error) {
      console.error('Error fetching agent stocks:', error);
      throw error;
    }
  }

  async getOllamaModels(): Promise<{ status: string; data: string[]; current: string; error?: string }> {
    try {
      const response = await this.api.get('/api/agent/models');
      return response.data;
    } catch (error) {
      console.error('Error fetching Ollama models:', error);
      return { status: 'error', data: [], current: 'llama3.2' };
    }
  }

  async analyzeStock(symbol: string, model?: string): Promise<ApiResponse<AIAnalysis>> {
    try {
      const params = model ? { model } : {};
      const response = await this.api.post(`/api/agent/analyze/${symbol}`, null, { params });
      return response.data;
    } catch (error) {
      console.error(`Error analyzing ${symbol}:`, error);
      throw error;
    }
  }

  // Backtest API
  async getStrategies(): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get('/api/backtest/strategies');
      return response.data;
    } catch (error) {
      console.error('Error fetching strategies:', error);
      throw error;
    }
  }

  async runBacktest(payload: {
    symbol: string;
    strategy: string;
    start_date: string;
    end_date: string;
    initial_capital: number;
    commission: number;
    params: Record<string, number>;
  }): Promise<ApiResponse<BacktestResult>> {
    try {
      const response = await this.api.post('/api/backtest/run', payload);
      return response.data;
    } catch (error) {
      console.error('Error running backtest:', error);
      throw error;
    }
  }

  async getLiveSignal(
    symbol: string,
    strategy: string,
    params: Record<string, number>
  ): Promise<ApiResponse<LiveSignal>> {
    try {
      const response = await this.api.get(`/api/backtest/live-signal/${symbol}`, {
        params: { strategy, ...params },
      });
      return response.data;
    } catch (error) {
      console.error('Error fetching live signal:', error);
      throw error;
    }
  }

  async runDayAutopilot(payload: {
    symbol: string;
    date: string;
    capital: number;
    model?: string;
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/backtest/day-autopilot', payload);
      return response.data;
    } catch (error) {
      console.error('Error running day autopilot:', error);
      throw error;
    }
  }

  async getIntradayCandles(symbol: string, date: string): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get(`/api/backtest/intraday-candles/${symbol}`, { params: { date } });
      return response.data;
    } catch (error) {
      console.error('Error fetching intraday candles:', error);
      throw error;
    }
  }

  async agentStep(payload: {
    symbol: string;
    date: string;
    candles: any[];
    position: 'NONE' | 'LONG';
    entryPrice: number;
    entryTime: string;
    entryQty: number;
    capital: number;
    model?: string;
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/backtest/agent-step', {
        symbol:      payload.symbol,
        date:        payload.date,
        candles:     payload.candles,
        position:    payload.position,
        entry_price: payload.entryPrice,
        entry_time:  payload.entryTime,
        entry_qty:   payload.entryQty,
        capital:     payload.capital,
        model:       payload.model,
      });
      return response.data;
    } catch (error) {
      console.error('Error calling agent step:', error);
      throw error;
    }
  }

  async progressiveStart(payload: {
    symbol: string; date: string; start_time: string;
    capital: number; model?: string; real_only?: boolean;
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/backtest/progressive/start', payload);
      return response.data;
    } catch (error) {
      console.error('Error starting progressive session:', error);
      throw error;
    }
  }

  async progressiveStep(payload: {
    symbol: string; date: string; current_time: string;
    capital: number; cash: number;
    position: 'NONE' | 'LONG'; quantity: number;
    entry_price: number; entry_time: string | null;
    trades: any[]; model?: string; real_only?: boolean;
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/backtest/progressive/step', payload);
      return response.data;
    } catch (error) {
      console.error('Error running progressive step:', error);
      throw error;
    }
  }

  async paperTradingStatus(): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get('/api/paper-trading/status');
      return response.data;
    } catch (error) {
      console.error('Error fetching paper trading status:', error);
      throw error;
    }
  }

  async paperTradingStart(payload: {
    symbol: string;
    capital: number;
    model?: string;
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/paper-trading/start', payload);
      return response.data;
    } catch (error) {
      console.error('Error starting paper trading session:', error);
      throw error;
    }
  }

  async paperTradingTick(symbol: string, params?: {
    position?: string; entry_price?: number; quantity?: number;
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get(`/api/paper-trading/tick/${symbol}`, { params });
      return response.data;
    } catch (error) {
      console.error('Error fetching live tick:', error);
      throw error;
    }
  }

  async paperTradingPlaceOrder(payload: {
    symbol: string; action: string; quantity: number;
    order_type?: string; price?: number; product?: string; exchange?: string;
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/paper-trading/place-order', payload);
      return response.data;
    } catch (error) {
      console.error('Error placing order:', error);
      throw error;
    }
  }

  async paperTradingStep(payload: {
    symbol: string;
    capital: number;
    cash: number;
    position: 'NONE' | 'LONG';
    quantity: number;
    entry_price: number;
    entry_time: string | null;
    trades: any[];
    model?: string;
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/paper-trading/step', payload);
      return response.data;
    } catch (error) {
      console.error('Error running paper trading step:', error);
      throw error;
    }
  }

  // ── AI Engine ──────────────────────────────────────────────────────────────

  async aiEngineAnalyze(payload: {
    symbol: string;
    candles: any[];
    context?: Record<string, any>;
    capital?: number;
    position?: 'NONE' | 'LONG';
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/ai-engine/analyze', payload);
      return response.data;
    } catch (error) {
      console.error('Error calling AI engine analyze:', error);
      throw error;
    }
  }

  async aiEngineOutcome(payload: {
    predictionId: string;
    symbol: string;
    entryPrice: number;
    exitPrice: number;
    pnl: number;
    pnlPct: number;
  }): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.post('/api/ai-engine/outcome', {
        prediction_id: payload.predictionId,
        symbol:        payload.symbol,
        entry_price:   payload.entryPrice,
        exit_price:    payload.exitPrice,
        pnl:           payload.pnl,
        pnl_pct:       payload.pnlPct,
      });
      return response.data;
    } catch (error) {
      console.error('Error recording AI engine outcome:', error);
      throw error;
    }
  }

  async aiEnginePerformance(): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get('/api/ai-engine/performance');
      return response.data;
    } catch (error) {
      console.error('Error fetching AI engine performance:', error);
      throw error;
    }
  }

  async aiEngineHistory(symbol?: string, limit = 20): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get('/api/ai-engine/history', {
        params: { symbol, limit },
      });
      return response.data;
    } catch (error) {
      console.error('Error fetching AI engine history:', error);
      throw error;
    }
  }

  async aiEngineWeights(): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get('/api/ai-engine/weights');
      return response.data;
    } catch (error) {
      console.error('Error fetching AI engine weights:', error);
      throw error;
    }
  }

  // ── Pattern Memory bank ─────────────────────────────────────────────────────
  async memoryStats(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/memory/stats');
    return response.data;
  }

  async learningSummary(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/learning-summary');
    return response.data;
  }

  // ── AI watchlist (self-running scanner) + autopilot + learning curve ────────
  async aiWatchlist(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/watchlist');
    return response.data;
  }
  async scanWatchlist(): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/ai-engine/watchlist/scan');
    return response.data;
  }
  async getScanStatus(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/scan-status', { timeout: 8000 });
    return response.data;
  }
  async scanEvaluation(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/scan-evaluation');
    return response.data;
  }
  async getTradeGate(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/trade-gate');
    return response.data;
  }
  async setTradeGate(mode: string): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/ai-engine/trade-gate', { mode });
    return response.data;
  }
  async getRanked(limit = 100): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/ranked', { params: { limit }, timeout: 20000 });
    return response.data;
  }
  async getWatchlistConfig(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/watchlist-config');
    return response.data;
  }
  async setWatchlistConfig(max: number): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/ai-engine/watchlist-config', { max });
    return response.data;
  }
  async scanDiff(limit = 60): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/scan-diff', { params: { limit }, timeout: 20000 });
    return response.data;
  }
  async patternModelStatus(): Promise<any> {
    const response = await this.api.get('/api/ai-engine/pattern-model/status');
    return response.data;
  }
  async patternModelCurve(limit = 200): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/pattern-model/curve', { params: { limit } });
    return response.data;
  }
  async trainPatternModel(body: { lookbackDays?: number; horizon?: number; stride?: number } = {}): Promise<any> {
    const response = await this.api.post('/api/ai-engine/pattern-model/train', {
      lookback_days: body.lookbackDays ?? 365, horizon: body.horizon ?? 3, stride: body.stride ?? 1,
    });
    return response.data;
  }
  async getAutopilot(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/autopilot');
    return response.data;
  }
  async setAutopilot(enabled: boolean, mode: 'paper' | 'backtest' = 'paper'): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/ai-engine/autopilot', { enabled, mode });
    return response.data;
  }
  async resetBacktestCursor(): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/ai-engine/autopilot/reset-cursor');
    return response.data;
  }
  async setAutopilotPaperTiming(mode: 'normal' | 'aggressive'): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/ai-engine/autopilot/paper-timing', { mode });
    return response.data;
  }
  async learningCurve(source = 'PAPER,LIVE,REPLAY', window = 50): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/learning-curve', { params: { source, window } });
    return response.data;
  }
  async learningEvents(): Promise<any> {
    const response = await this.api.get('/api/ai-engine/learning-events');
    return response.data;
  }
  async addLearningEvent(ev: { title: string; detail?: string; category?: string; occurredAt?: string }): Promise<any> {
    const response = await this.api.post('/api/ai-engine/learning-events', {
      title: ev.title, detail: ev.detail ?? '', category: ev.category ?? 'update',
      occurred_at: ev.occurredAt,
    });
    return response.data;
  }

  async memoryQuery(payload: { symbol: string; candles: any[] }): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/ai-engine/memory/query', payload);
    return response.data;
  }

  async memorySeed(payload: {
    symbols?: string[];
    lookback_days?: number;
    horizon?: number;
    stride?: number;
    max_per_symbol?: number;
  } = {}): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/ai-engine/memory/seed', payload);
    return response.data;
  }

  async memorySweep(): Promise<any> {
    const response = await this.api.post('/api/ai-engine/memory/sweep?background=true');
    return response.data;
  }

  async memorySweepStatus(): Promise<any> {
    const response = await this.api.get('/api/ai-engine/memory/sweep/status');
    return response.data;
  }

  // Health check
  async healthCheck(): Promise<ApiResponse<any>> {
    try {
      const response = await this.api.get('/health');
      return response.data;
    } catch (error) {
      console.error('Health check failed:', error);
      throw error;
    }
  }

  // ── Live trading sessions (server-side, background-persistent) ──────────────
  async sessionStart(payload: {
    mode: 'replay' | 'paper'; symbol: string; date?: string;
    start_time?: string; capital?: number; speed?: number; model?: string;
    max_hold_minutes?: number; timing_mode?: 'normal' | 'aggressive';
  }): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/sessions/start', payload);
    return response.data;
  }

  async sessionList(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/sessions');
    return response.data;
  }

  async sessionGet(id: string): Promise<ApiResponse<any>> {
    const response = await this.api.get(`/api/sessions/${id}`);
    return response.data;
  }

  async sessionStop(id: string): Promise<ApiResponse<any>> {
    const response = await this.api.post(`/api/sessions/${id}/stop`);
    return response.data;
  }

  async sessionSpeed(id: string, speed: number): Promise<ApiResponse<any>> {
    const response = await this.api.post(`/api/sessions/${id}/speed`, { speed });
    return response.data;
  }

  async sessionDelete(id: string): Promise<ApiResponse<any>> {
    const response = await this.api.delete(`/api/sessions/${id}`);
    return response.data;
  }

  async getPaperConfig(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/sessions/paper-config');
    return response.data;
  }

  async setPaperConfig(noEntryAfter: string, squareoffAfter: string): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/sessions/paper-config', {
      no_entry_after: noEntryAfter,
      squareoff_after: squareoffAfter,
    });
    return response.data;
  }

  // Market-data providers (Groww, Yahoo, Alpha Vantage, …) and their availability
  async getDataProviders(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/backtest/providers');
    return response.data;
  }

  // Settings — provider configuration (auth required)
  async getProviderSettings(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/settings/providers');
    return response.data;
  }

  async updateProviderSettings(payload: {
    order?: string[]; disabled?: string[]; keys?: Record<string, string>;
  }): Promise<ApiResponse<any>> {
    const response = await this.api.put('/api/settings/providers', payload);
    return response.data;
  }

  // Microservice health — backend proxies checks on the Docker network
  async getServicesHealth(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/agent/services/health');
    return response.data;
  }

  async getLlmStatus(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/llm-status');
    return response.data;
  }

  // AI loss-learning: post-mortems on losing trades + aggregated lessons
  async runLossLearning(): Promise<ApiResponse<any>> {
    const response = await this.api.post('/api/ai-engine/loss-learning/run', null, { timeout: 90000 });
    return response.data;
  }
  async getLossPostmortems(limit = 50): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/loss-learning/postmortems', { params: { limit } });
    return response.data;
  }
  async getLossLessons(): Promise<ApiResponse<any>> {
    const response = await this.api.get('/api/ai-engine/loss-learning/lessons');
    return response.data;
  }

  // Feedback-service proxy (browser cannot reach Docker-internal port 8012)
  async getFeedbackStats(): Promise<any> {
    const response = await this.api.get('/api/orders/feedback/stats');
    return response.data;
  }

  async getFeedbackTrades(): Promise<any> {
    const response = await this.api.get('/api/orders/feedback/trades');
    return response.data;
  }

  async getPortfolioMetrics(): Promise<any> {
    const response = await this.api.get('/api/orders/feedback/portfolio-metrics');
    return response.data;
  }

  async getAgentAccuracy(minTrades = 20): Promise<any> {
    const response = await this.api.get(`/api/orders/feedback/agent-accuracy?min_trades=${minTrades}`);
    return response.data;
  }
}

export default new ApiService();
