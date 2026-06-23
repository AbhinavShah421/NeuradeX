import React from 'react';
import { useScanStore } from '../stores/scanStore';

const ScanControl: React.FC<{ align?: 'left' | 'right' }> = ({ align = 'right' }) => {
  const {
    scanning, scanned, universe, lastScan, runningSessions,
    autoScanEnabled, togglingAutoScan,
    rescan, toggleAutoScan,
  } = useScanStore();
  const pct = universe ? Math.round((scanned / universe) * 100) : 0;
  const blocked = scanning || runningSessions > 0;

  const lastScanLabel = lastScan
    ? new Date(lastScan).toLocaleString('en-IN', {
        day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true,
      })
    : null;

  const rescanTooltip = scanning
    ? 'A scan is already running'
    : runningSessions > 0
      ? `Cannot scan while ${runningSessions} session${runningSessions > 1 ? 's are' : ' is'} running`
      : 'Run a fresh full-market AI scan';

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginLeft: align === 'right' ? 'auto' : undefined }}>
      <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
        AI scan {scanned}/{universe}
        {scanning
          ? <span style={{ color: 'var(--nd-accent)', fontWeight: 600 }}> · scanning… {pct}%</span>
          : runningSessions > 0
            ? <span style={{ color: '#f59e0b', fontWeight: 600 }}> · {runningSessions} session{runningSessions > 1 ? 's' : ''} running</span>
            : lastScanLabel
              ? <span> · {lastScanLabel}</span>
              : null}
      </span>

      {/* Auto-scan toggle */}
      <button
        onClick={toggleAutoScan}
        disabled={togglingAutoScan}
        title={autoScanEnabled ? 'Auto-scan is ON — click to pause background sweeps' : 'Auto-scan is OFF — click to resume background sweeps'}
        style={{
          display: 'flex', alignItems: 'center', gap: 5, padding: '5px 10px',
          borderRadius: 8, border: '1px solid var(--nd-border)',
          background: autoScanEnabled ? 'rgba(16,185,129,0.12)' : 'var(--nd-surface)',
          color: autoScanEnabled ? 'var(--nd-green)' : 'var(--nd-text-3)',
          cursor: togglingAutoScan ? 'not-allowed' : 'pointer',
          opacity: togglingAutoScan ? 0.6 : 1,
          fontSize: 11, fontWeight: 600, transition: 'all 0.15s',
        }}>
        <span className="material-icons" style={{ fontSize: 13 }}>
          {autoScanEnabled ? 'autorenew' : 'pause_circle'}
        </span>
        Auto
      </button>

      {/* Manual rescan button */}
      <button
        onClick={rescan}
        disabled={blocked}
        title={rescanTooltip}
        style={{
          display: 'flex', alignItems: 'center', gap: 5, padding: '6px 14px', borderRadius: 8,
          border: '1px solid var(--nd-border)', background: 'var(--nd-surface)',
          color: blocked ? 'var(--nd-text-3)' : 'var(--nd-text-2)',
          cursor: blocked ? 'not-allowed' : 'pointer', opacity: blocked ? 0.6 : 1,
          fontSize: 12, fontWeight: 600,
        }}>
        <span className={`material-icons${scanning ? ' nd-spin' : ''}`} style={{ fontSize: 15 }}>
          {scanning ? 'autorenew' : runningSessions > 0 ? 'block' : 'refresh'}
        </span>
        {scanning ? 'Scanning…' : runningSessions > 0 ? 'Session active' : 'Rescan'}
      </button>
    </div>
  );
};

export default ScanControl;
