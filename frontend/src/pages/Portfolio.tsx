import React, { useEffect, useState, useMemo } from 'react';
import apiService from '../services/api';
import ScanControl from '../components/ScanControl';
import { Portfolio, Performance, PortfolioStock } from '../types';

type SortKey = keyof Pick<PortfolioStock, 'symbol' | 'quantity' | 'purchasePrice' | 'currentPrice' | 'value' | 'gain' | 'gainPercent'>;
type SortDir = 'asc' | 'desc';
type Tab = 'holdings' | 'performance' | 'risk' | 'optimize' | 'invest' | 'sectors' | 'funds' | 'health' | 'planner' | 'tax' | 'advisor';

const inr = (v: number) =>
  v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const pct = (v: any) => (v === null || v === undefined) ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
const pctColor = (v: any) => (v === null || v === undefined) ? 'var(--nd-text-3)' : v >= 0 ? 'var(--nd-green)' : 'var(--nd-red)';

const COLUMNS: { key: SortKey; label: string; align: 'left' | 'right' }[] = [
  { key: 'symbol',        label: 'Company',       align: 'left'  },
  { key: 'quantity',      label: 'Qty',           align: 'right' },
  { key: 'purchasePrice', label: 'Avg Price',     align: 'right' },
  { key: 'currentPrice',  label: 'Market Price',  align: 'right' },
  { key: 'value',         label: 'Current Value', align: 'right' },
  { key: 'gain',          label: 'P&L',           align: 'right' },
  { key: 'gainPercent',   label: 'Returns (%)',   align: 'right' },
];

// Herfindahl-Hirschman Index — 0 (perfectly diversified) to 1 (all in one stock)
function calcHHI(stocks: PortfolioStock[], totalValue: number): number {
  return stocks.reduce((sum, s) => {
    const w = s.value / totalValue;
    return sum + w * w;
  }, 0);
}

// Approximate 1-day 95% VaR using normal distribution assumption (1.645σ)
// σ derived from gainPercent as a rough daily volatility proxy
function calcVaR(stocks: PortfolioStock[], totalValue: number): number {
  const dailyVol = stocks.reduce((sum, s) => {
    const weight = s.value / totalValue;
    const vol = Math.abs(s.gainPercent / 100) * 0.1; // rough proxy
    return sum + weight * vol;
  }, 0);
  return totalValue * dailyVol * 1.645;
}

function RiskMeter({ score, label }: { score: 'LOW' | 'MEDIUM' | 'HIGH'; label: string }) {
  const color = score === 'LOW' ? 'var(--nd-green)' : score === 'MEDIUM' ? '#f59e0b' : 'var(--nd-red)';
  const pct   = score === 'LOW' ? 25 : score === 'MEDIUM' ? 60 : 90;
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{label}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color }}>{score}</span>
      </div>
      <div style={{ height: 6, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.5s' }} />
      </div>
    </div>
  );
}

// Risk-profiling questionnaire — points → conservative/moderate/aggressive.
const RISK_QUIZ: { q: string; opts: [string, number][] }[] = [
  { q: 'Your age band', opts: [['Under 35', 3], ['35–50', 2], ['Over 50', 1]] },
  { q: 'When do you need this money?', opts: [['10+ years', 3], ['3–10 years', 2], ['Under 3 years', 1]] },
  { q: 'If your portfolio dropped 20% in a month, you would', opts: [['Buy more', 3], ['Hold', 2], ['Sell some', 1]] },
  { q: 'Investing experience', opts: [['Experienced', 3], ['Some', 2], ['New', 1]] },
  { q: 'Primary goal', opts: [['Grow wealth aggressively', 3], ['Steady growth', 2], ['Protect capital', 1]] },
];
const riskFromScore = (pts: number) => pts >= 13 ? 'aggressive' : pts >= 9 ? 'moderate' : 'conservative';

const PortfolioPage: React.FC = () => {
  const [portfolio,   setPortfolio]   = useState<Portfolio | null>(null);
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [loading,     setLoading]     = useState(true);
  const [alerts,      setAlerts]      = useState<any[]>([]);
  const [sortKey,     setSortKey]     = useState<SortKey>('value');
  const [sortDir,     setSortDir]     = useState<SortDir>('desc');
  const [activeTab,   setActiveTab]   = useState<Tab>('holdings');
  const [optimization, setOptimization] = useState<any>(null);
  const [optimizing,   setOptimizing]   = useState(false);

  useEffect(() => { fetchPortfolioData(); }, []);

  const [orders, setOrders] = useState<any[]>([]);
  const [cancellingId, setCancellingId] = useState<string | null>(null);

  // Load the persisted plan when the tab opens, then silently refresh while it's
  // open so a newer AI scan auto-updates the recommendation (cheap when cached).
  useEffect(() => {
    if (activeTab !== 'optimize') return;
    loadOptimization(!!optimization);                 // silent if we already have one
    loadOrders();
    const t = setInterval(() => loadOptimization(true), 120000);
    const o = setInterval(loadOrders, 20000);
    // Re-sync the order book the instant the user returns to this tab — e.g.
    // right after cancelling an order directly in the Groww app/tab.
    const onVisible = () => { if (document.visibilityState === 'visible') loadOrders(); };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      clearInterval(t); clearInterval(o);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const loadOrders = async () => {
    try { const res = await apiService.listOrders(); setOrders(res.data || []); } catch { /* ignore */ }
  };

  const CANCELLABLE = ['NEW', 'OPEN', 'PENDING', 'ACKED', 'ACKNOWLEDGED', 'APPROVED', 'TRIGGER_PENDING', 'MODIFIED'];
  const cancelPendingOrder = async (orderId: string, segment: string) => {
    setCancellingId(orderId);
    try {
      const res = await apiService.cancelOrder(orderId, segment || 'CASH');
      setOrderMsg({ ok: res.status === 'success', text: res.status === 'success' ? `Order ${orderId} cancelled.` : (res.message || 'Cancel failed') });
    } catch (e: any) {
      setOrderMsg({ ok: false, text: e?.response?.data?.detail || e?.message || 'Cancel failed' });
    } finally {
      setCancellingId(null);
      loadOrders();
    }
  };

  const loadOptimization = async (silent = false) => {
    if (!silent) setOptimizing(true);
    try {
      const res = await apiService.optimizePortfolio(false);
      if (res.data?.plan?.actions) setOptimization(res.data);
    } catch (err) {
      console.error('Optimization load failed:', err);
    } finally {
      if (!silent) setOptimizing(false);
    }
  };

  const runOptimization = async () => {
    setOptimizing(true);
    try {
      const res = await apiService.optimizePortfolio(true);   // force a fresh recompute
      if (res.data) setOptimization(res.data);
    } catch (err) {
      console.error('Optimization failed:', err);
    } finally {
      setOptimizing(false);
    }
  };

  // ── Direct Groww order placement (with confirmation) ──────────────────────
  const [pendingOrder, setPendingOrder] = useState<any>(null);
  const [orderMsg, setOrderMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [placing, setPlacing] = useState(false);

  // Place one leg; never throws — returns {ok, text}.
  const placeOne = async (spec: any): Promise<{ ok: boolean; text: string }> => {
    try {
      const res = await apiService.placeOrder({
        symbol: spec.symbol,
        quantity: spec.quantity,
        transactionType: spec.transactionType,
        orderType: spec.orderType || 'MARKET',
        price: spec.price,
        product: spec.product || 'CNC',
        exchange: spec.exchange || 'NSE',
      } as any);
      const ok = res.status === 'success';
      return { ok, text: ok
        ? `${spec.transactionType} ${spec.quantity} ${spec.symbol} placed`
        : `${spec.symbol}: ${res.message || 'failed'}` };
    } catch (e: any) {
      return { ok: false, text: `${spec.transactionType} ${spec.symbol} failed: ${e?.response?.data?.detail || e?.message || 'error'}` };
    }
  };

  const confirmOrder = async () => {
    if (!pendingOrder) return;
    setPlacing(true);
    try {
      if (pendingOrder.kind === 'basket') {
        // Place each buy sequentially; report how many landed.
        let ok = 0; const fails: string[] = [];
        for (const leg of pendingOrder.legs) {
          const r = await placeOne(leg);
          if (r.ok) ok += 1; else fails.push(r.text);
        }
        setOrderMsg({ ok: fails.length === 0,
          text: `Placed ${ok}/${pendingOrder.legs.length} buy orders${fails.length ? ` · failed: ${fails.join('; ')}` : ''}.` });
      } else if (pendingOrder.kind === 'swap') {
        // Sell first, then buy the alternative — only buy if the sell was accepted.
        const s = await placeOne(pendingOrder.sell);
        if (!s.ok) {
          setOrderMsg({ ok: false, text: `Swap stopped — SELL failed (${s.text}). BUY not placed.` });
        } else {
          const b = await placeOne(pendingOrder.buy);
          setOrderMsg({ ok: b.ok, text: b.ok
            ? `Swap placed: ${s.text}, then ${b.text}.`
            : `Sell placed (${s.text}), but BUY failed: ${b.text}` });
        }
      } else {
        setOrderMsg(await placeOne(pendingOrder));
      }
    } finally {
      setPlacing(false);
      setPendingOrder(null);
      loadOrders();
    }
  };

  const askOrder = (spec: any) => {
    if (!spec?.quantity || spec.quantity <= 0) return;
    setOrderMsg(null);
    setPendingOrder(spec);
  };

  // ── AI Invest: allocate an amount across the best AI picks ────────────────
  const [investAmount, setInvestAmount] = useState('10000');
  const [investData, setInvestData] = useState<any>(null);
  const [investLoading, setInvestLoading] = useState(false);

  // Sector exposure + AI fund baskets
  const [sectorData, setSectorData] = useState<any>(null);
  const [baskets, setBaskets] = useState<any[]>([]);
  const [basketAmt, setBasketAmt] = useState('25000');
  const [openBasket, setOpenBasket] = useState<string | null>(null);
  // Health / Planner / Tax
  const [health, setHealth] = useState<any>(null);
  const [tax, setTax] = useState<any>(null);
  const [plan, setPlan] = useState<any>(null);
  const [planForm, setPlanForm] = useState({ goalAmount: '5000000', years: '15', risk: 'moderate', currentCorpus: '0', monthly: '' });
  const [planning, setPlanning] = useState(false);
  const [advisor, setAdvisor] = useState<any>(null);
  const [bench, setBench] = useState<any>(null);
  const [quizOpen, setQuizOpen] = useState(false);
  const [quizAns, setQuizAns] = useState<number[]>(Array(RISK_QUIZ.length).fill(2));

  useEffect(() => {
    if (activeTab === 'sectors') apiService.sectorExposure().then(r => setSectorData((r as any).data)).catch(() => {});
    if (activeTab === 'funds') apiService.fundBaskets().then(r => setBaskets((r as any).data?.baskets ?? [])).catch(() => {});
    if (activeTab === 'health') apiService.portfolioHealth().then(r => setHealth((r as any).data)).catch(() => {});
    if (activeTab === 'tax') apiService.taxHarvest().then(r => setTax((r as any).data)).catch(() => {});
    if (activeTab === 'advisor') {
      apiService.portfolioBenchmark().then(r => setBench((r as any).data)).catch(() => {});
      apiService.portfolioAdvisor().then(r => setAdvisor((r as any).data)).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const runPlan = async () => {
    setPlanning(true);
    try {
      const r = await apiService.sipPlanner({
        goalAmount: +planForm.goalAmount || 0, years: +planForm.years || 10, risk: planForm.risk,
        currentCorpus: +planForm.currentCorpus || 0, monthly: +planForm.monthly || 0,
      });
      setPlan((r as any).data);
    } catch {} finally { setPlanning(false); }
  };

  const askInvestBasket = async (b: any) => {
    const amt = parseFloat(basketAmt);
    if (!amt || amt <= 0) { setOrderMsg({ ok: false, text: 'Enter an amount to invest.' }); return; }
    try {
      const res = await apiService.investBasket(b.id, amt);
      const picks = (res as any).data?.picks ?? [];
      const legs = picks.filter((p: any) => p.trade && p.trade.quantity > 0).map((p: any) => ({
        symbol: p.symbol, transactionType: 'BUY', quantity: p.trade.quantity,
        orderType: p.trade.orderType, price: p.trade.limitPrice, exchange: p.trade.exchange,
        product: p.trade.product, estValue: p.trade.estValue,
      }));
      if (!legs.length) { setOrderMsg({ ok: false, text: 'Amount too small to buy any whole shares in this basket.' }); return; }
      setOrderMsg(null);
      setPendingOrder({ kind: 'basket', legs, label: `Invest ₹${amt.toLocaleString('en-IN')} in ${b.name}` });
    } catch (e) {
      setOrderMsg({ ok: false, text: 'Could not build the basket order.' });
    }
  };

  const generateInvestPlan = async () => {
    const amt = parseFloat(investAmount);
    if (!amt || amt <= 0) return;
    setInvestLoading(true);
    try {
      const res = await apiService.investPlan(amt, 6);
      if (res.data) setInvestData(res.data);
    } catch (e) {
      console.error('Invest plan failed:', e);
    } finally {
      setInvestLoading(false);
    }
  };

  const askInvestAll = (picks: any[]) => {
    const legs = picks.filter(p => p.order && p.order.quantity > 0).map(p => ({
      symbol: p.symbol, transactionType: 'BUY', quantity: p.order.quantity,
      orderType: p.order.orderType, price: p.order.limitPrice, exchange: p.order.exchange,
      product: p.order.product, estValue: p.order.estValue,
    }));
    if (!legs.length) return;
    setOrderMsg(null);
    setPendingOrder({ kind: 'basket', legs, label: `Invest across ${legs.length} stocks` });
  };

  const askSwap = (a: any, sig: any) => {
    const o = a.alternative?.order;
    if (!a.trade || !o) return;
    const alt = a.alternative;
    setOrderMsg(null);
    setPendingOrder({
      kind: 'swap',
      sell: { symbol: a.symbol, transactionType: 'SELL', quantity: a.trade.quantity,
              orderType: a.trade.orderType, price: a.trade.limitPrice, exchange: a.trade.exchange,
              product: a.trade.product, estValue: a.trade.estValue },
      buy:  { symbol: alt.symbol, transactionType: 'BUY', quantity: o.quantity,
              orderType: o.orderType, price: o.limitPrice, exchange: o.exchange,
              product: o.product, estValue: o.estValue },
      // Why the AI recommends this swap — shown in the confirm dialog.
      basis: {
        sell: {
          action: a.action, reason: a.reason,
          signal: sig?.signal, health: sig?.health, rsi: sig?.rsi,
          momentum: sig?.momentumPct, trend: sig?.smaTrend, atr: sig?.atrPct,
          weightNow: a.currentWeightPct, weightTarget: a.targetWeightPct, pnl: a.pnlPct,
        },
        buy: {
          grade: alt.grade, winProb: alt.winProbability, sector: alt.sector,
          sameSector: alt.sameSector, reasoning: alt.scannerReasoning, price: alt.price,
        },
      },
    });
  };

  const fetchPortfolioData = async () => {
    try {
      setLoading(true);
      const [portRes, perfRes, alertRes] = await Promise.all([
        apiService.getPortfolio(),
        apiService.getPerformance(),
        apiService.getAlerts(),
      ]);
      if (portRes.data)  setPortfolio(portRes.data);
      if (perfRes.data)  setPerformance(perfRes.data);
      if (alertRes.data) setAlerts(alertRes.data as any[]);
    } catch (err) {
      console.error('Error fetching portfolio:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir(key === 'symbol' ? 'asc' : 'desc'); }
  };

  const sortedStocks = useMemo(() => {
    if (!portfolio) return [];
    return [...portfolio.stocks].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      const cmp = typeof av === 'string' ? av.localeCompare(bv as string) : (av as number) - (bv as number);
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [portfolio, sortKey, sortDir]);

  // Risk metrics computed from portfolio
  const riskMetrics = useMemo(() => {
    if (!portfolio || !portfolio.stocks.length) return null;
    const tv = portfolio.totalValue;
    const hhi = calcHHI(portfolio.stocks, tv);
    const varAmount = calcVaR(portfolio.stocks, tv);
    const topHolding = [...portfolio.stocks].sort((a, b) => b.value - a.value)[0];
    const topWeight  = topHolding ? topHolding.value / tv : 0;
    const negCount   = portfolio.stocks.filter(s => s.gainPercent < 0).length;
    const worstStock = [...portfolio.stocks].sort((a, b) => a.gainPercent - b.gainPercent)[0];

    const concentrationRisk: 'LOW' | 'MEDIUM' | 'HIGH' =
      hhi > 0.25 ? 'HIGH' : hhi > 0.12 ? 'MEDIUM' : 'LOW';
    const diversificationRisk: 'LOW' | 'MEDIUM' | 'HIGH' =
      portfolio.stocks.length < 5 ? 'HIGH' : portfolio.stocks.length < 10 ? 'MEDIUM' : 'LOW';
    const drawdownRisk: 'LOW' | 'MEDIUM' | 'HIGH' =
      portfolio.gainPercent < -15 ? 'HIGH' : portfolio.gainPercent < -5 ? 'MEDIUM' : 'LOW';

    return {
      hhi, varAmount, topHolding, topWeight, negCount, worstStock,
      concentrationRisk, diversificationRisk, drawdownRisk,
      stockWeights: portfolio.stocks.map(s => ({
        symbol: s.symbol,
        pct: (s.value / tv) * 100,
        value: s.value,
        gain: s.gainPercent,
        isConcentrated: s.value / tv > 0.10,
      })).sort((a, b) => b.pct - a.pct),
    };
  }, [portfolio]);

  if (loading) {
    return (
      <div className="nd-loading">
        <span className="material-icons nd-spin">autorenew</span>
        <span>Loading portfolio...</span>
      </div>
    );
  }

  const tabs: { id: Tab; label: string; icon: string }[] = [
    { id: 'holdings',    label: 'Holdings',    icon: 'account_balance_wallet' },
    { id: 'performance', label: 'Performance', icon: 'trending_up' },
    { id: 'risk',        label: 'Risk',        icon: 'security' },
    { id: 'optimize',    label: 'AI Optimize', icon: 'auto_awesome' },
    { id: 'invest',      label: 'AI Invest',   icon: 'savings' },
    { id: 'advisor',     label: 'AI Advisor',  icon: 'support_agent' },
    { id: 'health',      label: 'Health Score', icon: 'health_and_safety' },
    { id: 'sectors',     label: 'Sector Exposure', icon: 'donut_large' },
    { id: 'funds',       label: 'AI Funds',    icon: 'inventory_2' },
    { id: 'planner',     label: 'Goal Planner', icon: 'savings' },
    { id: 'tax',         label: 'Tax Harvest', icon: 'receipt_long' },
  ];

  const ACTION_STYLE: Record<string, { bg: string; color: string }> = {
    EXIT: { bg: '#ef444420', color: '#ef4444' },
    TRIM: { bg: '#f59e0b20', color: '#f59e0b' },
    HOLD: { bg: 'var(--nd-surface-2)', color: 'var(--nd-text-2)' },
    ADD:  { bg: '#22c55e20', color: '#22c55e' },
  };

  return (
    <div>
      {/* Page header */}
      <div style={{ marginBottom: 20, display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <h1 className="nd-page-title">Portfolio</h1>
          <p className="nd-page-sub" style={{ marginBottom: 0 }}>Holdings, performance & risk from your Groww account</p>
        </div>
        <ScanControl align="right" />
      </div>

      {portfolio && (
        <>
          {/* Summary strip */}
          <div className="nd-card" style={{ padding: '20px 16px', marginBottom: 16 }}>
            <div className="nd-grid-4" style={{ gap: 0 }}>
              {[
                { label: 'Current Value',   value: `₹${inr(portfolio.totalValue)}`,    color: 'var(--nd-text-1)', icon: 'account_balance_wallet' },
                { label: 'Invested Value',  value: `₹${inr(portfolio.totalInvested)}`, color: 'var(--nd-text-2)', icon: 'savings' },
                { label: '1D Returns',
                  value: `${(portfolio.dayChange ?? 0) >= 0 ? '+' : '-'}₹${inr(Math.abs(portfolio.dayChange ?? 0))}${portfolio.dayChangePercent != null ? ` (${portfolio.dayChangePercent >= 0 ? '+' : ''}${portfolio.dayChangePercent.toFixed(2)}%)` : ''}`,
                  color: (portfolio.dayChange ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', icon: 'show_chart' },
                { label: 'Total Returns',
                  value: `${portfolio.totalGain >= 0 ? '+' : '-'}₹${inr(Math.abs(portfolio.totalGain))} (${portfolio.gainPercent >= 0 ? '+' : ''}${portfolio.gainPercent.toFixed(2)}%)`,
                  color: portfolio.gainPercent >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', icon: 'trending_up' },
              ].map((c) => (
                <div key={c.label} className="nd-portfolio-stat">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-text-3)' }}>{c.icon}</span>
                    <p className="nd-label" style={{ margin: 0 }}>{c.label}</p>
                  </div>
                  <p className="nd-stat-value" style={{ fontSize: 22, fontWeight: 700, color: c.color, margin: 0 }}>{c.value}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Tab strip */}
          <div className="nd-pill-tabs">
            {tabs.map(t => (
              <button key={t.id} onClick={() => setActiveTab(t.id)} className="nd-pill-tab"
                style={{
                  background: activeTab === t.id ? 'var(--nd-green)' : 'transparent',
                  color: activeTab === t.id ? '#fff' : 'var(--nd-text-2)',
                }}>
                <span className="material-icons" style={{ fontSize: 15 }}>{t.icon}</span>
                {t.label}
              </button>
            ))}
          </div>

          {/* ── Holdings Tab ─────────────────────────────────────────────────────── */}
          {activeTab === 'holdings' && (
            <>
              <div className="nd-card" style={{ padding: 0, marginBottom: 16 }}>
                <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h2 className="nd-section-title" style={{ margin: 0 }}>
                    Holdings
                    <span style={{ fontWeight: 400, color: 'var(--nd-text-2)', marginLeft: 6 }}>({portfolio.stocks.length})</span>
                  </h2>
                  <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>Click column to sort</span>
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table className="nd-table">
                    <thead>
                      <tr>
                        {COLUMNS.map(col => (
                          <th key={col.key} onClick={() => handleSort(col.key)}
                            className={sortKey === col.key ? 'active-sort' : ''}
                            style={{ textAlign: col.align, cursor: 'pointer' }}>
                            {col.label}
                            {sortKey === col.key && (
                              <span className="material-icons" style={{ fontSize: 12, verticalAlign: 'middle', marginLeft: 3 }}>
                                {sortDir === 'asc' ? 'arrow_upward' : 'arrow_downward'}
                              </span>
                            )}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedStocks.map(stock => (
                        <tr key={stock.symbol}>
                          <td><p style={{ fontWeight: 600, color: 'var(--nd-green)' }}>{stock.symbol}</p></td>
                          <td className="text-right">{stock.quantity}</td>
                          <td className="text-right">₹{inr(stock.purchasePrice)}</td>
                          <td className="text-right" style={{ fontWeight: 600 }}>₹{inr(stock.currentPrice)}</td>
                          <td className="text-right" style={{ fontWeight: 600 }}>₹{inr(stock.value)}</td>
                          <td className="text-right" style={{ fontWeight: 600, color: stock.gain >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                            {stock.gain >= 0 ? '+' : '-'}₹{inr(Math.abs(stock.gain))}
                          </td>
                          <td className="text-right">
                            <span className={`nd-badge ${stock.gainPercent >= 0 ? 'nd-badge-green' : 'nd-badge-red'}`}>
                              {stock.gainPercent >= 0 ? '+' : ''}{stock.gainPercent.toFixed(2)}%
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {alerts.length > 0 && (
                <div className="nd-card">
                  <h2 className="nd-section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-orange)' }}>notifications_active</span>
                    Active Alerts
                  </h2>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {alerts.map(alert => (
                      <div key={alert.id} style={{ padding: '12px 16px', borderRadius: 8, border: `1px solid ${alert.enabled ? 'var(--nd-green)' : 'var(--nd-border)'}`, background: alert.enabled ? 'var(--nd-green-50)' : 'var(--nd-surface)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', opacity: alert.enabled ? 1 : 0.6 }}>
                        <div>
                          <p style={{ fontWeight: 600, fontSize: 13.5 }}>{alert.symbol} — {alert.alertType}</p>
                          <p style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 2 }}>{alert.condition}</p>
                        </div>
                        <span className={`nd-badge ${alert.enabled ? 'nd-badge-green' : 'nd-badge-gray'}`}>
                          {alert.enabled ? 'Active' : 'Inactive'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {/* ── Performance Tab ──────────────────────────────────────────────────── */}
          {activeTab === 'performance' && performance && (
            <div className="nd-card">
              <h2 className="nd-section-title">Performance Metrics</h2>
              <div className="nd-grid-4" style={{ gap: 12 }}>
                {[
                  { label: 'Daily Return',   value: `${performance.dailyReturn > 0 ? '+' : ''}${performance.dailyReturn.toFixed(2)}%`,   color: performance.dailyReturn >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' },
                  { label: 'Monthly Return', value: `${performance.monthlyReturn > 0 ? '+' : ''}${performance.monthlyReturn.toFixed(2)}%`, color: performance.monthlyReturn >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' },
                  { label: 'Sharpe Ratio',   value: performance.sharpeRatio.toFixed(2),                                                   color: 'var(--nd-text-1)' },
                  { label: 'Win Rate',       value: `${(performance.winRate * 100).toFixed(1)}%`,                                         color: 'var(--nd-text-1)' },
                ].map(m => (
                  <div key={m.label} className="nd-metric">
                    <p className="nd-metric-label">{m.label}</p>
                    <p className="nd-metric-value" style={{ color: m.color }}>{m.value}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Risk Tab ─────────────────────────────────────────────────────────── */}
          {activeTab === 'risk' && (
            riskMetrics ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

                {/* Risk summary cards */}
                <div className="nd-grid-3" style={{ gap: 12 }}>
                  {[
                    {
                      label: '95% 1-Day VaR',
                      value: `₹${inr(riskMetrics.varAmount)}`,
                      sub: 'Maximum expected daily loss',
                      color: riskMetrics.varAmount > portfolio.totalValue * 0.03 ? 'var(--nd-red)' : 'var(--nd-text-1)',
                      icon: 'trending_down',
                    },
                    {
                      label: 'Diversification (HHI)',
                      value: riskMetrics.hhi.toFixed(3),
                      sub: riskMetrics.hhi > 0.25 ? 'High concentration' : riskMetrics.hhi > 0.12 ? 'Moderate' : 'Well diversified',
                      color: riskMetrics.hhi > 0.25 ? 'var(--nd-red)' : riskMetrics.hhi > 0.12 ? '#f59e0b' : 'var(--nd-green)',
                      icon: 'donut_large',
                    },
                    {
                      label: 'Stocks in Loss',
                      value: `${riskMetrics.negCount} / ${portfolio.stocks.length}`,
                      sub: riskMetrics.worstStock ? `Worst: ${riskMetrics.worstStock.symbol} (${riskMetrics.worstStock.gainPercent.toFixed(1)}%)` : '',
                      color: riskMetrics.negCount > portfolio.stocks.length / 2 ? 'var(--nd-red)' : 'var(--nd-text-1)',
                      icon: 'warning_amber',
                    },
                  ].map(card => (
                    <div key={card.label} className="nd-card" style={{ padding: '16px 20px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                        <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-3)' }}>{card.icon}</span>
                        <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{card.label}</span>
                      </div>
                      <div style={{ fontSize: 24, fontWeight: 700, color: card.color, marginBottom: 4 }}>{card.value}</div>
                      <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{card.sub}</div>
                    </div>
                  ))}
                </div>

                {/* Risk meters */}
                <div className="nd-card">
                  <h3 style={{ margin: '0 0 16px', fontSize: 14, fontWeight: 600, color: 'var(--nd-text-2)' }}>Risk Indicators</h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                    <RiskMeter score={riskMetrics.concentrationRisk}  label="Concentration Risk (HHI-based)" />
                    <RiskMeter score={riskMetrics.diversificationRisk} label="Diversification Risk (stock count)" />
                    <RiskMeter score={riskMetrics.drawdownRisk}        label="Drawdown Risk (portfolio returns)" />
                  </div>
                </div>

                {/* Position weight breakdown */}
                <div className="nd-card" style={{ padding: 0 }}>
                  <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-2)' }}>Position Weights</h3>
                    <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Positions &gt;10% flagged as HIGH concentration</span>
                  </div>
                  <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {riskMetrics.stockWeights.map(sw => (
                      <div key={sw.symbol}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, alignItems: 'center' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)' }}>{sw.symbol}</span>
                            {sw.isConcentrated && (
                              <span style={{ fontSize: 10, background: '#ef444420', color: '#ef4444', padding: '1px 6px', borderRadius: 4, fontWeight: 600 }}>HIGH</span>
                            )}
                          </div>
                          <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                            <span style={{ fontSize: 12, color: sw.gain >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 500 }}>
                              {sw.gain >= 0 ? '+' : ''}{sw.gain.toFixed(1)}%
                            </span>
                            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)', minWidth: 40, textAlign: 'right' }}>
                              {sw.pct.toFixed(1)}%
                            </span>
                          </div>
                        </div>
                        <div style={{ height: 5, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden' }}>
                          <div style={{
                            height: '100%',
                            width: `${Math.min(sw.pct, 100)}%`,
                            background: sw.isConcentrated ? 'var(--nd-red)' : sw.gain >= 0 ? 'var(--nd-green)' : '#f59e0b',
                            borderRadius: 3,
                            transition: 'width 0.4s',
                          }} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Largest position warning */}
                {riskMetrics.topWeight > 0.10 && (
                  <div style={{ background: '#ef444410', border: '1px solid #ef444430', borderRadius: 12, padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span className="material-icons" style={{ color: 'var(--nd-red)', fontSize: 20 }}>warning</span>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-red)' }}>High Concentration Warning</div>
                      <div style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 2 }}>
                        {riskMetrics.topHolding?.symbol} represents {(riskMetrics.topWeight * 100).toFixed(1)}% of your portfolio.
                        Consider rebalancing — positions above 10% increase single-stock risk significantly.
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="nd-card" style={{ textAlign: 'center', padding: 48 }}>
                <span className="material-icons" style={{ fontSize: 40, color: 'var(--nd-text-3)', display: 'block' }}>security</span>
                <div style={{ color: 'var(--nd-text-3)', marginTop: 8, fontSize: 13 }}>No portfolio data available for risk analysis</div>
              </div>
            )
          )}

          {/* ── AI Optimize Tab ──────────────────────────────────────────────────── */}
          {activeTab === 'optimize' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* Intro / run */}
              <div className="nd-card" style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                <span className="material-icons" style={{ fontSize: 26, color: 'var(--nd-green)' }}>auto_awesome</span>
                <div style={{ flex: 1, minWidth: 220 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI Portfolio Optimizer</div>
                  <div style={{ fontSize: 12.5, color: 'var(--nd-text-2)', marginTop: 2 }}>
                    Scores every holding with live AI signals, measures concentration &amp; sector risk, pulls higher-conviction picks from the AI scanner, and proposes a rebalancing plan.
                  </div>
                </div>
                <button onClick={runOptimization} disabled={optimizing}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 18px', borderRadius: 10, border: 'none',
                    background: 'var(--nd-green)', color: '#fff', fontWeight: 600, fontSize: 14, cursor: optimizing ? 'wait' : 'pointer' }}>
                  <span className={`material-icons${optimizing ? ' nd-spin' : ''}`} style={{ fontSize: 18 }}>{optimizing ? 'autorenew' : 'insights'}</span>
                  {optimizing ? 'Optimizing…' : optimization ? 'Re-run' : 'Run optimization'}
                </button>
              </div>

              {/* Live Groww orders (today) with cancel */}
              {orders.length > 0 && (
                <div className="nd-card" style={{ padding: 0 }}>
                  <div style={{ padding: '12px 18px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-2)' }}>receipt_long</span>
                    <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>Today's Orders (Groww)</span>
                    <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{orders.length}</span>
                    <button onClick={loadOrders} title="Refresh" style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-3)', display: 'flex' }}>
                      <span className="material-icons" style={{ fontSize: 16 }}>refresh</span>
                    </button>
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table className="nd-table">
                      <thead><tr>
                        <th>Symbol</th><th style={{ textAlign: 'center' }}>Side</th><th style={{ textAlign: 'right' }}>Qty</th>
                        <th style={{ textAlign: 'center' }}>Type</th><th style={{ textAlign: 'center' }}>Status</th><th style={{ textAlign: 'center' }}>Action</th>
                      </tr></thead>
                      <tbody>
                        {orders.map((o: any) => {
                          const status = String(o.status || '').toUpperCase();
                          const canCancel = CANCELLABLE.includes(status);
                          const stColor = ['EXECUTED', 'COMPLETE', 'FILLED'].includes(status) ? 'var(--nd-green)'
                            : ['FAILED', 'REJECTED', 'CANCELLED'].includes(status) ? 'var(--nd-red)' : 'var(--nd-orange, #f59e0b)';
                          return (
                            <tr key={o.orderId || o.referenceId}>
                              <td style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-text-1)' }}>{o.symbol}</td>
                              <td style={{ textAlign: 'center', color: o.transactionType === 'SELL' ? 'var(--nd-red)' : 'var(--nd-green)', fontWeight: 600, fontSize: 12 }}>{o.transactionType}</td>
                              <td style={{ textAlign: 'right', fontSize: 12.5 }}>{o.quantity}</td>
                              <td style={{ textAlign: 'center', fontSize: 11.5, color: 'var(--nd-text-2)' }}>{o.orderType}</td>
                              <td style={{ textAlign: 'center', fontSize: 11, fontWeight: 700, color: stColor }}>{status}</td>
                              <td style={{ textAlign: 'center' }}>
                                {canCancel ? (
                                  <button onClick={() => cancelPendingOrder(o.orderId, o.segment)} disabled={cancellingId === o.orderId}
                                    style={{ fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 6, border: '1px solid #ef444455', background: '#ef44441a', color: '#ef4444', cursor: cancellingId === o.orderId ? 'wait' : 'pointer' }}>
                                    {cancellingId === o.orderId ? '…' : 'Cancel'}
                                  </button>
                                ) : (
                                  <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {!optimization && !optimizing && (
                <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>
                  Click <strong>Run optimization</strong> to generate an AI-driven rebalancing plan for your live holdings.
                </div>
              )}

              {optimization && (() => {
                const plan = optimization.plan || {};
                const risk = optimization.risk || {};
                const signals = optimization.signals || {};
                const actions: any[] = plan.actions || [];
                return (
                  <>
                    {/* Summary */}
                    <div className="nd-card" style={{ borderLeft: '3px solid var(--nd-green)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>Recommendation</span>
                        <span style={{ fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 6, background: 'var(--nd-surface-2)', color: 'var(--nd-text-3)' }}>
                          {String(optimization.source || '').startsWith('ai') ? 'AI-generated' : 'rule-based'}
                        </span>
                        {optimization.scanAt && (
                          <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
                            <span className="material-icons" style={{ fontSize: 12 }}>sync</span>
                            tracks AI scan · {new Date(optimization.scanAt).toLocaleString()}
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: 13.5, color: 'var(--nd-text-1)', lineHeight: 1.6 }}>{plan.summary}</div>
                      {plan.objective && <div style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 8 }}><strong>Objective:</strong> {plan.objective}</div>}
                      {plan.expectedEffect && <div style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 4 }}><strong>Expected effect:</strong> {plan.expectedEffect}</div>}
                    </div>

                    {/* Risk snapshot */}
                    <div className="nd-grid-3" style={{ gap: 12 }}>
                      {[
                        { label: 'Largest Position', value: `${risk.topSymbol ?? '—'} · ${risk.topWeightPct ?? 0}%`, icon: 'pie_chart' },
                        { label: 'Top Sector',       value: `${risk.topSector ?? '—'} · ${risk.topSectorPct ?? 0}%`, icon: 'category' },
                        { label: 'Effective Holdings', value: `${risk.effectiveHoldings ?? '—'} of ${risk.holdings ?? 0}`, icon: 'donut_large' },
                      ].map(c => (
                        <div key={c.label} className="nd-card" style={{ padding: '14px 18px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                            <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>{c.icon}</span>
                            <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>{c.label}</span>
                          </div>
                          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>{c.value}</div>
                        </div>
                      ))}
                    </div>

                    {/* Per-holding action plan */}
                    <div className="nd-card" style={{ padding: 0 }}>
                      <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)', fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>
                        Rebalancing Plan
                      </div>
                      <div style={{ overflowX: 'auto' }}>
                        <table className="nd-table">
                          <thead><tr>
                            <th>Symbol</th><th style={{ textAlign: 'center' }}>AI Signal</th><th style={{ textAlign: 'center' }}>Action</th>
                            <th style={{ textAlign: 'right' }}>Now</th><th style={{ textAlign: 'right' }}>Target</th>
                            <th>Reason / AI Alternative</th><th style={{ textAlign: 'center' }}>Execute</th>
                          </tr></thead>
                          <tbody>
                            {actions.map((a: any) => {
                              const st = ACTION_STYLE[a.action] || ACTION_STYLE.HOLD;
                              const sg = signals[a.symbol]?.signal;
                              const sgColor = sg === 'bullish' ? 'var(--nd-green)' : sg === 'bearish' ? 'var(--nd-red)' : 'var(--nd-text-3)';
                              const trade = a.trade;
                              const alt = a.alternative;
                              return (
                                <tr key={a.symbol}>
                                  <td style={{ fontWeight: 700, color: 'var(--nd-text-1)', fontFamily: 'monospace' }}>{a.symbol}</td>
                                  <td style={{ textAlign: 'center', color: sgColor, fontSize: 12, fontWeight: 600 }}>{sg ?? '—'}</td>
                                  <td style={{ textAlign: 'center' }}>
                                    <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 6, background: st.bg, color: st.color }}>{a.action}</span>
                                  </td>
                                  <td style={{ textAlign: 'right', fontSize: 12.5 }}>{a.currentWeightPct?.toFixed?.(1) ?? a.currentWeightPct ?? 0}%</td>
                                  <td style={{ textAlign: 'right', fontSize: 12.5, fontWeight: 600, color: 'var(--nd-accent)' }}>{a.targetWeightPct?.toFixed?.(1) ?? a.targetWeightPct ?? 0}%</td>
                                  <td style={{ fontSize: 11.5, color: 'var(--nd-text-2)', maxWidth: 380 }}>
                                    <div>{a.reason}</div>
                                    {alt && (
                                      <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                                        background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '6px 10px' }}>
                                        <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-green)' }}>swap_horiz</span>
                                        <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>Swap into</span>
                                        <span style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-green)' }}>{alt.symbol}</span>
                                        <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)' }}>{alt.reason}</span>
                                        {alt.buyQty > 0 && !(trade && trade.transactionType === 'SELL') && (
                                          <button onClick={() => askOrder({ symbol: alt.symbol, transactionType: 'BUY',
                                            quantity: alt.order?.quantity ?? alt.buyQty, orderType: alt.order?.orderType,
                                            price: alt.order?.limitPrice, exchange: alt.order?.exchange, product: alt.order?.product,
                                            estValue: alt.order?.estValue })}
                                            style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 6, border: '1px solid #22c55e55',
                                              background: '#22c55e1a', color: '#22c55e', cursor: 'pointer' }}>
                                            Buy {alt.buyQty}
                                          </button>
                                        )}
                                      </div>
                                    )}
                                  </td>
                                  <td style={{ textAlign: 'center' }}>
                                    {trade && trade.transactionType === 'SELL' && alt?.order ? (
                                      <button onClick={() => askSwap(a, signals[a.symbol])}
                                        title={`Sell ${trade.quantity} ${a.symbol} → Buy ${alt.order.quantity} ${alt.symbol}`}
                                        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 700, padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
                                          border: '1px solid #8b5cf680', background: '#8b5cf61a', color: '#a78bfa' }}>
                                        <span className="material-icons" style={{ fontSize: 13 }}>swap_horiz</span> Swap
                                      </button>
                                    ) : trade ? (
                                      <button onClick={() => askOrder({ symbol: a.symbol, transactionType: trade.transactionType,
                                        quantity: trade.quantity, orderType: trade.orderType, price: trade.limitPrice,
                                        exchange: trade.exchange, product: trade.product, estValue: trade.estValue })}
                                        style={{ fontSize: 11, fontWeight: 700, padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
                                          border: `1px solid ${trade.transactionType === 'SELL' ? '#ef444455' : '#22c55e55'}`,
                                          background: trade.transactionType === 'SELL' ? '#ef44441a' : '#22c55e1a',
                                          color: trade.transactionType === 'SELL' ? '#ef4444' : '#22c55e' }}>
                                        {trade.transactionType} {trade.quantity}
                                      </button>
                                    ) : (
                                      <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</span>
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    {/* Add candidates */}
                    {Array.isArray(plan.addCandidates) && plan.addCandidates.length > 0 && (
                      <div className="nd-card">
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)', marginBottom: 10 }}>Rotate Into — AI High-Conviction Picks</div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                          {plan.addCandidates.map((c: any) => (
                            <div key={c.symbol} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--nd-border)' }}>
                              <span style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-green)', width: 110 }}>{c.symbol}</span>
                              <span style={{ fontSize: 11, color: 'var(--nd-accent)', width: 70 }}>~{c.suggestedWeightPct}%</span>
                              <span style={{ fontSize: 12, color: 'var(--nd-text-2)', flex: 1 }}>{c.reason}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Risk warnings */}
                    {Array.isArray(plan.riskWarnings) && plan.riskWarnings.length > 0 && (
                      <div style={{ background: '#f59e0b10', border: '1px solid #f59e0b30', borderRadius: 12, padding: '14px 18px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                          <span className="material-icons" style={{ color: '#f59e0b', fontSize: 18 }}>warning_amber</span>
                          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)' }}>Risk Warnings</span>
                        </div>
                        <ul style={{ margin: 0, paddingLeft: 22, color: 'var(--nd-text-2)', fontSize: 12.5, lineHeight: 1.7 }}>
                          {plan.riskWarnings.map((w: string, i: number) => <li key={i}>{w}</li>)}
                        </ul>
                      </div>
                    )}

                    <div style={{ fontSize: 11, color: 'var(--nd-text-3)', textAlign: 'center' }}>
                      Advisory only — not investment advice. Source: {optimization.source}. Generated {optimization.asOf ? new Date(optimization.asOf).toLocaleString() : ''}.
                    </div>
                  </>
                );
              })()}
            </div>
          )}

          {/* ── AI Invest Tab ────────────────────────────────────────────────────── */}
          {activeTab === 'invest' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div className="nd-card" style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                <span className="material-icons" style={{ fontSize: 26, color: 'var(--nd-green)' }}>savings</span>
                <div style={{ flex: 1, minWidth: 220 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI Invest</div>
                  <div style={{ fontSize: 12.5, color: 'var(--nd-text-2)', marginTop: 2 }}>
                    Enter an amount and the AI divides it across the best-performing stocks from its live scan (graded A/B, conviction-weighted) — then place them all in one go.
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ position: 'relative' }}>
                    <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--nd-text-3)', fontSize: 14 }}>₹</span>
                    <input type="number" min={0} value={investAmount} onChange={e => setInvestAmount(e.target.value)}
                      placeholder="Amount" className="nd-input" style={{ width: 140, paddingLeft: 22 }} />
                  </div>
                  <button onClick={generateInvestPlan} disabled={investLoading}
                    style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '9px 16px', borderRadius: 10, border: 'none',
                      background: 'var(--nd-green)', color: '#fff', fontWeight: 600, fontSize: 14, cursor: investLoading ? 'wait' : 'pointer' }}>
                    <span className={`material-icons${investLoading ? ' nd-spin' : ''}`} style={{ fontSize: 18 }}>{investLoading ? 'autorenew' : 'auto_awesome'}</span>
                    {investLoading ? 'Building…' : 'Build plan'}
                  </button>
                </div>
              </div>

              {investData && investData.picks?.length > 0 && (
                <>
                  <div className="nd-card" style={{ display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'center' }}>
                    <div><div className="nd-label" style={{ margin: 0 }}>Amount</div><div style={{ fontSize: 18, fontWeight: 700 }}>₹{inr(investData.amount)}</div></div>
                    <div><div className="nd-label" style={{ margin: 0 }}>Deploying</div><div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-green)' }}>₹{inr(investData.deployed)}</div></div>
                    <div><div className="nd-label" style={{ margin: 0 }}>Leftover</div><div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-text-2)' }}>₹{inr(investData.leftover)}</div></div>
                    <div><div className="nd-label" style={{ margin: 0 }}>Stocks</div><div style={{ fontSize: 18, fontWeight: 700 }}>{investData.count}</div></div>
                    <button onClick={() => askInvestAll(investData.picks)}
                      style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, padding: '10px 18px', borderRadius: 10, border: 'none',
                        background: 'var(--nd-green)', color: '#fff', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>
                      <span className="material-icons" style={{ fontSize: 18 }}>shopping_cart_checkout</span>
                      Invest all
                    </button>
                  </div>

                  <div className="nd-card" style={{ padding: 0 }}>
                    <div style={{ overflowX: 'auto' }}>
                      <table className="nd-table">
                        <thead><tr>
                          <th>Stock</th><th style={{ textAlign: 'center' }}>Grade</th><th style={{ textAlign: 'right' }}>Win%</th>
                          <th style={{ textAlign: 'right' }}>Price</th><th style={{ textAlign: 'right' }}>Alloc</th>
                          <th style={{ textAlign: 'right' }}>Qty</th><th style={{ textAlign: 'right' }}>Cost</th>
                          <th>Why (AI)</th><th style={{ textAlign: 'center' }}>Buy</th>
                        </tr></thead>
                        <tbody>
                          {investData.picks.map((p: any) => (
                            <tr key={p.symbol}>
                              <td><div style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-text-1)' }}>{p.symbol}</div>
                                <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)' }}>{p.name} · {p.sector}</div></td>
                              <td style={{ textAlign: 'center', fontWeight: 700, color: p.grade === 'A' ? 'var(--nd-green)' : 'var(--nd-blue)' }}>{p.grade}</td>
                              <td style={{ textAlign: 'right', fontSize: 12.5, color: 'var(--nd-green)' }}>{p.winProbability != null ? `${Math.round(p.winProbability * 100)}%` : '—'}</td>
                              <td style={{ textAlign: 'right', fontSize: 12.5 }}>₹{inr(p.price)}</td>
                              <td style={{ textAlign: 'right', fontSize: 12.5, fontWeight: 600, color: 'var(--nd-accent)' }}>{p.weightPct}%</td>
                              <td style={{ textAlign: 'right', fontSize: 12.5 }}>{p.quantity}</td>
                              <td style={{ textAlign: 'right', fontSize: 12.5 }}>₹{inr(p.estCost)}</td>
                              <td style={{ fontSize: 11, color: 'var(--nd-text-3)', maxWidth: 320, lineHeight: 1.5 }}>{p.reasoning}</td>
                              <td style={{ textAlign: 'center' }}>
                                <button onClick={() => askOrder({ symbol: p.symbol, transactionType: 'BUY', quantity: p.order.quantity,
                                  orderType: p.order.orderType, price: p.order.limitPrice, exchange: p.order.exchange, product: p.order.product, estValue: p.order.estValue })}
                                  style={{ fontSize: 11, fontWeight: 700, padding: '4px 12px', borderRadius: 6, border: '1px solid #22c55e55', background: '#22c55e1a', color: '#22c55e', cursor: 'pointer' }}>
                                  Buy {p.quantity}
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)', textAlign: 'center' }}>
                    Conviction-weighted (win probability, capped 35%/stock) protective LIMIT buys. Advisory only — not investment advice.
                  </div>
                </>
              )}

              {investData && (!investData.picks || investData.picks.length === 0) && (
                <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>
                  {investData.note || 'No qualifying picks right now — try after the next AI scan, or increase the amount.'}
                </div>
              )}

              {!investData && !investLoading && (
                <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>
                  Enter the amount from your Groww wallet and click <strong>Build plan</strong> — the AI will split it across its best current picks.
                </div>
              )}
            </div>
          )}

          {/* ── Sector Exposure ── */}
          {activeTab === 'sectors' && (
            <div style={{ padding: '18px 20px' }}>
              {!sectorData ? (
                <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Scanning sector exposure…</div>
              ) : (
                <>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
                    {[
                      { label: 'Top sector', value: `${sectorData.topSector} · ${sectorData.topSectorPct}%`, color: sectorData.topSectorPct > 40 ? 'var(--nd-red)' : 'var(--nd-text-1)' },
                      { label: 'Effective sectors', value: sectorData.effectiveSectors, color: 'var(--nd-text-1)' },
                      { label: 'AI-favoured', value: (sectorData.aiFavoured ?? []).slice(0, 3).map((a: any) => a.sector).join(', ') || '—', color: 'var(--nd-green)' },
                    ].map((c, i) => (
                      <div key={i} className="nd-card" style={{ flex: '1 1 200px', padding: '12px 16px' }}>
                        <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{c.label}</div>
                        <div style={{ fontSize: 15, fontWeight: 700, color: c.color }}>{c.value}</div>
                      </div>
                    ))}
                  </div>

                  {(sectorData.warnings ?? []).map((w: string, i: number) => (
                    <div key={i} style={{ fontSize: 12, color: '#fca5a5', background: '#ef444415', border: '1px solid #ef444433', borderRadius: 8, padding: '8px 11px', marginBottom: 10 }}>⚠ {w}</div>
                  ))}

                  {/* Donut of current sector allocation */}
                  {(() => {
                    const PALETTE = ['#22c55e', '#3b82f6', '#a855f7', '#f59e0b', '#ef4444', '#06b6d4', '#ec4899', '#84cc16', '#94a3b8'];
                    const entries = Object.entries(sectorData.current || {}) as [string, number][];
                    if (!entries.length) return null;
                    const top = entries.slice(0, 8);
                    const restPct = entries.slice(8).reduce((s, [, v]) => s + (v as number), 0);
                    const segs = restPct > 0.1 ? [...top, ['Other', restPct] as [string, number]] : top;
                    const R = 52, C = 2 * Math.PI * R;
                    let off = 0;
                    return (
                      <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14, display: 'flex', gap: 22, alignItems: 'center', flexWrap: 'wrap' }}>
                        <svg width={140} height={140} viewBox="0 0 140 140" style={{ flexShrink: 0 }}>
                          <g transform="rotate(-90 70 70)">
                            {segs.map(([sec, pct], i) => {
                              const len = (pct / 100) * C;
                              const el = (
                                <circle key={sec} cx={70} cy={70} r={R} fill="none"
                                  stroke={PALETTE[i % PALETTE.length]} strokeWidth={16}
                                  strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-off} />
                              );
                              off += len; return el;
                            })}
                          </g>
                          <text x={70} y={66} textAnchor="middle" fontSize="11" fill="var(--nd-text-3)">sectors</text>
                          <text x={70} y={82} textAnchor="middle" fontSize="15" fontWeight="700" fill="var(--nd-text-1)">{entries.length}</text>
                        </svg>
                        <div style={{ flex: 1, minWidth: 180, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '4px 14px' }}>
                          {segs.map(([sec, pct], i) => (
                            <div key={sec} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12 }}>
                              <span style={{ width: 10, height: 10, borderRadius: 2, background: PALETTE[i % PALETTE.length], flexShrink: 0 }} />
                              <span style={{ color: 'var(--nd-text-2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sec}</span>
                              <span style={{ color: 'var(--nd-text-1)', fontWeight: 600 }}>{(pct as number).toFixed(0)}%</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })()}

                  <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 10 }}>Your sectors vs the AI-favoured target</div>
                    {(sectorData.sectors ?? []).map((r: any) => {
                      const col = r.status === 'overweight' ? '#ef4444' : r.status === 'underweight' ? '#f59e0b' : '#22c55e';
                      return (
                        <div key={r.sector} style={{ marginBottom: 11 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 3 }}>
                            <span style={{ fontWeight: 600, color: 'var(--nd-text-1)' }}>{r.sector} <span style={{ color: 'var(--nd-text-3)', fontWeight: 400 }}>({r.holdingCount})</span></span>
                            <span style={{ color: 'var(--nd-text-2)' }}>now {r.currentPct}% · target {r.targetPct}% <span style={{ color: col, fontWeight: 700 }}>{r.status}</span></span>
                          </div>
                          <div style={{ position: 'relative', height: 8, background: 'var(--nd-border)', borderRadius: 4 }}>
                            <div style={{ position: 'absolute', height: 8, borderRadius: 4, width: `${Math.min(100, r.currentPct)}%`, background: col, opacity: 0.85 }} />
                            <div style={{ position: 'absolute', height: 8, width: 2, background: 'var(--nd-text-1)', left: `${Math.min(100, r.targetPct)}%` }} title={`AI target ${r.targetPct}%`} />
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {(sectorData.suggestions ?? []).length > 0 && (
                    <div className="nd-card" style={{ padding: '14px 18px' }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 10 }}>AI rebalance moves</div>
                      {sectorData.suggestions.map((s: any, i: number) => (
                        <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12.5 }}>
                          <span style={{ fontSize: 10, fontWeight: 700, color: s.action === 'ADD' ? '#22c55e' : '#ef4444', minWidth: 38 }}>{s.action}</span>
                          <span style={{ fontWeight: 700, color: 'var(--nd-text-1)', minWidth: 120 }}>{s.sector}</span>
                          <span style={{ color: 'var(--nd-text-2)' }}>{s.reason}{s.stock ? ` → ${s.action === 'ADD' ? 'buy' : 'trim'} ${s.stock}` : ''}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* ── AI Fund Baskets ── */}
          {activeTab === 'funds' && (
            <div style={{ padding: '18px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
                <span style={{ fontSize: 12.5, color: 'var(--nd-text-2)' }}>Invest amount ₹</span>
                <input value={basketAmt} onChange={e => setBasketAmt(e.target.value.replace(/[^0-9]/g, ''))}
                  className="nd-input" style={{ width: 140 }} placeholder="25000" />
                <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>AI-built, mutual-fund-style stock baskets from the live scan — pick one and invest across it in one click.</span>
              </div>
              {baskets.length === 0 ? (
                <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Building baskets from the latest AI scan…</div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))', gap: 14 }}>
                  {baskets.map((b: any) => {
                    const open = openBasket === b.id;
                    return (
                      <div key={b.id} className="nd-card" style={{ padding: '14px 16px', minWidth: 0 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>{b.name}</div>
                          <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--nd-purple)', border: '1px solid var(--nd-purple)', borderRadius: 5, padding: '1px 6px' }}>{b.risk}</span>
                        </div>
                        <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', margin: '4px 0 8px' }}>{b.description}</div>
                        <div style={{ fontSize: 11, color: 'var(--nd-text-2)', marginBottom: 8 }}>
                          {b.stats.size} stocks · {b.stats.sectors} sectors · avg win-prob {(b.stats.avgWinProbability * 100).toFixed(0)}%
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 8 }}>
                          {(open ? b.holdings : b.holdings.slice(0, 4)).map((h: any) => (
                            <div key={h.symbol} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                              <span style={{ fontFamily: 'monospace', fontWeight: 600, color: 'var(--nd-text-1)', minWidth: 96 }}>{h.symbol}</span>
                              <span style={{ color: 'var(--nd-text-3)', fontSize: 10.5, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.sector}</span>
                              <span style={{ color: 'var(--nd-green)', fontWeight: 700 }}>{h.weightPct}%</span>
                            </div>
                          ))}
                          {b.holdings.length > 4 && (
                            <button onClick={() => setOpenBasket(open ? null : b.id)} style={{ alignSelf: 'flex-start', background: 'none', border: 'none', color: 'var(--nd-blue)', cursor: 'pointer', fontSize: 11, padding: 0 }}>
                              {open ? 'show less' : `+${b.holdings.length - 4} more`}
                            </button>
                          )}
                        </div>
                        <button onClick={() => askInvestBasket(b)} className="nd-btn"
                          style={{ width: '100%', background: 'var(--nd-green)', color: '#fff', border: 'none', borderRadius: 8, padding: '8px 0', fontSize: 12.5, fontWeight: 700, cursor: 'pointer' }}>
                          Invest ₹{Number(basketAmt || 0).toLocaleString('en-IN')}
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* ── AI Advisor ── */}
          {activeTab === 'advisor' && (
            <div style={{ padding: '18px 20px' }}>
              {/* Benchmark vs NIFTY */}
              <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Portfolio vs NIFTY 50</div>
                {!bench ? <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Comparing to benchmark…</div>
                  : bench.note ? <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{bench.note}</div>
                  : (
                    <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
                      {(bench.periods ?? []).map((p: any) => (
                        <div key={p.key} style={{ minWidth: 120 }}>
                          <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{p.label}</div>
                          <div style={{ fontSize: 14 }}>You <strong style={{ color: pctColor(p.portfolio) }}>{pct(p.portfolio)}</strong></div>
                          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>NIFTY {pct(p.benchmark)}</div>
                          <div style={{ fontSize: 12, fontWeight: 700, color: pctColor(p.alpha) }}>{p.alpha >= 0 ? 'α +' : 'α '}{p.alpha != null ? `${p.alpha}%` : '—'}</div>
                        </div>
                      ))}
                    </div>
                  )}
              </div>
              {/* AI insights feed */}
              <div className="nd-card" style={{ padding: '14px 18px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>AI advisor insights</div>
                  {advisor?.source && <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{advisor.source === 'llm' ? 'AI-generated' : 'rule-based'}{advisor.score != null ? ` · health ${advisor.score}/100` : ''}</span>}
                </div>
                {!advisor ? <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Analysing your portfolio…</div>
                  : (advisor.insights ?? []).map((ins: string, i: number) => (
                    <div key={i} style={{ display: 'flex', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12.5 }}>
                      <span style={{ color: 'var(--nd-green)' }}>▸</span><span style={{ color: 'var(--nd-text-2)' }}>{ins}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* ── Portfolio Health Score ── */}
          {activeTab === 'health' && (
            <div style={{ padding: '18px 20px' }}>
              {!health ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Analysing portfolio health…</div>
              ) : health.score == null ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>{health.note}</div>
              ) : (() => {
                const sc = health.score; const col = sc >= 70 ? '#22c55e' : sc >= 55 ? '#f59e0b' : '#ef4444';
                return (
                  <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap' }}>
                    <div className="nd-card" style={{ padding: '18px 22px', textAlign: 'center', minWidth: 180 }}>
                      <svg width={150} height={150} viewBox="0 0 150 150">
                        <circle cx={75} cy={75} r={62} fill="none" stroke="var(--nd-border)" strokeWidth={12} />
                        <circle cx={75} cy={75} r={62} fill="none" stroke={col} strokeWidth={12} strokeLinecap="round"
                          strokeDasharray={`${2 * Math.PI * 62 * sc / 100} ${2 * Math.PI * 62}`} transform="rotate(-90 75 75)" />
                        <text x={75} y={70} textAnchor="middle" fontSize="34" fontWeight="700" fill={col}>{sc.toFixed(0)}</text>
                        <text x={75} y={94} textAnchor="middle" fontSize="12" fill="var(--nd-text-3)">/ 100 · {health.grade}</text>
                      </svg>
                      <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 4 }}>{health.metrics.holdings} holdings · ~{health.metrics.effectiveHoldings} effective</div>
                    </div>
                    <div style={{ flex: 1, minWidth: 280 }}>
                      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Health factors</div>
                      {health.factors.map((f: any) => {
                        const fc = f.score >= 70 ? '#22c55e' : f.score >= 50 ? '#f59e0b' : '#ef4444';
                        return (
                          <div key={f.key} style={{ marginBottom: 8 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 2 }}>
                              <span style={{ color: 'var(--nd-text-2)' }}>{f.label} <span style={{ color: 'var(--nd-text-3)' }}>({f.weight}%)</span></span>
                              <span style={{ color: fc, fontWeight: 700 }}>{f.score.toFixed(0)}</span>
                            </div>
                            <div style={{ height: 7, background: 'var(--nd-border)', borderRadius: 4 }}><div style={{ height: 7, width: `${f.score}%`, background: fc, borderRadius: 4 }} /></div>
                          </div>
                        );
                      })}
                      <div style={{ marginTop: 14, fontSize: 13, fontWeight: 700 }}>Issues &amp; fixes</div>
                      {health.issues.map((s: string, i: number) => <div key={i} style={{ fontSize: 12, color: 'var(--nd-text-2)', padding: '3px 0' }}>• {s}</div>)}
                      {health.actions.map((s: string, i: number) => <div key={i} style={{ fontSize: 12, color: 'var(--nd-green)', padding: '3px 0' }}>→ {s}</div>)}
                    </div>
                  </div>
                );
              })()}
            </div>
          )}

          {/* ── Goal Planner ── */}
          {activeTab === 'planner' && (
            <div style={{ padding: '18px 20px' }}>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 16 }}>
                {[['goalAmount', 'Goal ₹'], ['years', 'Years'], ['currentCorpus', 'Current corpus ₹'], ['monthly', 'Monthly SIP ₹ (optional)']].map(([k, label]) => (
                  <div key={k}><div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 3 }}>{label}</div>
                    <input className="nd-input" style={{ width: k === 'years' ? 80 : 150 }} value={(planForm as any)[k]}
                      onChange={e => setPlanForm({ ...planForm, [k]: e.target.value.replace(/[^0-9]/g, '') })} /></div>
                ))}
                <div><div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 3 }}>Risk</div>
                  <select className="nd-input" value={planForm.risk} onChange={e => setPlanForm({ ...planForm, risk: e.target.value })}>
                    <option value="conservative">Conservative</option><option value="moderate">Moderate</option><option value="aggressive">Aggressive</option>
                  </select></div>
                <button onClick={() => setQuizOpen(true)} style={{ padding: '9px 14px', borderRadius: 8, border: '1px solid var(--nd-blue)', background: 'transparent', color: 'var(--nd-blue)', fontWeight: 600, fontSize: 12, cursor: 'pointer' }}>📋 Find my risk</button>
                <button onClick={runPlan} disabled={planning} style={{ padding: '9px 18px', borderRadius: 8, border: 'none', background: 'var(--nd-green)', color: '#fff', fontWeight: 700, fontSize: 12.5, cursor: 'pointer' }}>{planning ? 'Planning…' : 'Plan'}</button>
              </div>
              {plan && (
                <>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                    {[
                      ['Required SIP', plan.requiredSip != null ? `₹${inr(plan.requiredSip)}/mo` : `₹${inr(plan.monthlySip)}/mo`],
                      ['Projected corpus', `₹${inr(plan.projectedCorpus)}`],
                      ['You invest', `₹${inr(plan.invested)}`],
                      ['Wealth gained', `₹${inr(plan.wealthGained)}`],
                      ['Assumed return', `${plan.assumedReturnPct}% p.a.`],
                    ].map(([l, v], i) => (
                      <div key={i} className="nd-card" style={{ padding: '12px 16px' }}>
                        <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{l}</div>
                        <div style={{ fontSize: 15, fontWeight: 700, color: i === 3 ? 'var(--nd-green)' : 'var(--nd-text-1)' }}>{v}</div>
                      </div>
                    ))}
                  </div>
                  {plan.goalAmount && (
                    <div style={{ fontSize: 12.5, marginBottom: 14, color: plan.onTrack ? 'var(--nd-green)' : '#f59e0b' }}>
                      {plan.onTrack ? `✓ On track — projected ₹${inr(plan.projectedCorpus)} meets your ₹${inr(plan.goalAmount)} goal.` : `⚠ Short of goal — increase the SIP or horizon. Range: ₹${inr(plan.pessimistic)}–₹${inr(plan.optimistic)}.`}
                    </div>
                  )}
                  {/* projection chart */}
                  {plan.projection?.length > 1 && (() => {
                    const pts = plan.projection; const W = 600, H = 140, PL = 8, PR = 8, PT = 10, PB = 18;
                    const max = Math.max(...pts.map((p: any) => p.optimistic));
                    const sx = (i: number) => PL + i / (pts.length - 1) * (W - PL - PR);
                    const sy = (v: number) => PT + (1 - v / max) * (H - PT - PB);
                    const line = (key: string) => pts.map((p: any, i: number) => `${sx(i).toFixed(1)},${sy(p[key]).toFixed(1)}`).join(' ');
                    return (
                      <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
                        <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 6 }}>Projected growth ({plan.years} yrs)</div>
                        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 140 }} preserveAspectRatio="none">
                          <polyline points={`${line('optimistic')} ${pts.map((p: any, i: number) => `${sx(pts.length - 1 - i).toFixed(1)},${sy(p.pessimistic ? pts[pts.length - 1 - i].pessimistic : 0).toFixed(1)}`).join(' ')}`} fill="#22c55e15" stroke="none" />
                          <polyline points={line('expected')} fill="none" stroke="#22c55e" strokeWidth="2" />
                          <text x={PL} y={H - 6} fontSize="9" fill="var(--nd-text-3)">Yr 1</text>
                          <text x={W - PR} y={H - 6} fontSize="9" fill="var(--nd-text-3)" textAnchor="end">Yr {plan.years}</text>
                        </svg>
                      </div>
                    );
                  })()}
                  <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>AI asset allocation ({plan.risk})</div>
                  {plan.sleeves.map((s: any) => (
                    <div key={s.sleeve} style={{ marginBottom: 8 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5 }}><span style={{ fontWeight: 600 }}>{s.sleeve} · {s.pct}%</span></div>
                      <div style={{ height: 7, background: 'var(--nd-border)', borderRadius: 4, margin: '3px 0' }}><div style={{ height: 7, width: `${s.pct}%`, background: 'var(--nd-blue)', borderRadius: 4 }} /></div>
                      <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{s.how}</div>
                    </div>
                  ))}
                  <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginTop: 8 }}>{plan.note}</div>
                </>
              )}
            </div>
          )}

          {/* ── Tax Harvest ── */}
          {activeTab === 'tax' && (
            <div style={{ padding: '18px 20px' }}>
              {!tax ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Analysing capital gains…</div>
              ) : tax.note ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>{tax.note}</div>
              ) : (
                <>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
                    {[
                      ['Unrealised gains', `₹${inr(tax.unrealisedGains)}`, 'var(--nd-green)'],
                      ['Harvestable losses', `₹${inr(tax.harvestableLosses)}`, 'var(--nd-red)'],
                      ['Potential offset', `₹${inr(tax.potentialOffset)}`, 'var(--nd-text-1)'],
                      ['Est. tax saved', `₹${inr(tax.estTaxSaved)}`, 'var(--nd-green)'],
                    ].map(([l, v, c], i) => (
                      <div key={i} className="nd-card" style={{ padding: '12px 16px' }}><div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{l}</div><div style={{ fontSize: 15, fontWeight: 700, color: c as string }}>{v}</div></div>
                    ))}
                  </div>
                  {tax.tips.map((t: string, i: number) => <div key={i} style={{ fontSize: 12, color: 'var(--nd-text-2)', padding: '3px 0' }}>💡 {t}</div>)}
                  {tax.harvestCandidates?.length > 0 && (
                    <div className="nd-card" style={{ padding: '12px 16px', marginTop: 12 }}>
                      <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 6 }}>Loss-harvest candidates</div>
                      {tax.harvestCandidates.map((c: any) => (
                        <div key={c.symbol} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, padding: '5px 0', borderBottom: '1px solid var(--nd-border)' }}>
                          <span style={{ fontWeight: 600 }}>{c.symbol}</span>
                          <span style={{ color: 'var(--nd-red)' }}>₹{inr(c.gain)} ({c.gainPct}%)</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginTop: 12 }}>{tax.caveat}</div>
                </>
              )}
            </div>
          )}
        </>
      )}

      {/* ── Risk-profile questionnaire ── */}
      {quizOpen && (
        <div onClick={() => setQuizOpen(false)} style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={e => e.stopPropagation()} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 14, width: '100%', maxWidth: 460, maxHeight: '88vh', overflow: 'auto', padding: 22 }}>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>Find your risk profile</div>
            <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginBottom: 14 }}>5 quick questions → recommended risk level for your plan.</div>
            {RISK_QUIZ.map((item, qi) => (
              <div key={qi} style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 5 }}>{item.q}</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {item.opts.map(([label, pts]) => (
                    <button key={label} onClick={() => setQuizAns(a => a.map((v, i) => i === qi ? pts : v))} style={{
                      padding: '5px 10px', fontSize: 11.5, borderRadius: 7, cursor: 'pointer',
                      border: `1px solid ${quizAns[qi] === pts ? 'var(--nd-green)' : 'var(--nd-border)'}`,
                      background: quizAns[qi] === pts ? 'rgba(34,197,94,0.12)' : 'transparent',
                      color: quizAns[qi] === pts ? 'var(--nd-green)' : 'var(--nd-text-2)',
                    }}>{label}</button>
                  ))}
                </div>
              </div>
            ))}
            {(() => { const total = quizAns.reduce((s, v) => s + v, 0); const risk = riskFromScore(total);
              return (
                <div style={{ marginTop: 8, padding: '10px 12px', background: 'var(--nd-surface)', borderRadius: 8, fontSize: 12.5 }}>
                  Recommended: <strong style={{ color: 'var(--nd-green)', textTransform: 'capitalize' }}>{risk}</strong>
                  <span style={{ color: 'var(--nd-text-3)' }}> (score {total}/15)</span>
                  <button onClick={() => { setPlanForm({ ...planForm, risk }); setQuizOpen(false); }} style={{ float: 'right', padding: '6px 14px', borderRadius: 7, border: 'none', background: 'var(--nd-green)', color: '#fff', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>Use this</button>
                </div>
              ); })()}
          </div>
        </div>
      )}

      {/* ── Order result toast ── */}
      {orderMsg && (
        <div onClick={() => setOrderMsg(null)}
          style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 1100, maxWidth: 360, cursor: 'pointer',
            background: orderMsg.ok ? 'var(--nd-green-50)' : 'var(--nd-red-50)',
            border: `1px solid ${orderMsg.ok ? '#22c55e55' : '#ef444455'}`, borderRadius: 10, padding: '12px 16px',
            display: 'flex', alignItems: 'center', gap: 10, boxShadow: 'var(--nd-shadow-md)' }}>
          <span className="material-icons" style={{ color: orderMsg.ok ? 'var(--nd-green)' : 'var(--nd-red)', fontSize: 20 }}>
            {orderMsg.ok ? 'check_circle' : 'error_outline'}
          </span>
          <span style={{ fontSize: 13, color: orderMsg.ok ? '#065f46' : 'var(--nd-red)' }}>{orderMsg.text}</span>
        </div>
      )}

      {/* ── Order confirmation modal ── */}
      {pendingOrder && (
        <div onClick={e => { if (e.target === e.currentTarget && !placing) setPendingOrder(null); }}
          style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 14, width: '100%', maxWidth: 460, maxHeight: '88vh', overflow: 'auto', padding: 22 }}>
            {pendingOrder.kind === 'basket' ? (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                  <span className="material-icons" style={{ color: 'var(--nd-green)' }}>shopping_cart_checkout</span>
                  <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>Confirm investment</span>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--nd-text-2)', marginBottom: 10 }}>
                  Placing <strong style={{ color: 'var(--nd-text-1)' }}>{pendingOrder.legs.length}</strong> protective LIMIT buy orders
                  {' '}(~₹{inr(pendingOrder.legs.reduce((s: number, l: any) => s + (l.estValue || 0), 0))} total):
                </div>
                <div style={{ maxHeight: 240, overflow: 'auto', marginBottom: 12 }}>
                  {pendingOrder.legs.map((l: any) => (
                    <div key={l.symbol} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12.5 }}>
                      <strong style={{ color: 'var(--nd-green)' }}>BUY {l.quantity}</strong>
                      <strong style={{ fontFamily: 'monospace', color: 'var(--nd-text-1)' }}>{l.symbol}</strong>
                      <span style={{ marginLeft: 'auto', color: 'var(--nd-text-3)' }}>{l.orderType === 'LIMIT' && l.price ? `LIMIT @ ₹${l.price}` : 'MARKET'} · ~₹{inr(l.estValue || 0)}</span>
                    </div>
                  ))}
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--nd-red)', background: 'var(--nd-red-50)', borderRadius: 8, padding: '8px 10px', marginBottom: 14 }}>
                  Places <strong>{pendingOrder.legs.length} real orders on your Groww account</strong>, one after another. Executes during market hours; needs sufficient funds.
                </div>
              </>
            ) : pendingOrder.kind === 'swap' ? (() => {
              const { sell, buy } = pendingOrder;
              const Leg = ({ leg, n }: { leg: any; n: string }) => (
                <div style={{ border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 8 }}>
                  <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', fontWeight: 700, letterSpacing: 0.5, marginBottom: 3 }}>{n}</div>
                  <div style={{ fontSize: 13.5 }}>
                    <strong style={{ color: leg.transactionType === 'SELL' ? 'var(--nd-red)' : 'var(--nd-green)' }}>{leg.transactionType} {leg.quantity}</strong>
                    {' '}<strong style={{ color: 'var(--nd-text-1)', fontFamily: 'monospace' }}>{leg.symbol}</strong>
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginTop: 2 }}>
                    {leg.orderType === 'LIMIT' && leg.price ? `LIMIT @ ₹${leg.price}` : 'MARKET'} · {leg.exchange || 'NSE'} · CNC
                    {leg.estValue ? ` · ~₹${inr(leg.estValue)}` : ''}
                  </div>
                </div>
              );
              return (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                    <span className="material-icons" style={{ color: '#a78bfa' }}>swap_horiz</span>
                    <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>Confirm swap</span>
                  </div>
                  <Leg leg={sell} n="STEP 1 — SELL FIRST" />
                  <div style={{ textAlign: 'center', color: 'var(--nd-text-3)', margin: '-2px 0 6px' }}>
                    <span className="material-icons" style={{ fontSize: 18 }}>south</span>
                  </div>
                  <Leg leg={buy} n="STEP 2 — THEN BUY" />

                  {/* Why the AI recommends this swap */}
                  {pendingOrder.basis && (() => {
                    const b = pendingOrder.basis;
                    const sc = b.sell.signal === 'bullish' ? 'var(--nd-green)' : b.sell.signal === 'bearish' ? 'var(--nd-red)' : 'var(--nd-text-2)';
                    const Fact = ({ k, v, c }: { k: string; v: any; c?: string }) => (v === undefined || v === null || v === '') ? null : (
                      <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{k} <strong style={{ color: c || 'var(--nd-text-1)' }}>{v}</strong></span>
                    );
                    return (
                      <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 10 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                          <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-accent)' }}>insights</span>
                          <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--nd-text-1)' }}>Why the AI suggests this swap</span>
                        </div>
                        <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginBottom: 4 }}>
                          <strong style={{ color: 'var(--nd-red)' }}>Out — {sell.symbol}:</strong> {b.sell.reason}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 8 }}>
                          <Fact k="AI signal" v={b.sell.signal} c={sc} />
                          <Fact k="health" v={b.sell.health != null ? `${b.sell.health}/100` : null} />
                          <Fact k="RSI" v={b.sell.rsi} />
                          <Fact k="momentum" v={b.sell.momentum != null ? `${b.sell.momentum > 0 ? '+' : ''}${b.sell.momentum}%` : null} />
                          <Fact k="trend" v={b.sell.trend} />
                          <Fact k="P&L" v={b.sell.pnl != null ? `${b.sell.pnl > 0 ? '+' : ''}${b.sell.pnl}%` : null} c={(b.sell.pnl ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)'} />
                          <Fact k="weight" v={`${b.sell.weightNow ?? '–'}% → ${b.sell.weightTarget ?? '–'}%`} />
                        </div>
                        <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginBottom: 4 }}>
                          <strong style={{ color: 'var(--nd-green)' }}>In — {buy.symbol}:</strong>{' '}
                          AI grade <strong style={{ color: 'var(--nd-text-1)' }}>{b.buy.grade}</strong>
                          {b.buy.winProb != null ? <> · win prob <strong style={{ color: 'var(--nd-green)' }}>{Math.round(b.buy.winProb * 100)}%</strong></> : null}
                          {b.buy.sameSector ? <> · <span style={{ color: 'var(--nd-accent)' }}>same sector ({b.buy.sector})</span></> : (b.buy.sector ? <> · {b.buy.sector}</> : null)}
                        </div>
                        {b.buy.reasoning && (
                          <div style={{ fontSize: 11, color: 'var(--nd-text-3)', lineHeight: 1.5 }}>{b.buy.reasoning}</div>
                        )}
                      </div>
                    );
                  })()}

                  <div style={{ fontSize: 11.5, color: 'var(--nd-red)', background: 'var(--nd-red-50)', borderRadius: 8, padding: '8px 10px', margin: '6px 0 14px' }}>
                    Places <strong>two real orders on Groww</strong> — the SELL first, then the BUY (only if the sell is accepted). Executes during market hours.
                  </div>
                </>
              );
            })() : (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                  <span className="material-icons" style={{ color: pendingOrder.transactionType === 'SELL' ? 'var(--nd-red)' : 'var(--nd-green)' }}>
                    {pendingOrder.transactionType === 'SELL' ? 'sell' : 'shopping_cart'}
                  </span>
                  <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>Confirm {pendingOrder.transactionType} order</span>
                </div>
                <div style={{ fontSize: 13.5, color: 'var(--nd-text-2)', lineHeight: 1.7, marginBottom: 8 }}>
                  <div><strong style={{ color: 'var(--nd-text-1)' }}>{pendingOrder.transactionType} {pendingOrder.quantity}</strong> × <strong style={{ color: 'var(--nd-text-1)', fontFamily: 'monospace' }}>{pendingOrder.symbol}</strong></div>
                  <div>
                    {pendingOrder.orderType === 'LIMIT' && pendingOrder.price
                      ? <>Type: <strong style={{ color: 'var(--nd-text-1)' }}>LIMIT @ ₹{pendingOrder.price}</strong> (protective collar)</>
                      : 'Type: MARKET'} · Product: CNC · {pendingOrder.exchange || 'NSE'}
                  </div>
                  {(pendingOrder.estValue || (pendingOrder.price && pendingOrder.quantity)) ? (
                    <div>Est. value: ₹{inr(pendingOrder.estValue ?? pendingOrder.quantity * pendingOrder.price)}</div>
                  ) : null}
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--nd-red)', background: 'var(--nd-red-50)', borderRadius: 8, padding: '8px 10px', marginBottom: 14 }}>
                  This places a <strong>real order on your Groww account</strong>. Executes only during market hours.
                </div>
              </>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button onClick={() => setPendingOrder(null)} disabled={placing}
                style={{ padding: '8px 16px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: 'var(--nd-text-2)', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
                Cancel
              </button>
              <button onClick={confirmOrder} disabled={placing}
                style={{ padding: '8px 18px', borderRadius: 8, border: 'none', cursor: placing ? 'wait' : 'pointer', fontSize: 13, fontWeight: 700, color: '#fff',
                  background: pendingOrder.kind === 'swap' ? '#8b5cf6'
                    : pendingOrder.kind === 'basket' ? 'var(--nd-green)'
                    : (pendingOrder.transactionType === 'SELL' ? 'var(--nd-red)' : 'var(--nd-green)') }}>
                {placing ? 'Placing…'
                  : pendingOrder.kind === 'swap' ? 'Confirm swap'
                  : pendingOrder.kind === 'basket' ? `Invest in ${pendingOrder.legs.length} stocks`
                  : `Confirm ${pendingOrder.transactionType}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PortfolioPage;
