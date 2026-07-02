import React, { useEffect, useState, useMemo } from 'react';
import apiService from '../services/api';
import ScanControl from '../components/ScanControl';
import { Portfolio, Performance, Alert, OrderRecord, TradeLeg, PendingOrder, InvestableBasket, InvestPick, BasketInvestPick, RebalanceActionLike, AiSignalLike } from '../types';
import { getErrorMessage } from '../utils/errors';
import { inr, calcHHI, calcVaR, RISK_QUIZ, SortKey, SortDir, Tab, TABS } from '../components/portfolio/shared';
import HoldingsTab from '../components/portfolio/HoldingsTab';
import PerformanceTab from '../components/portfolio/PerformanceTab';
import RiskTab from '../components/portfolio/RiskTab';
import OptimizeTab from '../components/portfolio/OptimizeTab';
import InvestTab from '../components/portfolio/InvestTab';
import SectorsTab from '../components/portfolio/SectorsTab';
import AdvisorTab from '../components/portfolio/AdvisorTab';
import HealthTab from '../components/portfolio/HealthTab';
import PlannerTab from '../components/portfolio/PlannerTab';
import TaxTab from '../components/portfolio/TaxTab';
import OrderModal from '../components/portfolio/OrderModal';
import RiskQuizModal from '../components/portfolio/RiskQuizModal';

const PortfolioPage: React.FC = () => {
  const [portfolio,   setPortfolio]   = useState<Portfolio | null>(null);
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [loading,     setLoading]     = useState(true);
  const [alerts,      setAlerts]      = useState<Alert[]>([]);
  const [sortKey,     setSortKey]     = useState<SortKey>('value');
  const [sortDir,     setSortDir]     = useState<SortDir>('desc');
  const [activeTab,   setActiveTab]   = useState<Tab>('holdings');
  const [optimization, setOptimization] = useState<any>(null);
  const [optimizing,   setOptimizing]   = useState(false);

  useEffect(() => { fetchPortfolioData(); }, []);

  const [orders, setOrders] = useState<OrderRecord[]>([]);
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
    try { const res = await apiService.listOrders(); setOrders((res.data as OrderRecord[]) || []); } catch { /* ignore */ }
  };

  const cancelPendingOrder = async (orderId: string, segment: string) => {
    setCancellingId(orderId);
    try {
      const res = await apiService.cancelOrder(orderId, segment || 'CASH');
      setOrderMsg({ ok: res.status === 'success', text: res.status === 'success' ? `Order ${orderId} cancelled.` : (res.message || 'Cancel failed') });
    } catch (e: unknown) {
      setOrderMsg({ ok: false, text: getErrorMessage(e, 'Cancel failed') });
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
  const [pendingOrder, setPendingOrder] = useState<PendingOrder | null>(null);
  const [orderMsg, setOrderMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [placing, setPlacing] = useState(false);

  // Place one leg; never throws — returns {ok, text}.
  const placeOne = async (spec: TradeLeg): Promise<{ ok: boolean; text: string }> => {
    try {
      const res = await apiService.placeOrder({
        symbol: spec.symbol,
        quantity: spec.quantity,
        transactionType: spec.transactionType,
        orderType: (spec.orderType as 'MARKET' | 'LIMIT') || 'MARKET',
        price: spec.price,
        product: (spec.product as 'CNC' | 'INTRADAY') || 'CNC',
        exchange: spec.exchange || 'NSE',
      });
      const ok = res.status === 'success';
      return { ok, text: ok
        ? `${spec.transactionType} ${spec.quantity} ${spec.symbol} placed`
        : `${spec.symbol}: ${res.message || 'failed'}` };
    } catch (e: unknown) {
      return { ok: false, text: `${spec.transactionType} ${spec.symbol} failed: ${getErrorMessage(e)}` };
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

  const askOrder = (spec: TradeLeg) => {
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
  // AI Invest sub-sections: quick split / fund baskets / thematic baskets
  const [investSub, setInvestSub] = useState<'quick' | 'funds' | 'themes'>('quick');
  // AI Themes (smallcase-style)
  const [themes, setThemes] = useState<any[]>([]);
  const [themesLoading, setThemesLoading] = useState(false);
  const [openTheme, setOpenTheme] = useState<string | null>(null);
  const [themeAnalytics, setThemeAnalytics] = useState<Record<string, any>>({});
  const [themeRebal, setThemeRebal] = useState<Record<string, any>>({});
  const [themeBusy, setThemeBusy] = useState<string | null>(null);
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
    if (activeTab === 'invest') apiService.fundBaskets().then(r => setBaskets((r as any).data?.baskets ?? [])).catch(() => {});
    if (activeTab === 'invest' && themes.length === 0) {
      setThemesLoading(true);
      apiService.themes().then(r => setThemes((r as any).data?.themes ?? []))
        .catch(() => {}).finally(() => setThemesLoading(false));
    }
    if (activeTab === 'health') apiService.portfolioHealth().then(r => setHealth((r as any).data)).catch(() => {});
    if (activeTab === 'tax') apiService.taxHarvest().then(r => setTax((r as any).data)).catch(() => {});
    if (activeTab === 'advisor') {
      apiService.portfolioBenchmark().then(r => setBench((r as any).data)).catch(() => {});
      apiService.portfolioAdvisor().then(r => setAdvisor((r as any).data)).catch(() => {});
    }
    if (activeTab === 'risk') apiService.riskLab().then(r => setRiskLab((r as any).data)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);
  const [riskLab, setRiskLab] = useState<any>(null);

  const toggleTheme = async (id: string) => {
    if (openTheme === id) { setOpenTheme(null); return; }
    setOpenTheme(id);
    if (!themeAnalytics[id]) {
      setThemeBusy(id);
      try {
        const r = await apiService.themeAnalytics(id);
        setThemeAnalytics(prev => ({ ...prev, [id]: (r as any).data?.analytics ?? { ok: false } }));
      } catch { /* leave undefined */ } finally { setThemeBusy(null); }
    }
  };
  const loadRebalance = async (id: string) => {
    setThemeBusy(id + ':rb');
    try {
      const r = await apiService.themeRebalance(id);
      setThemeRebal(prev => ({ ...prev, [id]: (r as any).data }));
    } catch { /* ignore */ } finally { setThemeBusy(null); }
  };

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

  const askInvestBasket = async (b: InvestableBasket) => {
    const amt = parseFloat(basketAmt);
    if (!amt || amt <= 0) { setOrderMsg({ ok: false, text: 'Enter an amount to invest.' }); return; }
    try {
      const res = await apiService.investBasket(b.id, amt);
      const picks: BasketInvestPick[] = (res.data as { picks?: BasketInvestPick[] })?.picks ?? [];
      const legs: TradeLeg[] = picks.filter(p => p.trade && p.trade.quantity > 0).map(p => ({
        symbol: p.symbol, transactionType: 'BUY', quantity: p.trade!.quantity,
        orderType: p.trade!.orderType, price: p.trade!.limitPrice, exchange: p.trade!.exchange,
        product: p.trade!.product, estValue: p.trade!.estValue,
      }));
      if (!legs.length) { setOrderMsg({ ok: false, text: 'Amount too small to buy any whole shares in this basket.' }); return; }
      setOrderMsg(null);
      setPendingOrder({ kind: 'basket', legs, label: `Invest ₹${amt.toLocaleString('en-IN')} in ${b.name}` });
    } catch {
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

  const askInvestAll = (picks: InvestPick[]) => {
    const legs: TradeLeg[] = picks.filter(p => p.order && p.order.quantity > 0).map(p => ({
      symbol: p.symbol, transactionType: 'BUY', quantity: p.order.quantity,
      orderType: p.order.orderType, price: p.order.limitPrice, exchange: p.order.exchange,
      product: p.order.product, estValue: p.order.estValue,
    }));
    if (!legs.length) return;
    setOrderMsg(null);
    setPendingOrder({ kind: 'basket', legs, label: `Invest across ${legs.length} stocks` });
  };

  const askSwap = (a: RebalanceActionLike, sig: AiSignalLike | undefined) => {
    const o = a.alternative?.order;
    if (!a.trade || !o) return;
    const alt = a.alternative!;
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
      if (alertRes.data) setAlerts(alertRes.data as Alert[]);
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
            {TABS.map(t => (
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
            <HoldingsTab portfolio={portfolio} alerts={alerts} sortKey={sortKey} sortDir={sortDir} sortedStocks={sortedStocks} onSort={handleSort} />
          )}

          {/* ── Performance Tab ──────────────────────────────────────────────────── */}
          {activeTab === 'performance' && performance && <PerformanceTab performance={performance} />}

          {/* ── Risk Tab ─────────────────────────────────────────────────────────── */}
          {activeTab === 'risk' && <RiskTab portfolio={portfolio} riskMetrics={riskMetrics} riskLab={riskLab} />}

          {/* ── AI Optimize Tab ──────────────────────────────────────────────────── */}
          {activeTab === 'optimize' && (
            <OptimizeTab optimization={optimization} optimizing={optimizing} orders={orders} cancellingId={cancellingId}
              runOptimization={runOptimization} loadOrders={loadOrders} cancelPendingOrder={cancelPendingOrder}
              askOrder={askOrder} askSwap={askSwap} setOrderMsg={setOrderMsg} />
          )}

          {/* ── AI Invest Tab — sub-nav: Quick Invest / AI Funds / AI Themes ─────── */}
          {activeTab === 'invest' && (
            <InvestTab
              investSub={investSub} setInvestSub={setInvestSub}
              investAmount={investAmount} setInvestAmount={setInvestAmount} investData={investData} investLoading={investLoading}
              generateInvestPlan={generateInvestPlan} askInvestAll={askInvestAll} askOrder={askOrder}
              basketAmt={basketAmt} setBasketAmt={setBasketAmt} askInvestBasket={askInvestBasket}
              themes={themes} themesLoading={themesLoading} openTheme={openTheme} themeAnalytics={themeAnalytics}
              themeRebal={themeRebal} themeBusy={themeBusy} toggleTheme={toggleTheme} loadRebalance={loadRebalance}
              setThemeBusy={setThemeBusy} setOrderMsg={setOrderMsg}
              baskets={baskets} openBasket={openBasket} setOpenBasket={setOpenBasket}
            />
          )}

          {/* ── Sector Exposure ── */}
          {activeTab === 'sectors' && <SectorsTab sectorData={sectorData} />}

          {/* ── AI Advisor ── */}
          {activeTab === 'advisor' && <AdvisorTab bench={bench} advisor={advisor} />}


          {/* ── Portfolio Health Score ── */}
          {activeTab === 'health' && <HealthTab health={health} />}

          {/* ── Goal Planner ── */}
          {activeTab === 'planner' && (
            <PlannerTab plan={plan} planForm={planForm} setPlanForm={setPlanForm} planning={planning} runPlan={runPlan} setQuizOpen={setQuizOpen} />
          )}

          {/* ── Tax Harvest ── */}
          {activeTab === 'tax' && <TaxTab tax={tax} />}
        </>
      )}

      {quizOpen && (
        <RiskQuizModal quizAns={quizAns} setQuizAns={setQuizAns} planForm={planForm} setPlanForm={setPlanForm} onClose={() => setQuizOpen(false)} />
      )}

      <OrderModal orderMsg={orderMsg} setOrderMsg={setOrderMsg} pendingOrder={pendingOrder} setPendingOrder={setPendingOrder} placing={placing} confirmOrder={confirmOrder} />
    </div>
  );
};

export default PortfolioPage;
