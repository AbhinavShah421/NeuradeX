import React, { useEffect, useState } from 'react';
import apiService from '../services/api';
import AutopilotBanner from '../components/dashboard/AutopilotBanner';
import TradeGateCard from '../components/dashboard/TradeGateCard';
import DeliveryAutopilotCard from '../components/dashboard/DeliveryAutopilotCard';
import ScanAccuracyCard from '../components/dashboard/ScanAccuracyCard';
import MetricModal from '../components/dashboard/MetricModal';
import LearningCurveCard from '../components/dashboard/LearningCurveCard';
import DirectoryTab from '../components/dashboard/DirectoryTab';
import PatternModelCard from '../components/dashboard/PatternModelCard';
import AiWatchlistTab from '../components/dashboard/AiWatchlistTab';
import SystemStartupModal from '../components/dashboard/SystemStartupModal';
import PerformanceRegimeStrip from '../components/dashboard/PerformanceRegimeStrip';
import LiveSessionsPanel from '../components/dashboard/LiveSessionsPanel';

// ── Dashboard Page ────────────────────────────────────────────────────────────

const Dashboard: React.FC = () => {
  const [accuracyStats, setAccuracyStats] = useState<any>(null);
  const [selectedCard, setSelectedCard] = useState<string | null>(null);
  const [showStartup, setShowStartup] = useState(false);
  // Bottom stocks section: AI Watchlist (default) + All Stocks, merged into one
  // tabbed card at the bottom of the page instead of two separate blocks.
  const [stocksTab, setStocksTab] = useState<'watchlist' | 'directory'>('watchlist');

  useEffect(() => {
    apiService.getAccuracyStats().then(r => { if (r.data) setAccuracyStats(r.data); }).catch(() => {});

    // Show startup modal once per browser session, only if at least one service isn't up yet.
    if (sessionStorage.getItem('neuradex-startup-done')) return;
    Promise.allSettled([
      apiService.healthCheck(),
      apiService.getServicesHealth(),
    ]).then(results => {
      const backendOk = results[0].status === 'fulfilled';
      const svcsOk = results[1].status === 'fulfilled' &&
        ((results[1].value as any).data ?? []).every((s: any) => s.status === 'ok');
      if (!backendOk || !svcsOk) setShowStartup(true);
      else sessionStorage.setItem('neuradex-startup-done', '1');
    }).catch(() => setShowStartup(true));
  }, []);

  // Net expectancy leads, not win rate. Win rate is a dial on the exit geometry
  // — tighten the target and it rises while the strategy gets worse — so it is
  // shown against the breakeven rate its own payoff ratio demands, which is the
  // number that says whether accuracy or geometry is the problem.
  const exp = accuracyStats?.expectancy;
  const ok = (v?: number) => ((v ?? 0) > 0 ? 'var(--nd-green)' : 'var(--nd-red)');

  const STAT_CARDS = accuracyStats ? [
    { id: 'expectancy', label: 'Net Expectancy / Trade',
      value: exp ? `${exp.netExpectancyPct >= 0 ? '+' : ''}${exp.netExpectancyPct.toFixed(3)}%` : '—',
      sub: exp ? `after ${exp.costPct.toFixed(3)}% costs` : undefined,
      icon: 'savings', color: ok(exp?.netExpectancyPct), bg: 'var(--nd-green-50)' },
    { id: 'win', label: 'Win Rate',
      value: `${(accuracyStats.winRate * 100).toFixed(1)}%`,
      sub: exp ? `needs ${(exp.breakevenWinRate * 100).toFixed(1)}% to break even` : undefined,
      icon: 'emoji_events',
      color: exp && accuracyStats.winRate >= exp.breakevenWinRate ? 'var(--nd-green)' : 'var(--nd-red)',
      bg: 'var(--nd-green-50)' },
    { id: 'payoff', label: 'Payoff Ratio',
      value: exp ? `${exp.payoffRatio.toFixed(2)}:1` : '—',
      sub: exp ? `+${exp.avgWinPct.toFixed(2)}% / −${exp.avgLossPct.toFixed(2)}%` : undefined,
      icon: 'balance', color: 'var(--nd-blue)', bg: '#e3f2fd' },
    { id: 'sharpe', label: 'Daily Sharpe',
      value: exp?.dailySharpe != null ? exp.dailySharpe.toFixed(2) : '—',
      sub: 'per day, not per trade',
      icon: 'analytics', color: ok(exp?.dailySharpe), bg: '#f5f3ff' },
  ] : [];

  return (
    <div>
      {showStartup && (
        <SystemStartupModal onClose={() => {
          setShowStartup(false);
          sessionStorage.setItem('neuradex-startup-done', '1');
        }} />
      )}

      {/* Page heading */}
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">Market Overview</h1>
        <p className="nd-page-sub">Real-time NSE · BSE stock data with AI-generated predictions</p>
      </div>

      {/* Live performance + current market regime */}
      <PerformanceRegimeStrip />

      {/* Currently-running auto-trading sessions with open positions + P&L */}
      <LiveSessionsPanel />

      {/* Accuracy stat cards — click any to see the evidence */}
      {STAT_CARDS.length > 0 && (
        <div className="nd-grid-4" style={{ gap: 12, marginBottom: 20 }}>
          {STAT_CARDS.map(s => (
            <div key={s.label} className="nd-card" onClick={() => setSelectedCard(s.id)}
              style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px', cursor: 'pointer', transition: 'box-shadow 0.15s' }}
              onMouseEnter={e => (e.currentTarget.style.boxShadow = 'var(--nd-shadow-md)')}
              onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}>
              <span className="material-icons" title="See how this is calculated"
                style={{ position: 'absolute', top: 8, right: 8, fontSize: 16, color: 'var(--nd-text-3)' }}>info</span>
              <div className="nd-icon-chip" style={{ background: s.bg }}>
                <span className="material-icons" style={{ color: s.color }}>{s.icon}</span>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p className="nd-label">{s.label}</p>
                <p style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{s.value}</p>
                {s.sub && (
                  <p style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 1,
                              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {s.sub}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedCard && accuracyStats && (
        <MetricModal cardId={selectedCard} stats={accuracyStats} onClose={() => setSelectedCard(null)} />
      )}

      {/* Self-running autopilot + the system's learning curve */}
      <AutopilotBanner />
      <TradeGateCard />
      <DeliveryAutopilotCard />

      {/* Two-up: the system's learning (curve + pattern model) | AI scan accuracy */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 420px), 1fr))', gap: 20, marginBottom: 20, alignItems: 'stretch', width: '100%' }}>
        {/* Unified "system learning" card: the equity/win-rate curve on top, the
            dedicated pattern-recognition model below the divider. */}
        <div className="nd-card" style={{ padding: 0, position: 'relative', display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
          <LearningCurveCard embedded />
          <div style={{ height: 1, background: 'var(--nd-border)', margin: '12px 18px 0' }} />
          <PatternModelCard embedded />
        </div>
        <ScanAccuracyCard />
      </div>

      {/* Stocks — AI Watchlist (scanner picks with grades/signals/auto-trade) and
          the full All Stocks directory, merged into one tabbed card. Watchlist
          is the default/first tab — it's the higher-signal view most people want;
          the full directory is there for lookup/search. */}
      <div className="nd-card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px 0' }}>
          <div className="nd-pill-tabs" style={{ marginBottom: 12 }}>
            <button onClick={() => setStocksTab('watchlist')} className="nd-pill-tab"
              style={{ background: stocksTab === 'watchlist' ? 'var(--nd-green)' : 'transparent', color: stocksTab === 'watchlist' ? '#fff' : 'var(--nd-text-2)' }}>
              <span className="material-icons" style={{ fontSize: 15 }}>auto_awesome</span>
              AI Watchlist
            </button>
            <button onClick={() => setStocksTab('directory')} className="nd-pill-tab"
              style={{ background: stocksTab === 'directory' ? 'var(--nd-green)' : 'transparent', color: stocksTab === 'directory' ? '#fff' : 'var(--nd-text-2)' }}>
              <span className="material-icons" style={{ fontSize: 15 }}>format_list_bulleted</span>
              All Stocks
            </button>
          </div>
        </div>
        <div style={{ padding: '0 20px 20px' }}>
          {stocksTab === 'watchlist' ? <AiWatchlistTab /> : <DirectoryTab />}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
