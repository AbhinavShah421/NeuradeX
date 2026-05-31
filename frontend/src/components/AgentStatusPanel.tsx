import React, { useEffect, useState } from 'react';
import apiService from '../services/api';

interface ServiceStatus {
  name: string;
  port: number;
  status: 'ok' | 'error' | 'loading';
  checkedAt?: string;
}

const LOADING_SERVICES: ServiceStatus[] = [
  { name: 'Market Data',      port: 8001, status: 'loading' },
  { name: 'Technical Agent',  port: 8002, status: 'loading' },
  { name: 'Sentiment Agent',  port: 8003, status: 'loading' },
  { name: 'Macro Agent',      port: 8004, status: 'loading' },
  { name: 'Pattern Agent',    port: 8005, status: 'loading' },
  { name: 'RL Agent',         port: 8006, status: 'loading' },
  { name: 'Ensemble Engine',  port: 8007, status: 'loading' },
  { name: 'Feedback Service', port: 8012, status: 'loading' },
  { name: 'Model Trainer',    port: 8013, status: 'loading' },
];

const AgentStatusPanel: React.FC = () => {
  const [statuses, setStatuses] = useState<ServiceStatus[]>(LOADING_SERVICES);
  const [collapsed, setCollapsed] = useState(true);  // collapsed by default — saves space

  const checkAll = async () => {
    try {
      const res = await apiService.getServicesHealth();
      const data: ServiceStatus[] = res?.data ?? [];
      setStatuses(data.length ? data : LOADING_SERVICES.map(s => ({ ...s, status: 'error' })));
    } catch {
      setStatuses(LOADING_SERVICES.map(s => ({ ...s, status: 'error' })));
    }
  };

  useEffect(() => {
    checkAll();
    const interval = setInterval(checkAll, 30_000);
    return () => clearInterval(interval);
  }, []);

  const okCount = statuses.filter(s => s.status === 'ok').length;
  const allOk   = okCount === statuses.length;

  return (
    <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: collapsed ? '14px 20px' : 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: collapsed ? 0 : 16 }}>
        {/* Clickable title area toggles the panel */}
        <button
          onClick={() => setCollapsed(c => !c)}
          style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'none', border: 'none', cursor: 'pointer', padding: 0, font: 'inherit', color: 'inherit', minWidth: 0, flex: 1 }}
        >
          <span className="material-icons" style={{ fontSize: 20, color: 'var(--nd-text-3)', flexShrink: 0, transition: 'transform 0.2s', transform: collapsed ? 'rotate(-90deg)' : 'none' }}>
            expand_more
          </span>
          <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: allOk ? 'var(--nd-green)' : 'var(--nd-red)', boxShadow: allOk ? '0 0 6px var(--nd-green)' : 'none' }} />
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            Microservice Status
          </h3>
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <span style={{ fontSize: 12, color: 'var(--nd-text-3)', whiteSpace: 'nowrap' }}>{okCount}/{statuses.length}</span>
          {!collapsed && (
            <button
              onClick={checkAll}
              style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 6, padding: '3px 10px', fontSize: 12, cursor: 'pointer', color: 'var(--nd-text-2)', whiteSpace: 'nowrap' }}
            >
              Refresh
            </button>
          )}
        </div>
      </div>

      {!collapsed && (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10 }}>
        {statuses.map(svc => (
          <div
            key={svc.port}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background: 'var(--nd-bg)',
              border: '1px solid var(--nd-border)',
              borderRadius: 8,
              padding: '8px 12px',
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                flexShrink: 0,
                background:
                  svc.status === 'ok'      ? 'var(--nd-green)' :
                  svc.status === 'error'   ? 'var(--nd-red)'   : '#888',
                boxShadow: svc.status === 'ok' ? '0 0 6px var(--nd-green)' : 'none',
                animation: svc.status === 'loading' ? 'nd-pulse 1.2s infinite' : 'none',
              }}
            />
            <div>
              <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--nd-text-1)' }}>{svc.name}</div>
              <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>:{svc.port}</div>
            </div>
          </div>
        ))}
      </div>
      )}
    </div>
  );
};

export default AgentStatusPanel;
