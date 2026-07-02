import React from 'react';
import { PortfolioStock } from '../../types';

export type SortKey = keyof Pick<PortfolioStock, 'symbol' | 'quantity' | 'purchasePrice' | 'currentPrice' | 'value' | 'gain' | 'gainPercent'>;
export type SortDir = 'asc' | 'desc';
export type Tab = 'holdings' | 'performance' | 'risk' | 'optimize' | 'invest' | 'sectors' | 'health' | 'planner' | 'tax' | 'advisor';

export const inr = (v: number) =>
  v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const pct = (v: any) => (v === null || v === undefined) ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
export const pctColor = (v: any) => (v === null || v === undefined) ? 'var(--nd-text-3)' : v >= 0 ? 'var(--nd-green)' : 'var(--nd-red)';

export const COLUMNS: { key: SortKey; label: string; align: 'left' | 'right' }[] = [
  { key: 'symbol',        label: 'Company',       align: 'left'  },
  { key: 'quantity',      label: 'Qty',           align: 'right' },
  { key: 'purchasePrice', label: 'Avg Price',     align: 'right' },
  { key: 'currentPrice',  label: 'Market Price',  align: 'right' },
  { key: 'value',         label: 'Current Value', align: 'right' },
  { key: 'gain',          label: 'P&L',           align: 'right' },
  { key: 'gainPercent',   label: 'Returns (%)',   align: 'right' },
];

// Herfindahl-Hirschman Index — 0 (perfectly diversified) to 1 (all in one stock)
export function calcHHI(stocks: PortfolioStock[], totalValue: number): number {
  return stocks.reduce((sum, s) => {
    const w = s.value / totalValue;
    return sum + w * w;
  }, 0);
}

// Approximate 1-day 95% VaR using normal distribution assumption (1.645σ)
// σ derived from gainPercent as a rough daily volatility proxy
export function calcVaR(stocks: PortfolioStock[], totalValue: number): number {
  const dailyVol = stocks.reduce((sum, s) => {
    const weight = s.value / totalValue;
    const vol = Math.abs(s.gainPercent / 100) * 0.1; // rough proxy
    return sum + weight * vol;
  }, 0);
  return totalValue * dailyVol * 1.645;
}

export function RiskMeter({ score, label }: { score: 'LOW' | 'MEDIUM' | 'HIGH'; label: string }) {
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
export const RISK_QUIZ: { q: string; opts: [string, number][] }[] = [
  { q: 'Your age band', opts: [['Under 35', 3], ['35–50', 2], ['Over 50', 1]] },
  { q: 'When do you need this money?', opts: [['10+ years', 3], ['3–10 years', 2], ['Under 3 years', 1]] },
  { q: 'If your portfolio dropped 20% in a month, you would', opts: [['Buy more', 3], ['Hold', 2], ['Sell some', 1]] },
  { q: 'Investing experience', opts: [['Experienced', 3], ['Some', 2], ['New', 1]] },
  { q: 'Primary goal', opts: [['Grow wealth aggressively', 3], ['Steady growth', 2], ['Protect capital', 1]] },
];
export const riskFromScore = (pts: number) => pts >= 13 ? 'aggressive' : pts >= 9 ? 'moderate' : 'conservative';

export const ACTION_STYLE: Record<string, { bg: string; color: string }> = {
  EXIT: { bg: '#ef444420', color: '#ef4444' },
  TRIM: { bg: '#f59e0b20', color: '#f59e0b' },
  HOLD: { bg: 'var(--nd-surface-2)', color: 'var(--nd-text-2)' },
  ADD:  { bg: '#22c55e20', color: '#22c55e' },
};

// Orders in these Groww statuses can still be cancelled by the user.
export const CANCELLABLE = ['NEW', 'OPEN', 'PENDING', 'ACKED', 'ACKNOWLEDGED', 'APPROVED', 'TRIGGER_PENDING', 'MODIFIED'];

export const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: 'holdings',    label: 'Holdings',    icon: 'account_balance_wallet' },
  { id: 'performance', label: 'Performance', icon: 'trending_up' },
  { id: 'risk',        label: 'AI Risk Lab', icon: 'security' },
  { id: 'optimize',    label: 'AI Optimize', icon: 'auto_awesome' },
  { id: 'invest',      label: 'AI Invest',   icon: 'savings' },
  { id: 'advisor',     label: 'AI Advisor',  icon: 'support_agent' },
  { id: 'health',      label: 'Health Score', icon: 'health_and_safety' },
  { id: 'sectors',     label: 'Sector Exposure', icon: 'donut_large' },
  { id: 'planner',     label: 'Goal Planner', icon: 'savings' },
  { id: 'tax',         label: 'Tax Harvest', icon: 'receipt_long' },
];
