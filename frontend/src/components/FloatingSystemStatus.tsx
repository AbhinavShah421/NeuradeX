import React, { useCallback, useEffect, useRef, useState } from 'react';
import apiService from '../services/api';

type SvcStatus = 'checking' | 'ok' | 'error';
interface SvcState { name: string; icon: string; status: SvcStatus; }

const MICROSERVICE_NAMES = [
  'Market Data', 'Technical Agent', 'Sentiment Agent', 'Macro Agent',
  'Pattern Agent', 'RL Agent', 'Ensemble Engine', 'Feedback Service', 'Model Trainer',
];

const INITIAL_SVCS: SvcState[] = [
  { name: 'Backend',          icon: 'dns',              status: 'checking' },
  { name: 'Market Data',      icon: 'candlestick_chart', status: 'checking' },
  { name: 'Technical Agent',  icon: 'show_chart',       status: 'checking' },
  { name: 'Sentiment Agent',  icon: 'article',          status: 'checking' },
  { name: 'Macro Agent',      icon: 'public',           status: 'checking' },
  { name: 'Pattern Agent',    icon: 'pattern',          status: 'checking' },
  { name: 'RL Agent',         icon: 'smart_toy',        status: 'checking' },
  { name: 'Ensemble Engine',  icon: 'hub',              status: 'checking' },
  { name: 'Feedback Service', icon: 'feedback',         status: 'checking' },
  { name: 'Model Trainer',    icon: 'model_training',   status: 'checking' },
  { name: 'LLM',              icon: 'psychology',       status: 'checking' },
];

async function pollServices(): Promise<SvcState[]> {
  const next = INITIAL_SVCS.map(s => ({ ...s, status: 'checking' as SvcStatus }));
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

const POS_KEY  = 'nd-sys-status-pos';
const BTN_SIZE = 48;

function clampPos(x: number, y: number) {
  return {
    x: Math.max(8, Math.min(window.innerWidth  - BTN_SIZE - 8, x)),
    y: Math.max(8, Math.min(window.innerHeight - BTN_SIZE - 8, y)),
  };
}

function loadPos() {
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (raw) return clampPos(...(Object.values(JSON.parse(raw)) as [number, number]));
  } catch {}
  return { x: window.innerWidth - BTN_SIZE - 16, y: window.innerHeight - BTN_SIZE - 80 };
}

const FloatingSystemStatus: React.FC = () => {
  const [pos,    setPos]    = useState(loadPos);
  const [open,   setOpen]   = useState(false);
  const [svcs,   setSvcs]   = useState<SvcState[]>(INITIAL_SVCS.map(s => ({ ...s })));
  const [dragging, setDragging] = useState(false);

  // Paper trading time config
  const [noEntryAfter,  setNoEntryAfter]  = useState('14:00');
  const [squareoffAfter, setSquareoffAfter] = useState('14:30');
  const [configSaving, setConfigSaving]   = useState(false);
  const [configMsg,    setConfigMsg]      = useState('');

  const dragOrigin    = useRef<{ mx: number; my: number; px: number; py: number } | null>(null);
  const moved         = useRef(false);
  const touchHandled  = useRef(false);  // prevents synthetic click firing after touchend

  // ── Poll services ───────────────────────────────────────────────────────────
  const refresh = useCallback(async () => {
    const next = await pollServices();
    setSvcs(next);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 8_000);
    return () => clearInterval(t);
  }, [refresh]);

  // ── Load paper config on mount ───────────────────────────────────────────
  useEffect(() => {
    apiService.getPaperConfig()
      .then(r => {
        const d = r?.data ?? {};
        if (d.noEntryAfter)  setNoEntryAfter(d.noEntryAfter);
        if (d.squareoffAfter) setSquareoffAfter(d.squareoffAfter);
      })
      .catch(() => {});
  }, []);

  const saveConfig = async () => {
    setConfigSaving(true);
    setConfigMsg('');
    try {
      await apiService.setPaperConfig(noEntryAfter, squareoffAfter);
      setConfigMsg('Saved');
    } catch {
      setConfigMsg('Error');
    } finally {
      setConfigSaving(false);
      setTimeout(() => setConfigMsg(''), 2000);
    }
  };

  // ── Save position ───────────────────────────────────────────────────────────
  useEffect(() => {
    localStorage.setItem(POS_KEY, JSON.stringify(pos));
  }, [pos]);

  // ── Mouse drag ──────────────────────────────────────────────────────────────
  const onMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    moved.current = false;
    dragOrigin.current = { mx: e.clientX, my: e.clientY, px: pos.x, py: pos.y };
    setDragging(true);
  };

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      if (!dragOrigin.current) return;
      const dx = e.clientX - dragOrigin.current.mx;
      const dy = e.clientY - dragOrigin.current.my;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved.current = true;
      setPos(clampPos(dragOrigin.current.px + dx, dragOrigin.current.py + dy));
    };
    const onUp = () => { dragOrigin.current = null; setDragging(false); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup',  onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, [dragging]);

  // ── Touch drag ──────────────────────────────────────────────────────────────
  const onTouchStart = (e: React.TouchEvent) => {
    const t = e.touches[0];
    moved.current = false;
    dragOrigin.current = { mx: t.clientX, my: t.clientY, px: pos.x, py: pos.y };
  };
  const onTouchMove = (e: React.TouchEvent) => {
    if (!dragOrigin.current) return;
    e.preventDefault();
    const t = e.touches[0];
    const dx = t.clientX - dragOrigin.current.mx;
    const dy = t.clientY - dragOrigin.current.my;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved.current = true;
    setPos(clampPos(dragOrigin.current.px + dx, dragOrigin.current.py + dy));
  };
  const onTouchEnd = () => {
    if (!moved.current) {
      touchHandled.current = true;  // flag so the follow-up synthetic click is ignored
      setOpen(o => !o);
    }
    dragOrigin.current = null;
  };

  const handleClick = () => {
    if (touchHandled.current) { touchHandled.current = false; return; }
    if (!moved.current) setOpen(o => !o);
  };

  // ── Derived state ───────────────────────────────────────────────────────────
  const ok     = svcs.filter(s => s.status === 'ok').length;
  const total  = svcs.length;
  const allOk  = ok === total;
  const dotColor = allOk ? '#00b386' : '#f59e0b';

  // Panel position — above if space, else below; right-aligned to button
  const panelW   = 230;
  const panelH   = total * 40 + 52;
  const panelTop = pos.y - panelH - 8 >= 8 ? pos.y - panelH - 8 : pos.y + BTN_SIZE + 8;
  const panelLeft = Math.max(8, Math.min(window.innerWidth - panelW - 8, pos.x - panelW + BTN_SIZE));

  return (
    <>
      {/* ── Floating button ── */}
      <div
        onMouseDown={onMouseDown}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onClick={handleClick}
        title={allOk ? 'All systems live' : `${ok}/${total} live`}
        style={{
          position: 'fixed', left: pos.x, top: pos.y,
          width: BTN_SIZE, height: BTN_SIZE,
          zIndex: 10000,
          cursor: dragging ? 'grabbing' : 'grab',
          userSelect: 'none', touchAction: 'none',
        }}
      >
        {/* Pulse ring when degraded */}
        {!allOk && (
          <div style={{
            position: 'absolute', inset: -5, borderRadius: '50%',
            border: `2px solid ${dotColor}`,
            animation: 'nd-float-ring 1.8s ease-out infinite',
            pointerEvents: 'none',
          }} />
        )}

        {/* Button face */}
        <div style={{
          width: '100%', height: '100%', borderRadius: '50%',
          background: 'var(--nd-surface)',
          border: `2px solid ${dotColor}`,
          boxShadow: `0 4px 16px rgba(0,0,0,0.3), 0 0 10px ${dotColor}55`,
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', gap: 1,
        }}>
          {/* Inner pulsing dot */}
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {!allOk && (
              <div style={{
                position: 'absolute', width: 10, height: 10, borderRadius: '50%',
                background: dotColor, opacity: 0.35,
                animation: 'nd-status-ping 1.8s ease-out infinite',
              }} />
            )}
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: dotColor, boxShadow: `0 0 6px ${dotColor}` }} />
          </div>
          <span style={{ fontSize: 9, fontWeight: 700, color: dotColor, lineHeight: 1 }}>
            {ok}/{total}
          </span>
        </div>
      </div>

      {/* ── Expanded panel — same style as dashboard dropdown ── */}
      {open && !dragging && (
        <div
          onClick={e => e.stopPropagation()}
          style={{
            position: 'fixed', left: panelLeft, top: panelTop,
            width: panelW, zIndex: 9999,
            background: 'var(--nd-bg)',
            border: '1px solid var(--nd-border)',
            borderRadius: 12,
            boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
            overflow: 'hidden',
          }}
        >
          {svcs.map((svc, i) => (
            <div key={svc.name} style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '8px 14px',
              borderBottom: i < svcs.length - 1 ? '1px solid var(--nd-border)' : 'none',
            }}>
              <div style={{
                width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                background: svc.status === 'ok' ? '#00b386' : svc.status === 'error' ? '#f59e0b' : 'var(--nd-text-3)',
                boxShadow: svc.status === 'ok' ? '0 0 5px rgba(0,179,134,0.7)' : 'none',
              }} />
              <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-text-3)', flexShrink: 0 }}>
                {svc.icon}
              </span>
              <span style={{ flex: 1, fontSize: 12, color: 'var(--nd-text-1)', fontWeight: 500 }}>{svc.name}</span>
              <span style={{
                fontSize: 10.5, fontWeight: 600,
                color: svc.status === 'ok' ? '#00b386' : svc.status === 'error' ? '#f59e0b' : 'var(--nd-text-3)',
              }}>
                {svc.status === 'ok' ? 'Live' : svc.status === 'error' ? 'Waiting' : '…'}
              </span>
            </div>
          ))}

          {/* Paper trading time config */}
          <div style={{ borderTop: '1px solid var(--nd-border)', padding: '10px 14px 12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
              <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-text-3)' }}>schedule</span>
              <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-2)' }}>Paper Trading Windows</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)', width: 90, flexShrink: 0 }}>No entry after</span>
                <input
                  type="time" value={noEntryAfter}
                  onChange={e => setNoEntryAfter(e.target.value)}
                  style={{
                    flex: 1, fontSize: 11, padding: '3px 6px', borderRadius: 5,
                    border: '1px solid var(--nd-border)', background: 'var(--nd-bg)',
                    color: 'var(--nd-text-1)', outline: 'none',
                  }}
                />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)', width: 90, flexShrink: 0 }}>Square off after</span>
                <input
                  type="time" value={squareoffAfter}
                  onChange={e => setSquareoffAfter(e.target.value)}
                  style={{
                    flex: 1, fontSize: 11, padding: '3px 6px', borderRadius: 5,
                    border: '1px solid var(--nd-border)', background: 'var(--nd-bg)',
                    color: 'var(--nd-text-1)', outline: 'none',
                  }}
                />
              </div>
              <button
                onClick={e => { e.stopPropagation(); saveConfig(); }}
                disabled={configSaving}
                style={{
                  marginTop: 2, padding: '5px 0', borderRadius: 6, fontSize: 11, fontWeight: 600,
                  border: 'none', cursor: configSaving ? 'wait' : 'pointer',
                  background: configMsg === 'Saved' ? '#00b386' : configMsg === 'Error' ? '#f59e0b' : 'var(--nd-green)',
                  color: '#fff', transition: 'background 0.2s',
                }}
              >
                {configMsg || (configSaving ? 'Saving…' : 'Save')}
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        @keyframes nd-float-ring {
          0%   { transform: scale(1);   opacity: 0.8; }
          70%  { transform: scale(1.5); opacity: 0;   }
          100% { transform: scale(1.5); opacity: 0;   }
        }
      `}</style>
    </>
  );
};

export default FloatingSystemStatus;
