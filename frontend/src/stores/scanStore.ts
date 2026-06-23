import { create } from 'zustand';
import apiService from '../services/api';

// Centralized AI-scan state shared across Dashboard, Predictions and Portfolio.
// The scanner is the single source of truth; this store mirrors its status so a
// rescan started on any page disables rescan everywhere until the sweep finishes.
// Scans are also blocked while any trading session is running (replay or paper).
interface ScanState {
  scanning: boolean;
  scanned: number;
  universe: number;
  candidates: number;
  lastScan: string | null;
  marketRegime: string | null;
  triggering: boolean;
  runningSessions: number;
  autoScanEnabled: boolean;
  togglingAutoScan: boolean;
  fetchStatus: () => Promise<void>;
  rescan: () => Promise<void>;
  toggleAutoScan: () => Promise<void>;
}

export const useScanStore = create<ScanState>((set, get) => ({
  scanning: false,
  scanned: 0,
  universe: 0,
  candidates: 0,
  lastScan: null,
  marketRegime: null,
  triggering: false,
  runningSessions: 0,
  autoScanEnabled: true,
  togglingAutoScan: false,

  fetchStatus: async () => {
    try {
      const [scanRes, sessRes] = await Promise.allSettled([
        apiService.getScanStatus(),
        apiService.sessionList('running'),
      ]);
      if (scanRes.status === 'fulfilled') {
        const d: any = scanRes.value.data || {};
        set({
          scanning: !!d.scanning,
          scanned: d.scanned ?? 0,
          universe: d.universe ?? 0,
          candidates: d.candidates ?? 0,
          lastScan: d.lastScan ?? null,
          marketRegime: d.marketRegime ?? null,
          autoScanEnabled: d.autoScanEnabled !== false,   // default true if absent
        });
      }
      if (sessRes.status === 'fulfilled') {
        const sessions: any[] = (sessRes.value as any).data ?? [];
        set({ runningSessions: sessions.length });
      }
    } catch { /* keep last known */ }
  },

  rescan: async () => {
    if (get().scanning || get().triggering) return;
    if (get().runningSessions > 0) return;   // block scan while sessions are live
    set({ triggering: true, scanning: true });
    try { await apiService.scanWatchlist(); } catch { /* ignore */ }
    finally {
      set({ triggering: false });
      setTimeout(() => get().fetchStatus(), 2000);
    }
  },

  toggleAutoScan: async () => {
    if (get().togglingAutoScan) return;
    const next = !get().autoScanEnabled;
    set({ togglingAutoScan: true, autoScanEnabled: next });   // optimistic update
    try {
      await apiService.setAutoScan(next);
    } catch {
      set({ autoScanEnabled: !next });   // revert on failure
    } finally {
      set({ togglingAutoScan: false });
    }
  },
}));
