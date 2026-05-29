import React, { useEffect, useState } from 'react';

interface ServiceStatus {
  name: string;
  port: number;
  status: 'ok' | 'error' | 'loading';
  lastCheck?: string;
}

const SERVICES: { name: string; port: number }[] = [
  { name: 'Market Data', port: 8001 },
  { name: 'Technical Agent', port: 8002 },
  { name: 'Sentiment Agent', port: 8003 },
  { name: 'Macro Agent', port: 8004 },
  { name: 'Pattern Agent', port: 8005 },
  { name: 'RL Agent', port: 8006 },
  { name: 'Ensemble Engine', port: 8007 },
  { name: 'Feedback Service', port: 8012 },
  { name: 'Model Trainer', port: 8013 },
];

const AgentStatusPanel: React.FC = () => {
  const [statuses, setStatuses] = useState<ServiceStatus[]>(
    SERVICES.map(s => ({ ...s, status: 'loading' }))
  );

  const checkAll = async () => {
    const results = await Promise.all(
      SERVICES.map(async svc => {
        try {
          const res = await fetch(`http://localhost:${svc.port}/health`, { signal: AbortSignal.timeout(3000) });
          return { ...svc, status: res.ok ? 'ok' : 'error', lastCheck: new Date().toLocaleTimeString() } as ServiceStatus;
        } catch {
          return { ...svc, status: 'error', lastCheck: new Date().toLocaleTimeString() } as ServiceStatus;
        }
      })
    );
    setStatuses(results);
  };

  useEffect(() => {
    checkAll();
    const interval = setInterval(checkAll, 30_000);
    return () => clearInterval(interval);
  }, []);

  const okCount = statuses.filter(s => s.status === 'ok').length;

  return (
    <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: 'var(--nd-text-1)' }}>
          Microservice Status
        </h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{okCount}/{SERVICES.length} online</span>
          <button
            onClick={checkAll}
            style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 6, padding: '3px 10px', fontSize: 12, cursor: 'pointer', color: 'var(--nd-text-2)' }}
          >
            Refresh
          </button>
        </div>
      </div>

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
                  svc.status === 'ok' ? 'var(--nd-green)' :
                  svc.status === 'error' ? 'var(--nd-red)' : '#888',
                boxShadow: svc.status === 'ok' ? '0 0 6px var(--nd-green)' : 'none',
              }}
            />
            <div>
              <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--nd-text-1)' }}>{svc.name}</div>
              <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>:{svc.port}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default AgentStatusPanel;
