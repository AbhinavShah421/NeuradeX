/**
 * Dashboard — what the system DID, on the instrument-panel surface.
 *
 * Two things shape the layout:
 *
 *   • It is readings, not switches. Autopilot, the trade gate and the delivery
 *     autopilot moved to Trading Controls on 2026-09-09; a page you skim should
 *     not carry controls that change live trading when mis-clicked.
 *   • It is TABBED rather than stacked. Everything below the headline numbers
 *     used to run down one page, so answering "is it learning?" meant scrolling
 *     past the live sessions and answering "what is it watching?" meant
 *     scrolling past both. Three tabs, three questions.
 *
 * The four stat tiles sit ABOVE the tabs deliberately. They are the answer to
 * "is this thing making money", which stays relevant whichever tab you are on,
 * and they cost one compact row.
 */
import React, { useEffect, useState } from 'react';
import apiService from '../services/api';
import ScanAccuracyCard from '../components/dashboard/ScanAccuracyCard';
import MetricModal from '../components/dashboard/MetricModal';
import LearningCurveCard from '../components/dashboard/LearningCurveCard';
import DirectoryTab from '../components/dashboard/DirectoryTab';
import PatternModelCard from '../components/dashboard/PatternModelCard';
import AiWatchlistTab from '../components/dashboard/AiWatchlistTab';
import SystemStartupModal from '../components/dashboard/SystemStartupModal';
import PerformanceRegimeStrip from '../components/dashboard/PerformanceRegimeStrip';
import LiveSessionsPanel from '../components/dashboard/LiveSessionsPanel';

type Tab = 'live' | 'learning' | 'stocks';

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'live',     label: 'Live',     hint: 'Running sessions, open positions and the current market regime' },
  { id: 'learning', label: 'Learning', hint: 'The equity curve, the pattern model and scan accuracy' },
  { id: 'stocks',   label: 'Stocks',   hint: 'The AI watchlist and the full NSE directory' },
];

// ── Dashboard Page ────────────────────────────────────────────────────────────

const Dashboard: React.FC = () => {
  const [accuracyStats, setAccuracyStats] = useState<any>(null);
  const [selectedCard, setSelectedCard] = useState<string | null>(null);
  const [showStartup, setShowStartup] = useState(false);
  // Survives a reload, because the tab you were on is nearly always the tab you
  // want back after a refresh.
  const [tab, setTab] = useState<Tab>(
    () => (sessionStorage.getItem('neuradex-dash-tab') as Tab) || 'live');
  // Bottom stocks section: AI Watchlist (default) + All Stocks.
  const [stocksTab, setStocksTab] = useState<'watchlist' | 'directory'>('watchlist');

  const pick = (t: Tab) => {
    setTab(t);
    try { sessionStorage.setItem('neuradex-dash-tab', t); } catch { /* private mode */ }
  };

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
    { id: 'expectancy', label: 'Net expectancy / trade',
      value: exp ? `${exp.netExpectancyPct >= 0 ? '+' : ''}${exp.netExpectancyPct.toFixed(3)}%` : '—',
      sub: exp ? `after ${exp.costPct.toFixed(3)}% costs` : undefined,
      tone: ok(exp?.netExpectancyPct) },
    { id: 'win', label: 'Win rate',
      value: `${(accuracyStats.winRate * 100).toFixed(1)}%`,
      sub: exp ? `needs ${(exp.breakevenWinRate * 100).toFixed(1)}% to break even` : undefined,
      tone: exp && accuracyStats.winRate >= exp.breakevenWinRate ? 'var(--nd-green)' : 'var(--nd-red)' },
    { id: 'payoff', label: 'Payoff ratio',
      value: exp ? `${exp.payoffRatio.toFixed(2)}:1` : '—',
      sub: exp ? `+${exp.avgWinPct.toFixed(2)}% / −${exp.avgLossPct.toFixed(2)}%` : undefined,
      tone: 'var(--nd-blue)' },
    { id: 'sharpe', label: 'Daily Sharpe',
      value: exp?.dailySharpe != null ? exp.dailySharpe.toFixed(2) : '—',
      sub: 'per day, not per trade',
      tone: ok(exp?.dailySharpe) },
  ] : [];

  const activeHint = TABS.find(t => t.id === tab)?.hint;

  return (
    <div className="nd-console">
      {showStartup && (
        <SystemStartupModal onClose={() => {
          setShowStartup(false);
          sessionStorage.setItem('neuradex-startup-done', '1');
        }} />
      )}

      <div className="nd-console-head">
        <h1 className="nd-console-title">Market Overview</h1>
        <p className="nd-console-sub">Real-time NSE · BSE with AI-generated predictions</p>
      </div>

      {/* Headline numbers — above the tabs, because "is this making money" is
          the one question that stays relevant whichever tab you are reading. */}
      {STAT_CARDS.length > 0 && (
        <div className="nd-grid-4" style={{ gap: 12, marginBottom: 16 }}>
          {STAT_CARDS.map(s => (
            <div key={s.id} className="nd-stat" style={{ ['--tone' as any]: s.tone }}
                 onClick={() => setSelectedCard(s.id)}>
              <span className="material-icons nd-stat-info" title="See how this is calculated">info</span>
              <span className="nd-stat-label">{s.label}</span>
              <p className="nd-stat-value">{s.value}</p>
              {s.sub && <p className="nd-stat-sub">{s.sub}</p>}
            </div>
          ))}
        </div>
      )}

      {selectedCard && accuracyStats && (
        <MetricModal cardId={selectedCard} stats={accuracyStats} onClose={() => setSelectedCard(null)} />
      )}

      <div className="nd-tabs is-wide" role="tablist" aria-label="Dashboard section"
           style={{ marginBottom: 8 }}>
        {TABS.map(t => (
          <button key={t.id} role="tab" aria-selected={t.id === tab} onClick={() => pick(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      {/* The hint says what the tab holds, so choosing one does not need a
          click to find out. */}
      <p className="nd-console-sub" style={{ marginBottom: 16 }}>{activeHint}</p>

      {tab === 'live' && (
        <>
          <PerformanceRegimeStrip />
          <LiveSessionsPanel />
        </>
      )}

      {tab === 'learning' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 420px), 1fr))', gap: 20, alignItems: 'stretch', width: '100%' }}>
          {/* Unified "system learning" card: the equity/win-rate curve on top, the
              dedicated pattern-recognition model below the divider. */}
          <div className="nd-card nd-bank" style={{ padding: 0, position: 'relative', display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
            <LearningCurveCard embedded />
            <div style={{ height: 1, background: 'var(--nd-border)', margin: '12px 18px 0' }} />
            <PatternModelCard embedded />
          </div>
          <ScanAccuracyCard />
        </div>
      )}

      {tab === 'stocks' && (
        <div className="nd-card nd-bank" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '12px 16px 0' }}>
            <div className="nd-tabs" role="tablist" aria-label="Stock list"
                 style={{ marginBottom: 12 }}>
              <button role="tab" aria-selected={stocksTab === 'watchlist'}
                onClick={() => setStocksTab('watchlist')}>AI Watchlist</button>
              <button role="tab" aria-selected={stocksTab === 'directory'}
                onClick={() => setStocksTab('directory')}>All Stocks</button>
            </div>
          </div>
          <div style={{ padding: '0 20px 20px' }}>
            {stocksTab === 'watchlist' ? <AiWatchlistTab /> : <DirectoryTab />}
          </div>
        </div>
      )}
    </div>
  );
};

export default Dashboard;
