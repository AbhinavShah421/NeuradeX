import { create } from 'zustand';
import { Stock, Prediction, Portfolio } from '../types';

interface AppState {
  // Stocks
  stocks: Stock[];
  selectedStock: Stock | null;
  loadingStocks: boolean;

  // Predictions
  predictions: { [key: string]: Prediction };
  loadingPredictions: boolean;

  // Portfolio
  portfolio: Portfolio | null;
  loadingPortfolio: boolean;

  // UI
  theme: 'light' | 'dark';
  // Command palette is Ctrl/Cmd+K only by default — no on-screen trigger, so it
  // was unreachable on a touchscreen. This flag lets a visible button (e.g. a
  // header search icon on mobile) open it without CommandPalette owning global nav state.
  commandPaletteOpen: boolean;

  // Actions
  setStocks: (stocks: Stock[]) => void;
  setSelectedStock: (stock: Stock | null) => void;
  setLoadingStocks: (loading: boolean) => void;

  setPredictions: (predictions: { [key: string]: Prediction }) => void;
  addPrediction: (symbol: string, prediction: Prediction) => void;
  setLoadingPredictions: (loading: boolean) => void;

  setPortfolio: (portfolio: Portfolio | null) => void;
  setLoadingPortfolio: (loading: boolean) => void;

  setTheme: (theme: 'light' | 'dark') => void;
  setCommandPaletteOpen: (open: boolean) => void;
}

const THEME_KEY = 'neuradex-theme';

const getInitialTheme = (): 'light' | 'dark' => {
  try {
    const t = localStorage.getItem(THEME_KEY);
    if (t === 'dark' || t === 'light') return t;
  } catch { /* localStorage unavailable */ }
  return 'light';
};

export const useAppStore = create<AppState>((set) => ({
  // Initial state
  stocks: [],
  selectedStock: null,
  loadingStocks: false,

  predictions: {},
  loadingPredictions: false,

  portfolio: null,
  loadingPortfolio: false,

  theme: getInitialTheme(),
  commandPaletteOpen: false,

  // Actions
  setStocks: (stocks) => set({ stocks }),
  setSelectedStock: (stock) => set({ selectedStock: stock }),
  setLoadingStocks: (loading) => set({ loadingStocks: loading }),

  setPredictions: (predictions) => set({ predictions }),
  addPrediction: (symbol, prediction) =>
    set((state) => ({
      predictions: {
        ...state.predictions,
        [symbol]: prediction,
      } as { [key: string]: Prediction },
    })),
  setLoadingPredictions: (loading) => set({ loadingPredictions: loading }),

  setPortfolio: (portfolio) => set({ portfolio }),
  setLoadingPortfolio: (loading) => set({ loadingPortfolio: loading }),

  setTheme: (theme) => {
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
    set({ theme });
  },
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
}));
