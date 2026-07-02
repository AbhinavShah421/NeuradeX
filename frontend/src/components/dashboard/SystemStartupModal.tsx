import React, { useEffect, useState, useCallback, useRef } from 'react';
import apiService from '../../services/api';

// ── System Startup Modal ──────────────────────────────────────────────────────

type SvcStatus = 'checking' | 'ok' | 'error';
interface SvcState { name: string; icon: string; status: SvcStatus; }

const MICROSERVICE_NAMES = [
  'Market Data', 'Technical Agent', 'Sentiment Agent', 'Macro Agent',
  'Pattern Agent', 'RL Agent', 'Ensemble Engine', 'Feedback Service', 'Model Trainer',
];

const INITIAL_SVCS: SvcState[] = [
  { name: 'Backend',          icon: 'dns',             status: 'checking' },
  { name: 'Market Data',      icon: 'candlestick_chart', status: 'checking' },
  { name: 'Technical Agent',  icon: 'show_chart',      status: 'checking' },
  { name: 'Sentiment Agent',  icon: 'article',         status: 'checking' },
  { name: 'Macro Agent',      icon: 'public',          status: 'checking' },
  { name: 'Pattern Agent',    icon: 'pattern',         status: 'checking' },
  { name: 'RL Agent',         icon: 'smart_toy',       status: 'checking' },
  { name: 'Ensemble Engine',  icon: 'hub',             status: 'checking' },
  { name: 'Feedback Service', icon: 'feedback',        status: 'checking' },
  { name: 'Model Trainer',    icon: 'model_training',  status: 'checking' },
  { name: 'LLM',              icon: 'psychology',      status: 'checking' },
];

// Shared poll logic extracted so both modal and status icon can reuse it
async function pollServices(): Promise<SvcState[]> {
  const next: SvcState[] = INITIAL_SVCS.map(s => ({ ...s, status: 'checking' as SvcStatus }));
  const set = (name: string, status: SvcStatus) => {
    const s = next.find(x => x.name === name);
    if (s) s.status = status;
  };
  await Promise.allSettled([
    apiService.healthCheck()
      .then(() => set('Backend', 'ok'))
      .catch(() => set('Backend', 'error')),
    apiService.getServicesHealth()
      .then(r => {
        const list: any[] = (r as any).data ?? r ?? [];
        for (const svc of list)
          if (MICROSERVICE_NAMES.includes(svc.name))
            set(svc.name, svc.status === 'ok' ? 'ok' : 'error');
        MICROSERVICE_NAMES.forEach(n => {
          const s = next.find(x => x.name === n);
          if (s && s.status === 'checking') s.status = 'error';
        });
      })
      .catch(() => MICROSERVICE_NAMES.forEach(n => set(n, 'error'))),
    apiService.getLlmStatus()
      .then(r => { const d = (r as any).data ?? r; set('LLM', d?.available ? 'ok' : 'error'); })
      .catch(() => set('LLM', 'error')),
  ]);
  return next;
}

const SystemStartupModal: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [svcs, setSvcs] = useState<SvcState[]>(INITIAL_SVCS.map(s => ({ ...s })));
  const [allLive, setAllLive] = useState(false);
  const doneRef = useRef(false);

  const poll = useCallback(async () => {
    if (doneRef.current) return;
    const next = await pollServices();
    setSvcs([...next]);
    if (next.filter(s => s.name !== 'LLM').every(s => s.status === 'ok') && !doneRef.current) {
      doneRef.current = true;
      setAllLive(true);
      setTimeout(onClose, 1600);
    }
  }, [onClose]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 15_000);
    return () => clearInterval(id);
  }, [poll]);

  const okCount = svcs.filter(s => s.status === 'ok').length;
  const pct     = (okCount / svcs.length) * 100;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 2000,
      background: 'rgba(2,6,23,0.82)',
      backdropFilter: 'blur(14px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
    }}>
      {/* Animated gradient border wrapper */}
      <div className={`nd-startup-border${allLive ? ' nd-live' : ''}`}>
        {/* Dark inner panel */}
        <div style={{
          background: 'linear-gradient(160deg, #080d1a 0%, #0b1120 55%, #060a14 100%)',
          borderRadius: 19, overflow: 'hidden', position: 'relative',
        }}>

          {/* Subtle grid background */}
          <div style={{
            position: 'absolute', inset: 0, pointerEvents: 'none',
            backgroundImage: `
              linear-gradient(rgba(124,58,237,0.045) 1px, transparent 1px),
              linear-gradient(90deg, rgba(124,58,237,0.045) 1px, transparent 1px)
            `,
            backgroundSize: '36px 36px',
          }} />

          {/* ── Header ── */}
          <div style={{ padding: '28px 28px 22px', position: 'relative', zIndex: 1 }}>

            <div style={{ display: 'flex', alignItems: 'center', gap: 20, marginBottom: 22 }}>

              {/* Neural pulse icon */}
              <div style={{ position: 'relative', width: 58, height: 58, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {[0, 1].map(i => (
                  <div key={i} style={{
                    position: 'absolute', inset: 0, borderRadius: '50%',
                    border: `1px solid rgba(${i === 0 ? '124,58,237' : '6,182,212'},0.5)`,
                    animation: `nd-pulse-ring 2.6s ease-out infinite ${i * 1.3}s`,
                  }} />
                ))}
                <div style={{
                  position: 'absolute', inset: 5, borderRadius: '50%',
                  background: 'linear-gradient(135deg, rgba(124,58,237,0.18), rgba(6,182,212,0.14))',
                  border: '1px solid rgba(124,58,237,0.35)',
                  boxShadow: '0 0 24px rgba(124,58,237,0.28), inset 0 0 14px rgba(124,58,237,0.1)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  animation: 'nd-float 4s ease-in-out infinite',
                }}>
                  <span className="material-icons" style={{
                    fontSize: 22,
                    background: allLive ? '#00b386' : 'linear-gradient(135deg,#a78bfa,#67e8f9)',
                    WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
                    backgroundClip: 'text',
                  }}>{allLive ? 'verified' : 'hub'}</span>
                </div>
              </div>

              {/* Title block */}
              <div>
                <div style={{ fontSize: 10.5, letterSpacing: 3.5, color: 'rgba(167,139,250,0.65)', marginBottom: 5, fontWeight: 700 }}>
                  NEURADEX AI
                </div>
                <div style={{
                  fontSize: 19, fontWeight: 800, letterSpacing: 0.4, lineHeight: 1.15,
                  background: allLive ? '#00b386' : 'linear-gradient(120deg,#e2d9f3 0%,#a5f3fc 100%)',
                  WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text',
                }}>
                  {allLive ? 'All Systems Live' : 'Initializing Systems'}
                </div>
                <div style={{ fontSize: 11.5, color: 'rgba(148,163,184,0.6)', marginTop: 4, letterSpacing: 0.4 }}>
                  {allLive
                    ? 'NeuradeX is ready — closing automatically'
                    : `${okCount} of ${svcs.length} services operational`}
                </div>
              </div>
            </div>

            {/* Progress bar */}
            <div style={{ height: 3, borderRadius: 2, background: 'rgba(255,255,255,0.07)', overflow: 'hidden' }}>
              <div style={{
                height: '100%', borderRadius: 2,
                width: `${pct}%`,
                background: allLive
                  ? '#00b386'
                  : 'linear-gradient(90deg, #7c3aed 0%, #06b6d4 50%, #a78bfa 100%)',
                backgroundSize: '200% 100%',
                animation: !allLive ? 'nd-shimmer-bar 2s linear infinite' : 'none',
                transition: 'width 0.5s cubic-bezier(0.4,0,0.2,1)',
                boxShadow: allLive ? '0 0 10px rgba(0,179,134,0.7)' : '0 0 8px rgba(124,58,237,0.55)',
              }} />
            </div>
          </div>

          {/* Gradient divider */}
          <div style={{ height: 1, background: 'linear-gradient(90deg,transparent,rgba(124,58,237,0.35),rgba(6,182,212,0.35),transparent)' }} />

          {/* ── Service rows ── */}
          <div style={{ maxHeight: '50vh', overflow: 'auto', padding: '6px 0' }}>
            {svcs.map((svc, i) => (
              <div key={svc.name} style={{
                display: 'flex', alignItems: 'center', gap: 14,
                padding: '9px 28px',
                borderBottom: i < svcs.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                animation: 'nd-row-slide 0.35s ease both',
                animationDelay: `${i * 35}ms`,
                transition: 'background 0.15s',
                cursor: 'default',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >
                {/* Status dot with ping ring */}
                <div style={{ position: 'relative', width: 20, height: 20, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  {svc.status === 'ok' && (
                    <div style={{
                      position: 'absolute', inset: 0, borderRadius: '50%',
                      background: 'rgba(0,179,134,0.25)',
                      animation: 'nd-dot-ping 2s ease-out infinite',
                    }} />
                  )}
                  <div style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: svc.status === 'ok' ? '#00b386'
                      : svc.status === 'error' ? '#f59e0b'
                      : 'rgba(148,163,184,0.35)',
                    boxShadow: svc.status === 'ok' ? '0 0 8px rgba(0,179,134,0.9)'
                      : svc.status === 'error' ? '0 0 7px rgba(245,158,11,0.7)'
                      : 'none',
                    animation: svc.status === 'checking' ? 'nd-dot-blink 1.4s ease-in-out infinite' : 'none',
                    transition: 'all 0.3s ease',
                  }} />
                </div>

                {/* Service icon */}
                <span className="material-icons" style={{
                  fontSize: 14,
                  color: svc.status === 'ok' ? 'rgba(0,179,134,0.65)'
                    : svc.status === 'error' ? 'rgba(245,158,11,0.65)'
                    : 'rgba(148,163,184,0.3)',
                  transition: 'color 0.3s',
                }}>{svc.icon}</span>

                {/* Name */}
                <span style={{
                  flex: 1, fontSize: 13, fontWeight: 500, letterSpacing: 0.2,
                  color: svc.status === 'ok' ? 'rgba(226,232,240,0.9)'
                    : svc.status === 'error' ? 'rgba(226,232,240,0.6)'
                    : 'rgba(148,163,184,0.45)',
                  transition: 'color 0.3s',
                }}>{svc.name}</span>

                {/* Status badge */}
                <span style={{
                  fontSize: 9.5, fontWeight: 700, letterSpacing: 1.4,
                  padding: '3px 8px', borderRadius: 4,
                  background: svc.status === 'ok' ? 'rgba(0,179,134,0.13)'
                    : svc.status === 'error' ? 'rgba(245,158,11,0.1)'
                    : 'rgba(148,163,184,0.07)',
                  color: svc.status === 'ok' ? '#00b386'
                    : svc.status === 'error' ? '#f59e0b'
                    : 'rgba(148,163,184,0.45)',
                  border: `1px solid ${svc.status === 'ok' ? 'rgba(0,179,134,0.28)'
                    : svc.status === 'error' ? 'rgba(245,158,11,0.22)'
                    : 'rgba(148,163,184,0.1)'}`,
                  transition: 'all 0.3s ease',
                }}>
                  {svc.status === 'ok' ? 'LIVE' : svc.status === 'error' ? 'WAITING' : 'INIT'}
                </span>
              </div>
            ))}
          </div>

          {/* Footer */}
          {!allLive && (
            <div style={{
              padding: '11px 28px', position: 'relative', zIndex: 1,
              borderTop: '1px solid rgba(255,255,255,0.04)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <span style={{ fontSize: 10.5, color: 'rgba(148,163,184,0.35)', letterSpacing: 0.5 }}>
                Closes automatically when all systems are online
              </span>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <button onClick={onClose} style={{
                  fontSize: 10.5, fontWeight: 600, letterSpacing: 0.5,
                  padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
                  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
                  color: 'rgba(148,163,184,0.7)',
                }}>Skip</button>
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  {[0, 1, 2].map(i => (
                    <div key={i} style={{
                      width: 4, height: 4, borderRadius: '50%',
                      background: 'rgba(124,58,237,0.55)',
                      animation: `nd-dot-blink 1.4s ease-in-out infinite ${i * 0.22}s`,
                    }} />
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default SystemStartupModal;
