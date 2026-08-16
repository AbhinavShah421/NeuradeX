# NeuradeX Frontend

React 18 + TypeScript + Vite single-page app. In production it is a **static
Docker build served by nginx at `/neuradex`** — there is no hot reload against
the running stack; you rebuild the image to see changes.

## Stack

| Concern | Choice |
|---|---|
| Framework | React 18 + TypeScript, Vite build |
| Routing | react-router v6, `basename="/neuradex"` (see `App.tsx`) |
| State | Zustand (`src/stores/`: `appStore`, `authStore`, `scanStore`) |
| HTTP | axios via `src/services/api.ts` |
| Live updates | socket.io client (`src/services/socket.ts`) |
| Charts | lightweight-charts (trading), Chart.js, Recharts |
| Theme | Dark by default; most components are **inline-styled** |

## Layout

```
src/
  pages/        21 routed pages (Dashboard, Orders, AIEngine, PaperTrading,
                Recordings, PatternMemory, Backtest, RiskAnalytics, ...)
  components/   shared UI (Layout, TradingChart, SessionManager, ScanControl,
                MarketBoard, CommandPalette, dashboard/, pattern-memory/)
  stores/       Zustand stores
  services/     api.ts (axios + interceptors), socket.ts
  hooks/ types/ utils/ styles/
```

## Things that bite (read before debugging)

- **Every API response key is camelCased by an axios interceptor**
  (`api.ts` → `snakeToCamel`). The backend sends `pnl_pct`; components must
  read `pnlPct`. Reading the snake_case name compiles fine and renders
  `NaN`/blank. Check this first whenever a value is mysteriously empty.
- **Inline styles limit theming.** Global CSS only reaches components using
  `.nd-*` classes; a true global restyle means touching components
  one by one.
- **Two ensemble pipelines exist.** The Orders page talks to the
  microservice ensemble-engine (:8007); the AIEngine page talks to the
  legacy backend ensemble (:8000). A change to one does not affect the other.

## Develop & deploy

```bash
npm run dev          # local Vite dev server (API calls need the stack up)
npm run build        # tsc + vite build → dist/
npm run type-check   # tsc --noEmit
npm run lint

# The running stack serves the *image's* build, not your working tree:
docker compose build frontend && docker compose up -d frontend
```

The app is reached through nginx (container `stock-prediction-nginx`, port 80)
at `http://localhost/neuradex`. If the whole app 502s after recreating
containers, restart nginx first — it caches upstream IPs and a recreated
container gets a new one.

Quick visual check without a browser:

```bash
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new \
  --disable-gpu --screenshot=shot.png --window-size=1400,900 \
  http://localhost/neuradex
```
