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
    to: '/ai-engine/paper-trading',
    exact: false,
    label: 'Paper Trading',
    icon: 'receipt_long',
    description: 'Practice with real prices, no real money',
  },
];

const AIEngineLayout: React.FC = () => {
  const location = useLocation();

  const isActive = (to: string, exact: boolean) =>
    exact ? location.pathname === to : location.pathname === to || location.pathname.startsWith(to + '/');

  const activeItem = SUB_NAV.find(n => isActive(n.to, n.exact)) ?? SUB_NAV[0];

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '0 16px' }}>

      {/* Sub-nav header */}
      <div style={{ paddingTop: 20, paddingBottom: 0, marginBottom: 24 }}>

        {/* Breadcrumb-style header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <span className="material-icons" style={{ fontSize: 22, color: '#8b5cf6' }}>auto_awesome</span>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI Engine</h1>
          <span style={{ color: 'var(--nd-text-3)', fontSize: 16 }}>›</span>
          <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--nd-text-2)' }}>{activeItem.label}</span>
          <span style={{ fontSize: 12, color: 'var(--nd-text-3)', marginLeft: 4 }}>— {activeItem.description}</span>
        </div>

        {/* Tab strip */}
        <div style={{
          display: 'flex',
          gap: 2,
          background: 'var(--nd-surface)',
          border: '1px solid var(--nd-border)',
          borderRadius: 12,
          padding: 4,
          width: 'fit-content',
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
                  gap: 7,
                  padding: '8px 18px',
                  borderRadius: 9,
                  textDecoration: 'none',
                  fontSize: 13,
                  fontWeight: 500,
                  transition: 'all 0.15s',
                  background: active ? '#8b5cf6' : 'transparent',
                  color: active ? '#fff' : 'var(--nd-text-2)',
                  boxShadow: active ? '0 2px 8px #8b5cf640' : 'none',
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
          gap: 8,
          marginTop: 12,
          fontSize: 11,
          color: 'var(--nd-text-3)',
        }}>
          <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-green)' }}>fiber_manual_record</span>
          All modes (Live · Paper · Backtest) feed into the same training pipeline →
          <span style={{ color: 'var(--nd-accent)', fontWeight: 500 }}>model-trainer</span>
          <span style={{ color: 'var(--nd-text-3)' }}>→</span>
          <span style={{ color: 'var(--nd-accent)', fontWeight: 500 }}>MLflow</span>
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
