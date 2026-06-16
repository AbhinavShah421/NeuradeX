import { create } from 'zustand';
import apiService from '../services/api';

// Centralized AI-scan state shared across Dashboard, Predictions and Portfolio.
// The scanner is the single source of truth; this store mirrors its status so a
// rescan started on any page disables rescan everywhere until the sweep finishes.
interface ScanState {
  scanning: boolean;
  scanned: number;
  universe: number;
  candidates: number;
  lastScan: string | null;
  marketRegime: string | null;
  triggering: boolean;
  fetchStatus: () => Promise<void>;
  rescan: () => Promise<void>;
}

export const useScanStore = create<ScanState>((set, get) => ({
  scanning: false,
  scanned: 0,
  universe: 0,
  candidates: 0,
  lastScan: null,
  marketRegime: null,
  triggering: false,

  fetchStatus: async () => {
    try {
      const res = await apiService.getScanStatus();
      const d: any = res.data || {};
      set({
        scanning: !!d.scanning,
        scanned: d.scanned ?? 0,
        universe: d.universe ?? 0,
        candidates: d.candidates ?? 0,
        lastScan: d.lastScan ?? null,
        marketRegime: d.marketRegime ?? null,
      });
    } catch { /* keep last known */ }
  },

  rescan: async () => {
    if (get().scanning || get().triggering) return;   // hard-disable if a sweep is live
    set({ triggering: true, scanning: true });          // optimistic — reflected on every page
    try { await apiService.scanWatchlist(); } catch { /* ignore */ }
    finally {
      set({ triggering: false });
      setTimeout(() => get().fetchStatus(), 2000);
    }
  },
}));
