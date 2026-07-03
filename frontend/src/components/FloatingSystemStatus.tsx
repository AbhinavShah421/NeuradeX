import React, { useCallback, useEffect, useRef, useState } from 'react';
import apiService from '../services/api';

// One Docker container as reported by /api/system/services.
interface DockerSvc {
  name:    string;        // container name, e.g. stock-prediction-backend
  label:   string;        // friendly label
  icon:    string;        // material icon
  state:   string;        // running | exited | restarting | paused | created
  status:  string;        // "Up 7 minutes (healthy)"
  health:  string | null; // healthy | unhealthy | starting | null
  running: boolean;
  // NB: the axios interceptor camelCases all response keys (cpu_pct → cpuPct).
  cpuPct:    number | null;
  memUsedMb: number | null;
  logSeverity: 'error' | 'warning' | 'ok';  // from log_severity
}

interface DockerTotals { running: number; count: number; cpuPct: number; memUsedMb: number; }

const fmtMem = (mb: number | null) =>
  mb == null ? '—' : mb >= 1024 ? `${(mb / 1024).toFixed(1)}G` : `${Math.round(mb)}M`;

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
  const [svcs,   setSvcs]   = useState<DockerSvc[]>([]);
  const [totals, setTotals] = useState<DockerTotals | null>(null);
  const [svcErr, setSvcErr] = useState<string | null>(null);
  const [busy,   setBusy]   = useState<string | null>(null);   // container name being actioned
  const [restartingAll, setRestartingAll] = useState(false);
  const [dragging, setDragging] = useState(false);

  // Paper trading time config
  const [noEntryAfter,  setNoEntryAfter]  = useState('14:00');
  const [squareoffAfter, setSquareoffAfter] = useState('14:30');
  const [configSaving, setConfigSaving]   = useState(false);
  const [configMsg,    setConfigMsg]      = useState('');

  const dragOrigin    = useRef<{ mx: number; my: number; px: number; py: number } | null>(null);
  const moved         = useRef(false);
  const touchHandled  = useRef(false);  // prevents synthetic click firing after touchend

  // ── Poll Docker services ──────────────────────────────────────────────────
  const refresh = useCallback(async (fresh = false) => {
    try {
      const r: any = await apiService.getDockerServices(fresh);
      setSvcs(r.data ?? []);
      setTotals(r.totals ?? null);
      setSvcErr(null);
    } catch (e: any) {
      setSvcErr(e?.response?.data?.detail ?? e?.message ?? 'Docker unavailable');
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  const control = async (name: string, action: 'start' | 'stop' | 'restart') => {
    setBusy(name);
    try {
      await apiService.controlDockerService(name, action);
      // give Docker a beat to flip state, then refresh (bypass cache)
      setTimeout(() => refresh(true), 1200);
    } catch (e: any) {
      setSvcErr(`${action} ${name}: ${e?.response?.data?.detail ?? e?.message ?? 'failed'}`);
    } finally {
      setTimeout(() => setBusy(null), 1200);
    }
  };

  const openLogs = (name: string) => {
    window.open(apiService.dockerLogsUrl(name), '_blank', 'noopener,noreferrer');
  };

  const restartAll = async () => {
    if (!window.confirm('Restart all running services? (backend is skipped — it can’t restart itself)')) return;
    setRestartingAll(true);
    setSvcErr(null);
    try {
      await apiService.restartAllDockerServices();
      setTimeout(() => refresh(true), 2000);
    } catch (e: any) {
      setSvcErr(`restart all: ${e?.response?.data?.detail ?? e?.message ?? 'failed'}`);
    } finally {
      setTimeout(() => setRestartingAll(false), 2000);
    }
  };

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
  const ok     = svcs.filter(s => s.running).length;
  const total  = svcs.length;
  const allOk  = total > 0 && ok === total;
  const dotColor = svcErr ? '#ef4444' : allOk ? '#00b386' : '#f59e0b';

  // Sort: not-running first (needs attention), then by label.
  const sortedSvcs = [...svcs].sort((a, b) => {
    if (a.running !== b.running) return a.running ? 1 : -1;
    return a.label.localeCompare(b.label);
  });

  // Panel position — above if space, else below; right-aligned to button.
  // panelW clamps to the viewport: at a fixed 362px, a 320-360px phone (the
  // clamp on panelLeft can only push it to x=8, not shrink it) would still
  // render ~40px of the panel off the right edge of the screen.
  const panelW   = Math.min(362, window.innerWidth - 16);  // +25% over the original 290px
  const panelH   = 380; // fixed height; services list scrolls inside
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
          // Below the mobile full-screen nav (500) and chart-fullscreen mode
          // (9999) so this diagnostics widget can't float on top of either —
          // still above ordinary page chrome (header dropdowns top out at 300).
          zIndex: 450,
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
            width: panelW, zIndex: 449,
            background: 'var(--nd-bg)',
            border: '1px solid var(--nd-border)',
            borderRadius: 12,
            boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
            overflow: 'hidden',
            display: 'flex', flexDirection: 'column',
          }}
        >
          {/* Header: count + restart-all + manual refresh */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 12px', borderBottom: '1px solid var(--nd-border)',
          }}>
            <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)' }}>dns</span>
            <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-1)' }}>Docker Services</span>
            <span style={{ fontSize: 10, fontWeight: 600, color: dotColor, marginLeft: 2 }}>{ok}/{total}</span>
            <button
              onClick={e => { e.stopPropagation(); restartAll(); }}
              disabled={restartingAll}
              title="Restart all (skips backend)"
              style={{
                marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4,
                background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.4)',
                borderRadius: 6, padding: '3px 8px', cursor: restartingAll ? 'wait' : 'pointer',
                color: '#f59e0b', fontSize: 10, fontWeight: 700,
              }}
            >
              <span className="material-icons" style={{ fontSize: 12, animation: restartingAll ? 'nd-spin 0.9s linear infinite' : 'none' }}>
                {restartingAll ? 'autorenew' : 'restart_alt'}
              </span>
              {restartingAll ? 'Restarting…' : 'Restart all'}
            </button>
            <button
              onClick={e => { e.stopPropagation(); refresh(true); }}
              title="Refresh"
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--nd-text-3)', padding: 2, display: 'flex',
              }}
            >
              <span className="material-icons" style={{ fontSize: 15 }}>refresh</span>
            </button>
          </div>

          {/* Totals strip */}
          {totals && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 12,
              padding: '6px 12px', borderBottom: '1px solid var(--nd-border)',
              background: 'var(--nd-surface)', fontSize: 10.5,
            }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: 'var(--nd-text-2)' }}>
                <span className="material-icons" style={{ fontSize: 12, color: '#0ea5e9' }}>memory</span>
                CPU <strong style={{ color: 'var(--nd-text-1)' }}>{totals.cpuPct}%</strong>
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: 'var(--nd-text-2)' }}>
                <span className="material-icons" style={{ fontSize: 12, color: '#a855f7' }}>sd_card</span>
                Mem <strong style={{ color: 'var(--nd-text-1)' }}>{fmtMem(totals.memUsedMb)}</strong>
              </span>
              <span style={{ marginLeft: 'auto', color: 'var(--nd-text-3)' }}>{totals.running} running</span>
            </div>
          )}

          {svcErr && (
            <div style={{
              padding: '8px 12px', fontSize: 10.5, lineHeight: 1.5,
              color: '#ef4444', background: 'rgba(239,68,68,0.08)',
              borderBottom: '1px solid var(--nd-border)',
            }}>
              {svcErr}
            </div>
          )}

          {/* Scrollable services list */}
          <div style={{ overflowY: 'auto', maxHeight: 248 }}>
          {sortedSvcs.length === 0 && !svcErr && (
            <div style={{ padding: '14px 12px', fontSize: 11, color: 'var(--nd-text-3)', textAlign: 'center' }}>
              Loading containers…
            </div>
          )}
          {sortedSvcs.map((svc, i) => {
            const dotCol = svc.running
              ? (svc.health === 'unhealthy' ? '#f59e0b' : '#00b386')
              : '#ef4444';
            const isBusy = busy === svc.name;
            return (
            <div key={svc.name} style={{
              display: 'flex', alignItems: 'center', gap: 7,
              padding: '6px 10px 6px 12px',
              borderBottom: i < sortedSvcs.length - 1 ? '1px solid var(--nd-border)' : 'none',
              opacity: isBusy ? 0.6 : 1,
            }}>
              <div title={svc.status} style={{
                width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                background: dotCol,
                boxShadow: svc.running ? `0 0 5px ${dotCol}aa` : 'none',
              }} />
              <span className="material-icons" style={{ fontSize: 12, color: 'var(--nd-text-3)', flexShrink: 0 }}>
                {svc.icon}
              </span>
              <span style={{
                flex: 1, minWidth: 0, fontSize: 11, color: 'var(--nd-text-1)', fontWeight: 500,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }} title={svc.label}>{svc.label}</span>

              {/* CPU / Mem */}
              {svc.running && (
                <span style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'flex-end',
                  flexShrink: 0, lineHeight: 1.25, minWidth: 46,
                }} title={`CPU ${svc.cpuPct ?? '—'}% · Mem ${fmtMem(svc.memUsedMb)}`}>
                  <span style={{
                    fontSize: 9.5, fontWeight: 600,
                    color: (svc.cpuPct ?? 0) >= 80 ? '#ef4444' : (svc.cpuPct ?? 0) >= 40 ? '#f59e0b' : 'var(--nd-text-2)',
                  }}>{svc.cpuPct == null ? '—' : `${svc.cpuPct}%`}</span>
                  <span style={{ fontSize: 9.5, color: 'var(--nd-text-3)' }}>{fmtMem(svc.memUsedMb)}</span>
                </span>
              )}

              {/* Logs link → new tab. Color flags recent-log severity. */}
              {(() => {
                const sevColor = svc.logSeverity === 'error' ? '#ef4444'
                               : svc.logSeverity === 'warning' ? '#f59e0b'
                               : '#00b386';
                const sevTitle = svc.logSeverity === 'error' ? 'Errors in recent logs — view (new tab)'
                               : svc.logSeverity === 'warning' ? 'Warnings in recent logs — view (new tab)'
                               : 'No errors/warnings — view logs (new tab)';
                return (
                  <button
                    onClick={e => { e.stopPropagation(); openLogs(svc.name); }}
                    title={sevTitle}
                    style={{
                      background: 'none', border: 'none', cursor: 'pointer',
                      color: sevColor, padding: 2, display: 'flex', flexShrink: 0,
                    }}
                  >
                    <span className="material-icons" style={{ fontSize: 14 }}>article</span>
                  </button>
                );
              })()}

              {/* Control: start (if down) or restart/stop (if up) */}
              {svc.running ? (
                <>
                  <button
                    onClick={e => { e.stopPropagation(); control(svc.name, 'restart'); }}
                    disabled={isBusy}
                    title="Restart"
                    style={{ background: 'none', border: 'none', cursor: isBusy ? 'wait' : 'pointer', color: 'var(--nd-text-3)', padding: 2, display: 'flex', flexShrink: 0 }}
                  >
                    <span className="material-icons" style={{ fontSize: 14 }}>{isBusy ? 'hourglass_empty' : 'restart_alt'}</span>
                  </button>
                  <button
                    onClick={e => { e.stopPropagation(); control(svc.name, 'stop'); }}
                    disabled={isBusy}
                    title="Stop"
                    style={{ background: 'none', border: 'none', cursor: isBusy ? 'wait' : 'pointer', color: '#ef4444', padding: 2, display: 'flex', flexShrink: 0 }}
                  >
                    <span className="material-icons" style={{ fontSize: 14 }}>stop_circle</span>
                  </button>
                </>
              ) : (
                <button
                  onClick={e => { e.stopPropagation(); control(svc.name, 'start'); }}
                  disabled={isBusy}
                  title="Start"
                  style={{ background: 'none', border: 'none', cursor: isBusy ? 'wait' : 'pointer', color: '#00b386', padding: 2, display: 'flex', flexShrink: 0 }}
                >
                  <span className="material-icons" style={{ fontSize: 15 }}>{isBusy ? 'hourglass_empty' : 'play_circle'}</span>
                </button>
              )}
            </div>
            );
          })}
          </div>

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
