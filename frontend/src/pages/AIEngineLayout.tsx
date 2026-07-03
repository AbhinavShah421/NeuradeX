import React from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import AgentStatusPanel from '../components/AgentStatusPanel';

const SUB_NAV = [
  {
    to: '/ai-engine',
    exact: true,
    label: 'Live Analysis',
    icon: 'psychology',
    description: 'Run 5-agent ensemble on any stock',
  },
  {
    to: '/ai-engine/agents',
    exact: false,
    label: 'AI Agents',
    icon: 'smart_toy',
    description: 'Agent health, weights & performance',
  },
  {
    to: '/ai-engine/backtest',
    exact: false,
    label: 'Backtesting',
    icon: 'history_edu',
    description: 'Simulate strategies on historical data',
  },
  {
    to: '/ai-engine/recordings',
    exact: false,
    label: 'Recordings',
    icon: 'radio_button_checked',
    description: 'Capture a full trading day from the Groww stream into the dataset',
  },
  {
    to: '/ai-engine/paper-trading',
    exact: false,
    label: 'Paper Trading',
    icon: 'receipt_long',
    description: 'Practice with real prices, no real money',
  },
  {
    to: '/ai-engine/memory',
    exact: false,
    label: 'Agents & Memory',
    icon: 'memory',
    description: 'Agent controls, weights, GBM training & pattern memory bank',
  },
  {
    to: '/ai-engine/live-trading',
    exact: false,
    label: 'Live Trading',
    icon: 'bolt',
    description: 'Real Groww MIS orders — conviction-gated by full AI ensemble',
  },
];

const AIEngineLayout: React.FC = () => {
  const location = useLocation();

  const isActive = (to: string, exact: boolean) =>
    exact ? location.pathname === to : location.pathname === to || location.pathname.startsWith(to + '/');

  const activeItem = SUB_NAV.find(n => isActive(n.to, n.exact)) ?? SUB_NAV[0];

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto' }}>

      {/* Sub-nav header */}
      <div style={{ paddingTop: 20, paddingBottom: 0, marginBottom: 24 }}>

        {/* Breadcrumb-style header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
          <span className="material-icons" style={{ fontSize: 20, color: 'var(--nd-green)', flexShrink: 0 }}>auto_awesome</span>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--nd-text-1)', flexShrink: 0 }}>AI Engine</h1>
          <span style={{ color: 'var(--nd-text-3)', fontSize: 14, flexShrink: 0 }}>›</span>
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--nd-text-2)', flexShrink: 0 }}>{activeItem.label}</span>
          <span style={{ fontSize: 11, color: 'var(--nd-text-3)', marginLeft: 2 }}>— {activeItem.description}</span>
        </div>

        {/* Tab strip — scrollable, never wraps or overflows */}
        <div style={{
          display: 'flex',
          gap: 2,
          background: 'var(--nd-surface)',
          border: '1px solid var(--nd-border)',
          borderRadius: 12,
          padding: 4,
          overflowX: 'auto',
          scrollbarWidth: 'none',
          WebkitOverflowScrolling: 'touch' as any,
        }}>
          {SUB_NAV.map(item => {
            const active = isActive(item.to, item.exact);
            return (
              <Link
                key={item.to}
                to={item.to}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '8px 14px',
                  borderRadius: 9,
                  textDecoration: 'none',
                  fontSize: 13,
                  fontWeight: 500,
                  transition: 'all 0.15s',
                  background: active ? 'var(--nd-green)' : 'transparent',
                  color: active ? '#fff' : 'var(--nd-text-2)',
                  boxShadow: active ? '0 2px 8px var(--nd-green)40' : 'none',
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                }}
              >
                <span className="material-icons" style={{ fontSize: 15 }}>{item.icon}</span>
                {item.label}
              </Link>
            );
          })}
        </div>

        {/* Training pipeline indicator */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginTop: 10,
          fontSize: 11,
          color: 'var(--nd-text-3)',
          overflowX: 'auto',
          scrollbarWidth: 'none',
          whiteSpace: 'nowrap',
        }}>
          <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-green)', flexShrink: 0 }}>fiber_manual_record</span>
          All modes (Paper · Backtest · Live) feed into the same training pipeline →
          <span style={{ color: 'var(--nd-accent)', fontWeight: 500, flexShrink: 0 }}>model-trainer</span>
          <span style={{ flexShrink: 0 }}>→</span>
          <span style={{ color: 'var(--nd-accent)', fontWeight: 500, flexShrink: 0 }}>MLflow</span>
        </div>
      </div>

      {/* Page content from nested route */}
      <Outlet />

      {/* Agent status strip at the bottom of every AI Engine page */}
      <div style={{ marginTop: 32, marginBottom: 24 }}>
        <AgentStatusPanel />
      </div>
    </div>
  );
};

export default AIEngineLayout;
