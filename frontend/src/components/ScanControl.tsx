import React from 'react';
import { useScanStore } from '../stores/scanStore';

// Shared AI-scan status + Rescan button. Identical state on every page: the
// Rescan button is disabled whenever a sweep is running (started from any page).
const ScanControl: React.FC<{ align?: 'left' | 'right' }> = ({ align = 'right' }) => {
  const { scanning, scanned, universe, lastScan, rescan } = useScanStore();
  const pct = universe ? Math.round((scanned / universe) * 100) : 0;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginLeft: align === 'right' ? 'auto' : undefined }}>
      <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
        AI scan {scanned}/{universe}
        {scanning
          ? <span style={{ color: 'var(--nd-accent)', fontWeight: 600 }}> · scanning… {pct}%</span>
          : (lastScan ? ` · ${new Date(lastScan).toLocaleTimeString('en-IN')}` : '')}
      </span>
      <button
        onClick={rescan}
        disabled={scanning}
        title={scanning ? 'A scan is already running' : 'Run a fresh full-market AI scan'}
        style={{
          display: 'flex', alignItems: 'center', gap: 5, padding: '6px 14px', borderRadius: 8,
          border: '1px solid var(--nd-border)', background: 'var(--nd-surface)',
          color: scanning ? 'var(--nd-text-3)' : 'var(--nd-text-2)',
          cursor: scanning ? 'not-allowed' : 'pointer', opacity: scanning ? 0.6 : 1,
          fontSize: 12, fontWeight: 600,
        }}>
        <span className={`material-icons${scanning ? ' nd-spin' : ''}`} style={{ fontSize: 15 }}>
          {scanning ? 'autorenew' : 'refresh'}
        </span>
        {scanning ? 'Scanning…' : 'Rescan'}
      </button>
    </div>
  );
};

export default ScanControl;
