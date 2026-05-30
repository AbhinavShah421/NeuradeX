import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  createChart, ColorType, CrosshairMode, IChartApi, UTCTimestamp,
} from 'lightweight-charts';
import { useAppStore } from '../stores/appStore';
import apiService from '../services/api';
import { BacktestResult, BacktestTrade, EquityPoint, LiveSignal, StrategyParam } from '../types';

const inr = (v: number) =>
  `₹${Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (v: number, d = 2) => `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`;

// ── Stocks ─────────────────────────────────────────────────────────────────────
const PORTFOLIO_STOCKS = [
  'IDBI','SUZLON','SHREEGANES','SBIN','INDUSINDBK','TMPV','PNB',
  'FEDERALBNK','TMCV','IREDA','ZEEL','SYNCOMF','IOB','JKTYRE','VIKASECO',
];
const ALL_STOCKS = [
  ...PORTFOLIO_STOCKS,
  'RELIANCE','TCS','INFY','HDFCBANK','ICICIBANK','HINDUNILVR','BAJFINANCE',
  'WIPRO','KOTAKBANK','TATAMOTORS','ADANIENT','MARUTI','SUNPHARMA','TITAN',
];

// Time picker options: 09:15 to 14:45 every 15 min
const TIME_OPTIONS = Array.from({ length: 23 }, (_, i) => {
  const totalMins = 9 * 60 + 15 + i * 15;
  const h = Math.floor(totalMins / 60);
  const m = totalMins % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
});

// ── Strategy defs ──────────────────────────────────────────────────────────────
const STRATEGY_DEFS: Record<string, { name: string; description: string; params: Record<string, StrategyParam> }> = {
  sma_crossover: {
    name: 'SMA Crossover',
    description: 'Buy on golden cross (fast SMA > slow SMA), sell on death cross.',
    params: {
      sma_fast: { label: 'Fast SMA period', default: 20, min: 5,  max: 50,  step: 1,   type: 'int' },
      sma_slow: { label: 'Slow SMA period', default: 50, min: 20, max: 200, step: 5,   type: 'int' },
    },
  },
  rsi_mean_reversion: {
    name: 'RSI Mean Reversion',
    description: 'Buy when RSI drops below oversold; sell when it rises above overbought.',
    params: {
      rsi_period:  { label: 'RSI period',           default: 14, min: 5,  max: 30, step: 1, type: 'int' },
      oversold:    { label: 'Oversold threshold',   default: 30, min: 15, max: 45, step: 1, type: 'int' },
      overbought:  { label: 'Overbought threshold', default: 70, min: 55, max: 85, step: 1, type: 'int' },
    },
  },
  macd_crossover: {
    name: 'MACD Crossover',
    description: 'Buy when MACD line crosses above signal; sell on cross below.',
    params: {
      fast:   { label: 'Fast EMA period',   default: 12, min: 5,  max: 20, step: 1, type: 'int' },
      slow:   { label: 'Slow EMA period',   default: 26, min: 15, max: 50, step: 1, type: 'int' },
      signal: { label: 'Signal EMA period', default: 9,  min: 3,  max: 15, step: 1, type: 'int' },
    },
  },
  bollinger_band: {
    name: 'Bollinger Band Reversion',
    description: 'Buy at lower band; sell at upper band.',
    params: {
      window:  { label: 'Window period',  default: 20,  min: 10, max: 50,  step: 5,   type: 'int' },
      std_dev: { label: 'Std deviations', default: 2.0, min: 1.0, max: 3.0, step: 0.5, type: 'float' },
    },
  },
};

// ── Types ──────────────────────────────────────────────────────────────────────
interface IntradayCandle {
  time: string; timestamp: number;
  open: number; high: number; low: number; close: number; volume: number;
}
interface DayTrade {
  time: string; timestamp: number; action: string; price: number; quantity: number;
  confidence: number; reason: string; pnl: number | null; pnlPct: number | null;
  candleIndex: number; indicators: Record<string, any>;
}

// IST = UTC+5:30 = 19800s. lightweight-charts displays timestamps as UTC,
// so we offset every timestamp so the chart shows IST clock times.
const IST_OFFSET = 19800;

function prevTradingDay(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - 1);
  while (d.getUTCDay() === 0 || d.getUTCDay() === 6) d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

// ── Candle aggregation helpers ─────────────────────────────────────────────────
// aggFactor 1 = 5-min, 2 = 10-min, 3 = 15-min, 6 = 30-min
function buildAggCandles(raw: IntradayCandle[], upTo: number, factor: number) {
  const slice = raw.slice(0, upTo + 1);
  const bars: any[] = [];
  for (let i = 0; i < slice.length; i += factor) {
    const g = slice.slice(i, Math.min(i + factor, slice.length));
    bars.push({
      time:  (g[0].timestamp + IST_OFFSET) as UTCTimestamp,
      open:  g[0].open,
      high:  Math.max(...g.map(c => c.high)),
      low:   Math.min(...g.map(c => c.low)),
      close: g[g.length - 1].close,
    });
  }
  return bars;
}

function buildAggVolume(raw: IntradayCandle[], upTo: number, factor: number) {
  const slice = raw.slice(0, upTo + 1);
  const bars: any[] = [];
  for (let i = 0; i < slice.length; i += factor) {
    const g = slice.slice(i, Math.min(i + factor, slice.length));
    bars.push({
      time:  (g[0].timestamp + IST_OFFSET) as UTCTimestamp,
      value: g.reduce((s, c) => s + c.volume, 0),
      color: g[g.length - 1].close >= g[0].open ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.35)',
    });
  }
  return bars;
}

// visible bars → aggregation factor: zoom out merges candles into higher TF
function visibleBarsToFactor(bars: number) {
  if (bars <= 40) return 1; // 5-min
  if (bars <= 80) return 2; // 10-min
  if (bars <= 150) return 3; // 15-min
  return 6;                  // 30-min
}

function tfLabel(f: number) {
  return f === 1 ? '5m' : f === 2 ? '10m' : f === 3 ? '15m' : '30m';
}

// Stable wrapper so React never re-renders the canvas on parent state changes.
// Only recreates when theme changes (chart color scheme).
const StableChart = React.memo(
  ({ containerRef, isDark }: { containerRef: React.RefObject<HTMLDivElement>; isDark: boolean }) => (
    <div className="nd-chart-gpu">
      <div className="nd-card" style={{ padding: 0, overflow: 'hidden', borderRadius: 12, border: isDark ? '1px solid rgba(42,46,57,0.8)' : '1px solid rgba(0,0,0,0.1)' }}>
        <div ref={containerRef} style={{ height: 480, borderRadius: 12, overflow: 'hidden' }} />
      </div>
    </div>
  ),
  (prev, next) => prev.isDark === next.isDark,
);

// ═══════════════════════════════════════════════════════════════════════════════
//  LIVE REPLAY VIEW — progressive AI trading, candle by candle, no lookahead
// ═══════════════════════════════════════════════════════════════════════════════
const LiveReplayView: React.FC<{
  initialData: any;
  symbol: string;
  date: string;
  capital: number;
  theme: string;
  onReset: () => void;
}> = ({ initialData, symbol, date, capital, theme, onReset }) => {
  const isDark = theme === 'dark';

  // ── Chart refs ──────────────────────────────────────────────────────────────
  const chartRef     = useRef<IChartApi | null>(null);
  const csRef        = useRef<any>(null);
  const volRef       = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const timerRef     = useRef<ReturnType<typeof setInterval>>();
  const liveTimerRef = useRef<ReturnType<typeof setInterval>>();

  // ── Progressive session refs (authoritative source for API calls) ──────────
  const currentIdxRef      = useRef<number>((initialData.prevDayCandles?.length ?? 0) + initialData.candles.length - 1);
  const aggFactorRef       = useRef(1);
  const lastCandleAtRef    = useRef<number>(Date.now());
  const stepPendingRef     = useRef(false);
  const positionRef        = useRef<DayTrade | null>(null);
  const revealedTradesRef  = useRef<{ candleIndex: number; action: string; price: number }[]>([]);
  const livePriceRef       = useRef<number>(0);
  const liveTsRef          = useRef<number>(0);
  const prevDayCandlesRef  = useRef<IntradayCandle[]>(initialData.prevDayCandles ?? []);
  const candlesRef         = useRef<IntradayCandle[]>([...(initialData.prevDayCandles ?? []), ...initialData.candles]);
  const currentTimeRef     = useRef<string>(initialData.currentTime);
  const cashRef            = useRef<number>(initialData.cash);
  const tradesRef          = useRef<any[]>(initialData.trades);
  const isMarketClosedRef  = useRef<boolean>(initialData.isMarketClosed);
  const sessionPositionRef = useRef<any>(initialData.position);
  const historyLoadingRef  = useRef(false);
  // Initialize to the date of the oldest candles already in candlesRef so that
  // loadMoreHistory fetches the day BEFORE what's already loaded, not a duplicate.
  const earliestDateRef    = useRef<string>(
    (initialData.prevDayCandles?.length ?? 0) > 0 ? prevTradingDay(date) : date
  );
  const historyLoaderElRef = useRef<HTMLDivElement>(null);

  // ── React state (UI only) ──────────────────────────────────────────────────
  const [currentCandle,  setCurrentCandle]  = useState<IntradayCandle | null>(candlesRef.current[currentIdxRef.current] ?? null);
  const [currentIdx,     setCurrentIdx]     = useState<number>((initialData.prevDayCandles?.length ?? 0) + initialData.candles.length - 1);
  const [isMarketClosed, setIsMarketClosed] = useState<boolean>(initialData.isMarketClosed);
  const [isPlaying,      setIsPlaying]      = useState(true);
  const [speed,          setSpeed]          = useState(2_000);
  const countdownElRef  = useRef<HTMLSpanElement>(null);
  const [aggFactor,      setAggFactor]      = useState(1);
  const [position,       setPosition]       = useState<DayTrade | null>(null);
  const [closedTrades,   setClosedTrades]   = useState<DayTrade[]>([]);
  const [visibleDecs,    setVisibleDecs]    = useState<DayTrade[]>([]);
  const [chartType,      setChartType]      = useState<'candle'|'line'>('candle');
  const [liveClose,      setLiveClose]      = useState<number | null>(null);
  const [isLiveMode,     setIsLiveMode]     = useState(false);
  const [agentPending,   setAgentPending]   = useState(false);

  const totalCandles   = candlesRef.current.length;
  const displayClose   = (isLiveMode && liveClose !== null) ? liveClose : (currentCandle?.close ?? 0);
  const progress       = Math.min(100, (currentIdx / 74) * 100);
  const sessionEnded   = isMarketClosed;
  const unrealPnl      = position ? (displayClose - position.price) * position.quantity : 0;
  const unrealPct      = position ? (unrealPnl / (position.price * position.quantity)) * 100 : 0;
  const totalClosedPnl = closedTrades.reduce((s, t) => s + (t.pnl ?? 0), 0);

  // ── Helper: rebuild & apply chart markers from revealedTradesRef ───────────
  const applyMarkers = (factor?: number) => {
    const f  = factor ?? aggFactorRef.current;
    const cs = candlesRef.current;
    const markers = revealedTradesRef.current
      .filter(t => t.candleIndex < cs.length)
      .map(t => {
        const aggStart = Math.floor(t.candleIndex / f) * f;
        return {
          time:     (cs[aggStart].timestamp + IST_OFFSET) as UTCTimestamp,
          position: t.action === 'BUY' ? 'belowBar' : 'aboveBar',
          color:    t.action === 'BUY' ? '#22c55e' : '#ef4444',
          shape:    t.action === 'BUY' ? 'arrowUp' : 'arrowDown',
          text:     `${t.action} ₹${t.price.toFixed(2)}`,
          size:     2,
        };
      });
    csRef.current?.setMarkers(markers as any);
  };

  const addTradeMarker = (idx: number, action: 'BUY' | 'SELL', price: number) => {
    revealedTradesRef.current = [...revealedTradesRef.current, { candleIndex: idx, action, price }];
    applyMarkers();
  };

  // ── Load older history when user scrolls to the left edge ────────────────────
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const loadMoreHistory = useCallback(async () => {
    if (historyLoadingRef.current) return;
    historyLoadingRef.current = true;
    if (historyLoaderElRef.current) historyLoaderElRef.current.style.display = 'flex';
    try {
      const prevDate = prevTradingDay(earliestDateRef.current);
      const r = await apiService.getIntradayCandles(symbol, prevDate);
      const newCandles: IntradayCandle[] = (r.data?.candles ?? []) as IntradayCandle[];
      if (newCandles.length === 0) return;

      const n = newCandles.length;
      candlesRef.current = [...newCandles, ...candlesRef.current];
      currentIdxRef.current += n;
      // Shift all trade markers so they still point to the same physical candles
      revealedTradesRef.current = revealedTradesRef.current.map(t => ({ ...t, candleIndex: t.candleIndex + n }));

      const factor = aggFactorRef.current;
      const ts = chartRef.current?.timeScale();
      const oldRange = ts?.getVisibleLogicalRange();

      csRef.current?.setData(buildAggCandles(candlesRef.current, currentIdxRef.current, factor));
      volRef.current?.setData(buildAggVolume(candlesRef.current, currentIdxRef.current, factor));
      applyMarkers(factor);

      // Restore viewport shifted right by the number of newly added aggregated bars
      if (oldRange && ts) {
        const added = Math.ceil(n / factor);
        ts.setVisibleLogicalRange({ from: oldRange.from + added, to: oldRange.to + added });
      }
      earliestDateRef.current = prevDate;
    } catch (err) {
      console.error('History load failed:', err);
    } finally {
      historyLoadingRef.current = false;
      if (historyLoaderElRef.current) historyLoaderElRef.current.style.display = 'none';
    }
  }, []); // all accesses go through refs → stable with empty deps

  // ── Progressive step: fetch fresh Groww data + run agent each candle ────────
  const doProgressiveStep = async () => {
    if (stepPendingRef.current || isMarketClosedRef.current) return;
    stepPendingRef.current = true;
    setAgentPending(true);
    try {
      const sp  = sessionPositionRef.current;
      const res = await apiService.progressiveStep({
        symbol, date, capital,
        current_time: currentTimeRef.current,
        cash:         cashRef.current,
        position:     sp.status as 'NONE' | 'LONG',
        quantity:     sp.quantity,
        entry_price:  sp.entryPrice ?? 0,
        entry_time:   sp.entryTime ?? null,
        trades:       tradesRef.current,
      });
      const d = res.data;

      // Prepend static prev-day candles so all chart logic uses one unified array
      candlesRef.current = [...prevDayCandlesRef.current, ...d.candles];
      const allCandles = candlesRef.current;
      const idx = allCandles.length - 1;
      const c   = d.latestCandle;
      currentIdxRef.current = idx;

      // Update chart with new candle
      const factor   = aggFactorRef.current;
      const aggStart = Math.floor(idx / factor) * factor;
      const group    = allCandles.slice(aggStart, idx + 1);
      csRef.current?.update({
        time:  (allCandles[aggStart].timestamp + IST_OFFSET) as UTCTimestamp,
        open:  allCandles[aggStart].open,
        high:  Math.max(...group.map(g => g.high)),
        low:   Math.min(...group.map(g => g.low)),
        close: c.close,
      });
      volRef.current?.update({
        time:  (allCandles[aggStart].timestamp + IST_OFFSET) as UTCTimestamp,
        value: group.reduce((s, g) => s + g.volume, 0),
        color: c.close >= allCandles[aggStart].open ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.35)',
      });

      // Sync session state from server response
      currentTimeRef.current    = d.currentTime;
      cashRef.current           = d.cash;
      tradesRef.current         = d.trades;
      isMarketClosedRef.current = d.isMarketClosed;
      sessionPositionRef.current = d.position;

      // Handle trade executed this step
      if (d.tradeExecuted) {
        const t         = d.tradeExecuted;
        const lastTrade = d.trades[d.trades.length - 1];
        const dayTrade: DayTrade = {
          time: c.time, timestamp: c.timestamp ?? 0,
          action: t.action, price: t.price, quantity: t.quantity,
          confidence: d.agentDecision.confidence,
          reason:     d.agentDecision.reason,
          pnl:    lastTrade?.pnl    ?? null,
          pnlPct: lastTrade?.pnlPct ?? null,
          candleIndex: idx, indicators: d.indicators ?? {},
        };
        if (t.action === 'BUY') {
          positionRef.current = dayTrade;
          setPosition(dayTrade);
          setVisibleDecs(prev => [...prev, dayTrade]);
          addTradeMarker(idx, 'BUY', t.price);
        } else if (t.action === 'SELL') {
          positionRef.current = null;
          setPosition(null);
          setClosedTrades(prev => [...prev, dayTrade]);
          setVisibleDecs(prev => [...prev, dayTrade]);
          addTradeMarker(idx, 'SELL', t.price);
        }
      }

      // Update React state for render
      setCurrentCandle(d.latestCandle);
      setCurrentIdx(idx);
      if (d.isMarketClosed) {
        setIsMarketClosed(true);
        setIsPlaying(false);
      }

    } catch (err) {
      console.error('Progressive step failed:', err);
    } finally {
      stepPendingRef.current = false;
      setAgentPending(false);
    }
  };

  // ── Chart init (on theme / chart-type change) ──────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    chartRef.current?.remove();

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: isDark ? '#131722' : '#ffffff' },
        textColor:  isDark ? '#b2b5be' : '#131722',
        fontSize:   11,
        fontFamily: "'Inter', 'Trebuchet MS', sans-serif",
      },
      grid: {
        vertLines: { color: isDark ? 'rgba(42,46,57,0.6)' : 'rgba(0,0,0,0.06)', style: 1 },
        horzLines: { color: isDark ? 'rgba(42,46,57,0.6)' : 'rgba(0,0,0,0.06)', style: 1 },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: isDark ? '#758696' : '#9B9EA3', width: 1, style: 1 },
        horzLine: { color: isDark ? '#758696' : '#9B9EA3', width: 1, style: 1 },
      },
      rightPriceScale: {
        borderColor: isDark ? 'rgba(42,46,57,0.8)' : 'rgba(0,0,0,0.12)',
        borderVisible: true,
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: isDark ? 'rgba(42,46,57,0.8)' : 'rgba(0,0,0,0.12)',
        timeVisible: true, secondsVisible: false,
        rightOffset: 8, fixLeftEdge: false, fixRightEdge: false,
        tickMarkFormatter: (time: number) => {
          const d = new Date(time * 1000);
          return `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
        },
      },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true },
      handleScale:  { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: false } },
      width:  containerRef.current.clientWidth,
      height: 480,
    });
    chartRef.current = chart;

    const cs = chart.addCandlestickSeries({
      upColor:        '#26a69a',
      downColor:      '#ef5350',
      borderUpColor:  '#26a69a',
      borderDownColor:'#ef5350',
      wickUpColor:    '#26a69a',
      wickDownColor:  '#ef5350',
      borderVisible:  true,
      wickVisible:    true,
    });
    csRef.current = cs;

    const vs = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.80, bottom: 0 }, visible: false });
    volRef.current = vs;

    aggFactorRef.current = 1;
    setAggFactor(1);

    const curIdx = currentIdxRef.current;
    cs.setData(buildAggCandles(candlesRef.current, curIdx, 1));
    vs.setData(buildAggVolume(candlesRef.current, curIdx, 1));
    applyMarkers(1); // restore markers after re-init (theme / chart-type change)

    chart.timeScale().fitContent(); // show all data from left edge on init

    // Aggregation on zoom: rAF-debounced so it fires once per frame, not per pixel.
    // Aggregation on zoom: rAF-debounced. Uses a timestamp cooldown (not a flag) so
    // it reliably blocks re-entry even if lightweight-charts fires callbacks asynchronously.
    // We removed setVisibleLogicalRange entirely — setData auto-fits the new data, which is
    // the correct UX for a user-initiated timeframe change and avoids all oscillation.
    let rafId: number | null = null;
    let lastAggSwitch = 0;
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      if (rafId !== null) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        const range = chart.timeScale().getVisibleLogicalRange();
        if (!range) return;

        // User scrolled near the left edge → fetch the previous trading day
        if (range.from <= 3 && !historyLoadingRef.current) {
          loadMoreHistory();
        }

        if (Date.now() - lastAggSwitch < 400) return;
        const visibleBars = Math.max(1, Math.round(range.to - range.from));
        const newFactor   = visibleBarsToFactor(visibleBars);
        if (newFactor === aggFactorRef.current) return;

        lastAggSwitch = Date.now();
        aggFactorRef.current = newFactor;
        setAggFactor(newFactor);

        const curIdx = currentIdxRef.current;
        cs.setData(buildAggCandles(candlesRef.current, curIdx, newFactor));
        vs.setData(buildAggVolume(candlesRef.current, curIdx, newFactor));
        applyMarkers(newFactor);
        // setData auto-fits the chart — no setVisibleLogicalRange needed (and it caused oscillation)
      });
    });

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    ro.observe(containerRef.current);

    // Two-finger horizontal trackpad swipe → pan the chart.
    // deltaX dominates for a horizontal gesture; deltaY dominates for vertical (zoom handled natively).
    const el = containerRef.current;
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return; // let native zoom pass through
      e.preventDefault();
      const ts = chart.timeScale();
      const range = ts.getVisibleLogicalRange();
      if (!range) return;
      const pixelsPerBar = el.clientWidth / Math.max(1, range.to - range.from);
      const shift = e.deltaX / pixelsPerBar;
      ts.setVisibleLogicalRange({ from: range.from + shift, to: range.to + shift });
    };
    el.addEventListener('wheel', onWheel, { passive: false });

    return () => {
      el.removeEventListener('wheel', onWheel);
      if (rafId !== null) cancelAnimationFrame(rafId);
      ro.disconnect();
      chart.remove();
    };
  }, [isDark, chartType]);

  // ── Replay timer: fire progressive step (fresh Groww fetch) each tick ───────
  useEffect(() => {
    clearInterval(timerRef.current);
    if (!isPlaying) return;

    lastCandleAtRef.current = Date.now();

    timerRef.current = setInterval(() => {
      lastCandleAtRef.current = Date.now();
      doProgressiveStep(); // async — stepPendingRef prevents overlap
    }, speed);

    return () => clearInterval(timerRef.current);
  }, [isPlaying, speed]);

  // ── Countdown ticker — writes directly to DOM, zero React re-renders ─────────
  useEffect(() => {
    const id = setInterval(() => {
      const el = countdownElRef.current;
      if (!el) return;
      if (!isPlaying) { el.textContent = ''; return; }
      const remaining = Math.max(0, speed - (Date.now() - lastCandleAtRef.current));
      const secs = Math.ceil(remaining / 1000);
      el.textContent = secs > 1
        ? (secs >= 60 ? `next in ${Math.floor(secs/60)}m ${secs%60}s` : `next in ${secs}s`)
        : '';
    }, 500);
    return () => clearInterval(id);
  }, [isPlaying, speed]);

  // ── Session end: server already closed any open position; start live continuation ──
  useEffect(() => {
    if (!isMarketClosed) return;
    setIsPlaying(false);

    // Live continuation: keep generating simulated ticks until 15:30
    const MARKET_CLOSE_MINUTES = 15 * 60 + 30;
    const allCandles = candlesRef.current;
    if (allCandles.length === 0) return;
    const lastCandle = allCandles[allCandles.length - 1];
    const [lh, lm] = lastCandle.time.split(':').map(Number);
    const lastCandleMinutes = lh * 60 + lm;
    if (lastCandleMinutes >= MARKET_CLOSE_MINUTES) return;

    livePriceRef.current = lastCandle.close;
    liveTsRef.current    = lastCandle.timestamp + 300;
    setIsLiveMode(true);

    liveTimerRef.current = setInterval(() => {
      // Check if simulated clock has reached market close (15:30 IST = timestamp mod 86400)
      const istSeconds  = (liveTsRef.current + 19800) % 86400; // seconds since midnight IST
      const istMinutes  = Math.floor(istSeconds / 60);
      if (istMinutes >= MARKET_CLOSE_MINUTES) {
        clearInterval(liveTimerRef.current);
        setIsLiveMode(false);
        return;
      }

      const p     = livePriceRef.current;
      const move  = (Math.random() - 0.501) * 0.0032;
      const close = Math.round(p * (1 + move) * 100) / 100;
      const open  = Math.round(p * (1 + (Math.random() - 0.5) * 0.001) * 100) / 100;
      const high  = Math.round(Math.max(open, close) * (1 + Math.random() * 0.0015) * 100) / 100;
      const low   = Math.round(Math.min(open, close) * (1 - Math.random() * 0.0015) * 100) / 100;
      const ts    = liveTsRef.current;
      csRef.current?.update({ time: (ts + IST_OFFSET) as UTCTimestamp, open, high, low, close });
      volRef.current?.update({ time: (ts + IST_OFFSET) as UTCTimestamp, value: Math.floor(200_000 + Math.random() * 500_000), color: close >= open ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.35)' });
      livePriceRef.current = close;
      liveTsRef.current    = ts + 300;
      setLiveClose(close);
    }, speed);

    return () => clearInterval(liveTimerRef.current);
  }, [isMarketClosed]); // eslint-disable-line react-hooks/exhaustive-deps

  // Speed change in live mode: restart live timer
  useEffect(() => {
    if (!isLiveMode) return;
    const MARKET_CLOSE_MINUTES = 15 * 60 + 30;
    clearInterval(liveTimerRef.current);
    liveTimerRef.current = setInterval(() => {
      const istSeconds = (liveTsRef.current + 19800) % 86400;
      const istMinutes = Math.floor(istSeconds / 60);
      if (istMinutes >= MARKET_CLOSE_MINUTES) {
        clearInterval(liveTimerRef.current);
        setIsLiveMode(false);
        return;
      }
      const p     = livePriceRef.current;
      const move  = (Math.random() - 0.501) * 0.0032;
      const close = Math.round(p * (1 + move) * 100) / 100;
      const open  = Math.round(p * (1 + (Math.random() - 0.5) * 0.001) * 100) / 100;
      const high  = Math.round(Math.max(open, close) * (1 + Math.random() * 0.0015) * 100) / 100;
      const low   = Math.round(Math.min(open, close) * (1 - Math.random() * 0.0015) * 100) / 100;
      const ts    = liveTsRef.current;
      csRef.current?.update({ time: (ts + IST_OFFSET) as UTCTimestamp, open, high, low, close });
      volRef.current?.update({ time: (ts + IST_OFFSET) as UTCTimestamp, value: Math.floor(200_000 + Math.random() * 500_000), color: close >= open ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.35)' });
      livePriceRef.current = close;
      liveTsRef.current    = ts + 300;
      setLiveClose(close);
    }, speed);
    return () => clearInterval(liveTimerRef.current);
  }, [speed, isLiveMode]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
    <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>

      {/* ══ LEFT — Chart (60%) ═══════════════════════════════════════════════ */}
      <div className="nd-chart-col" style={{ flex: '0 0 60%', minWidth: 0, position: 'relative' }}>
        <StableChart containerRef={containerRef} isDark={isDark} />
        {/* History loading indicator — shown/hidden via DOM ref, zero React re-renders */}
        <div ref={historyLoaderElRef} style={{
          display: 'none', position: 'absolute', top: 10, left: 48, zIndex: 10,
          alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 6, fontSize: 11,
          background: isDark ? 'rgba(19,23,34,0.88)' : 'rgba(255,255,255,0.88)',
          border: `1px solid ${isDark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.1)'}`,
          color: 'var(--nd-text-2)', backdropFilter: 'blur(4px)',
        }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--nd-primary)', animation: 'nd-pulse 0.8s infinite' }} />
          Loading history…
        </div>
        {/* Scroll arrows — overlay on chart edges, never trigger React re-renders */}
        {([['◀', -1], ['▶', 1]] as [string, number][]).map(([arrow, dir]) => (
          <button key={arrow} onClick={() => {
            const ts = chartRef.current?.timeScale();
            if (!ts) return;
            const r = ts.getVisibleLogicalRange();
            if (!r) return;
            const shift = (r.to - r.from) * 0.25;
            ts.setVisibleLogicalRange({ from: r.from + dir * shift, to: r.to + dir * shift });
          }} style={{
            position: 'absolute', top: '50%',
            [dir === -1 ? 'left' : 'right']: 10,
            transform: 'translateY(-50%)',
            width: 32, height: 32,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            borderRadius: 8,
            border: `1px solid ${isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)'}`,
            background: isDark ? 'rgba(19,23,34,0.85)' : 'rgba(255,255,255,0.85)',
            backdropFilter: 'blur(4px)',
            color: isDark ? '#b2b5be' : '#444',
            fontSize: 14, cursor: 'pointer', zIndex: 10,
            boxShadow: '0 2px 8px rgba(0,0,0,0.18)',
          }}>{arrow}</button>
        ))}
      </div>

      {/* ══ RIGHT — Info cards (40%) ══════════════════════════════════════════ */}
      <div className="nd-info-col" style={{ flex: '0 0 calc(40% - 16px)', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>

      {/* ── Live Header ──────────────────────────────────────────────────────── */}
      <div className="nd-card" style={{ padding: '16px 20px' }}>

        {/* Row 1: Symbol + price — always stable */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 800, fontSize: 15 }}>{symbol}</span>
          <span style={{ color: 'var(--nd-text-3)', fontSize: 12 }}>{date}</span>
          <span style={{ fontSize: 20, fontWeight: 800 }}>₹{displayClose.toFixed(2)}</span>
          {currentCandle && <span style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>{currentCandle.time} IST</span>}
          <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 20, background: isDark ? 'rgba(255,255,255,0.07)' : '#f1f5f9', color: 'var(--nd-text-3)' }}>
            {initialData.dataSource === 'groww' ? '🟢 Live Data' : '🔵 Simulated'}
          </span>
        </div>

        {/* Row 2: Controls — always stable, never shift */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
          <div style={{ display: 'flex', gap: 3, background: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)', borderRadius: 8, padding: 3 }}>
            {(['candle','line'] as const).map(t => (
              <button key={t} onClick={() => setChartType(t)} style={{ padding: '4px 12px', borderRadius: 6, border: 'none', fontSize: 11, fontWeight: 600, cursor: 'pointer', background: chartType === t ? 'var(--nd-primary)' : 'transparent', color: chartType === t ? '#000' : 'var(--nd-text-2)' }}>
                {t === 'candle' ? '🕯' : '📈'}
              </button>
            ))}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            {([['−', 0.6, 'Zoom out'], ['+', 1.6, 'Zoom in']] as [string, number, string][]).map(([label, factor, title]) => (
              <button key={label} title={title} onClick={() => {
                const ts = chartRef.current?.timeScale();
                if (!ts) return;
                const range = ts.getVisibleLogicalRange();
                if (!range) return;
                const mid = (range.from + range.to) / 2;
                const half = (range.to - range.from) / 2;
                ts.setVisibleLogicalRange({ from: mid - Math.max(3, Math.min(120, half * factor)), to: mid + Math.max(3, Math.min(120, half * factor)) });
              }} style={{ width: 26, height: 26, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 6, border: `1px solid ${isDark ? 'rgba(255,255,255,0.12)' : '#e2e8f0'}`, background: 'transparent', color: 'var(--nd-text-2)', fontSize: 15, fontWeight: 700, cursor: 'pointer' }}>{label}</button>
            ))}
          </div>

          <span style={{ fontSize: 11, fontWeight: 800, padding: '3px 8px', borderRadius: 6, background: isDark ? 'rgba(99,102,241,0.2)' : '#ede9fe', color: '#6366f1', letterSpacing: '0.05em' }}>{tfLabel(aggFactor)}</span>

          <div style={{ display: 'flex', gap: 2, background: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)', borderRadius: 8, padding: 3 }}>
            {([[4_000,'½×'],[2_000,'1×'],[1_000,'2×'],[400,'5×'],[200,'10×']] as [number,string][]).map(([ms, label]) => (
              <button key={ms} onClick={() => { setSpeed(ms); lastCandleAtRef.current = Date.now(); }} style={{ padding: '4px 8px', borderRadius: 6, border: 'none', fontSize: 11, fontWeight: 600, cursor: 'pointer', background: speed === ms ? 'var(--nd-primary)' : 'transparent', color: speed === ms ? '#000' : 'var(--nd-text-2)' }}>{label}</button>
            ))}
          </div>

          {!sessionEnded && (
            <button onClick={() => setIsPlaying(p => !p)} style={{ padding: '5px 14px', borderRadius: 8, border: 'none', cursor: 'pointer', fontWeight: 700, fontSize: 12, background: isPlaying ? 'rgba(239,68,68,0.12)' : 'rgba(34,197,94,0.12)', color: isPlaying ? '#ef4444' : '#22c55e' }}>
              {isPlaying ? '⏸ Pause' : '▶ Resume'}
            </button>
          )}
          <button onClick={onReset} style={{ padding: '5px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: `1px solid ${isDark ? 'rgba(255,255,255,0.12)' : '#e2e8f0'}`, background: 'transparent', color: 'var(--nd-text-3)' }}>New Run</button>
        </div>

        {/* Row 3: Dynamic status — lives at the bottom so rows 1+2 never shift */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          {(() => {
            const col = sessionEnded ? '#9ca3af'
              : agentPending ? '#eab308'
              : !isPlaying ? '#6b7280'
              : closedTrades.length > 0 && !position ? '#6366f1'
              : '#22c55e';
            const label = sessionEnded ? 'Session End'
              : agentPending ? 'AI Thinking…'
              : !isPlaying ? 'Paused'
              : closedTrades.length > 0 && !position ? 'Post-Trade'
              : 'Live';
            return (
              <>
                <div style={{ width: 9, height: 9, borderRadius: '50%', background: col, boxShadow: !sessionEnded ? `0 0 6px ${col}` : 'none', animation: !sessionEnded && (isPlaying || agentPending) ? 'nd-pulse 1.2s infinite' : 'none' }} />
                <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase', color: col }}>{label}</span>
              </>
            );
          })()}
        </div>

        <div style={{ height: 4, background: isDark ? 'rgba(255,255,255,0.06)' : '#f1f5f9', borderRadius: 2 }}>
          <div style={{ height: '100%', width: `${progress}%`, background: 'var(--nd-primary)', borderRadius: 2, transition: 'width 0.25s linear' }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4, fontSize: 10, color: 'var(--nd-text-3)' }}>
          <span>{candlesRef.current[prevDayCandlesRef.current.length]?.time ?? '09:15'} IST</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span ref={countdownElRef} style={{ color: 'var(--nd-primary)', fontWeight: 700, fontSize: 11 }} />
            <span>Candle {currentIdx + 1} / {totalCandles}</span>
          </span>
          <span>15:25 IST</span>
        </div>
      </div>

      {/* ── AI Status Card — always visible, fixed height, content updates in-place ── */}
      <div className="nd-card" style={{
        padding: '12px 16px', minHeight: 60,
        border: `1px solid ${agentPending
          ? (isDark ? 'rgba(234,179,8,0.35)' : '#fef08a')
          : (isDark ? 'rgba(255,255,255,0.07)' : '#e2e8f0')}`,
        transition: 'border-color 0.25s',
      }}>
        {agentPending ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ display: 'flex', gap: 4 }}>
                {[0,1,2].map(i => <div key={i} style={{ width: 6, height: 6, borderRadius: '50%', background: '#eab308', animation: `nd-pulse ${0.5 + i * 0.15}s ease-in-out infinite alternate` }} />)}
              </div>
              <span style={{ fontSize: 13, fontWeight: 600, color: '#eab308' }}>
                AI analysing {symbol} at {currentTimeRef.current} IST…
              </span>
            </div>
            <span style={{ fontSize: 11, color: 'var(--nd-text-3)', paddingLeft: 22 }}>Evaluating VWAP · RSI · Momentum · Consulting LLM</span>
          </div>
        ) : sessionEnded ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 15 }}>✅</span>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-2)' }}>Session complete — all candles processed</span>
          </div>
        ) : !isPlaying ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 15 }}>⏸</span>
            <span style={{ fontSize: 13, color: 'var(--nd-text-2)' }}>Paused — press Resume to continue</span>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#22c55e', animation: 'nd-pulse 1.4s infinite' }} />
            <span style={{ fontSize: 13, color: 'var(--nd-text-2)' }}>
              Waiting for next candle
              {visibleDecs.length > 0 && (
                <span style={{ color: 'var(--nd-text-3)', marginLeft: 8, fontSize: 11 }}>
                  — last: <strong style={{ color: visibleDecs[visibleDecs.length-1].action === 'BUY' ? '#22c55e' : '#ef4444' }}>{visibleDecs[visibleDecs.length-1].action}</strong> @ ₹{visibleDecs[visibleDecs.length-1].price}
                </span>
              )}
            </span>
          </div>
        )}
      </div>

      {/* ── Live Position Card ────────────────────────────────────────────────── */}
      {position && (
        <div className="nd-card" style={{ padding: '18px 20px', border: '1px solid #22c55e44', boxShadow: '0 0 20px rgba(34,197,94,0.12)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
            <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#22c55e', boxShadow: '0 0 8px #22c55e', animation: 'nd-pulse 1.2s infinite', willChange: 'opacity, transform', transform: 'translateZ(0)' }} />
            <span style={{ fontWeight: 800, fontSize: 14, color: '#22c55e' }}>AI HOLDING — {symbol}</span>
            <span style={{ fontSize: 12, color: 'var(--nd-text-3)', marginLeft: 4 }}>Entered at {position.time} IST</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 16, marginBottom: 12 }}>
            {[
              { label: 'Entry Price',   value: `₹${position.price.toFixed(2)}` },
              { label: 'Quantity',      value: String(position.quantity) },
              { label: 'Current Price', value: `₹${displayClose.toFixed(2)}` },
              { label: 'Invested',      value: inr(position.price * position.quantity) },
            ].map(({ label, value }) => (
              <div key={label}>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 2 }}>{label}</div>
                <div style={{ fontSize: 15, fontWeight: 700 }}>{value}</div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Unrealised P&L</span>
            <span style={{ fontSize: 26, fontWeight: 900, color: unrealPnl >= 0 ? '#22c55e' : '#ef4444' }}>{unrealPnl >= 0 ? '+' : ''}{inr(unrealPnl)}</span>
            <span style={{ fontSize: 14, fontWeight: 700, color: unrealPnl >= 0 ? '#22c55e' : '#ef4444' }}>({unrealPct >= 0 ? '+' : ''}{unrealPct.toFixed(2)}%)</span>
          </div>
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--nd-text-3)', fontStyle: 'italic' }}>🤖 Entry reason: {position.reason}</div>
        </div>
      )}

      {/* ── Post-Sell Observation Card ───────────────────────────────────────── */}
      {closedTrades.length > 0 && !position && !sessionEnded && (() => {
        const lastSell     = closedTrades[closedTrades.length - 1];
        const sellPrice    = lastSell.price;
        const sellQty      = lastSell.quantity;
        const currentPrice = displayClose > 0 ? displayClose : sellPrice;
        const ifHeldPnl    = (currentPrice - sellPrice) * sellQty;
        const ifHeldPct    = ((currentPrice - sellPrice) / sellPrice) * 100;
        const aiRight      = ifHeldPnl <= 0;
        return (
          <div className="nd-card" style={{ padding: '18px 20px', border: `1px solid ${isDark ? 'rgba(99,102,241,0.35)' : '#c7d2fe'}`, boxShadow: '0 0 20px rgba(99,102,241,0.1)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
              <div style={{ width: 9, height: 9, borderRadius: '50%', background: '#6366f1', boxShadow: '0 0 8px #6366f1', animation: 'nd-pulse 1.4s infinite', willChange: 'opacity, transform', transform: 'translateZ(0)' }} />
              <span style={{ fontWeight: 800, fontSize: 14, color: '#6366f1' }}>Post-Sell Observation</span>
              <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Watching {symbol} after AI exit @ {lastSell.time} IST — candles still live</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 16, marginBottom: 12 }}>
              {[
                { label: 'AI Sold At',    value: `₹${sellPrice.toFixed(2)}`,     color: 'var(--nd-text-1)' },
                { label: 'Current Price', value: `₹${currentPrice.toFixed(2)}`,  color: currentPrice >= sellPrice ? '#ef4444' : '#22c55e' },
                { label: 'If Still Held', value: `${ifHeldPnl >= 0 ? '+' : ''}${inr(ifHeldPnl)} (${ifHeldPct >= 0 ? '+' : ''}${ifHeldPct.toFixed(2)}%)`, color: ifHeldPnl >= 0 ? '#ef4444' : '#22c55e' },
                { label: 'AI Decision',   value: aiRight ? '✓ Correct Sell' : '✗ Sold Too Early', color: aiRight ? '#22c55e' : '#ef4444' },
              ].map(({ label, value, color }) => (
                <div key={label}>
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 3 }}>{label}</div>
                  <div style={{ fontSize: 15, fontWeight: 700, color }}>{value}</div>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 12, color: 'var(--nd-text-2)', fontStyle: 'italic', padding: '8px 10px', borderRadius: 8, background: isDark ? 'rgba(99,102,241,0.08)' : '#eef2ff', borderLeft: `3px solid ${aiRight ? '#22c55e' : '#ef4444'}` }}>
              {aiRight ? `Price fell ₹${Math.abs(currentPrice - sellPrice).toFixed(2)} after the sell — AI correctly protected capital.` : `Price rose ₹${Math.abs(currentPrice - sellPrice).toFixed(2)} after the sell — AI may have exited too early.`}
            </div>
          </div>
        );
      })()}

      {/* ── Session Complete ─────────────────────────────────────────────────── */}
      {sessionEnded && (
        <div className="nd-card" style={{ padding: '18px 20px', borderLeft: `3px solid ${totalClosedPnl >= 0 ? '#22c55e' : '#ef4444'}` }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 12 }}>✅ Trading Session Complete</div>
          <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Total P&L</div>
              <div style={{ fontSize: 28, fontWeight: 900, color: totalClosedPnl >= 0 ? '#22c55e' : '#ef4444' }}>{totalClosedPnl >= 0 ? '+' : ''}{inr(totalClosedPnl)}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Trades Executed</div>
              <div style={{ fontSize: 28, fontWeight: 900 }}>{closedTrades.length}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Win Rate</div>
              <div style={{ fontSize: 28, fontWeight: 900, color: closedTrades.filter(t=>(t.pnl??0)>0).length === closedTrades.length ? '#22c55e' : 'var(--nd-text-1)' }}>
                {closedTrades.length > 0 ? `${Math.round(closedTrades.filter(t=>(t.pnl??0)>0).length / closedTrades.length * 100)}%` : '—'}
              </div>
            </div>
          </div>
        </div>
      )}

      </div>{/* end right column */}
    </div>{/* end 60/40 row */}

      {/* ── AI Decision Log (revealed in real-time, full-width below) ─────── */}
      {visibleDecs.length > 0 && (
        <div className="nd-card" style={{ padding: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-primary)' }}>psychology</span>
            AI Decisions — {visibleDecs.length} so far
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[...visibleDecs].reverse().map((t, i) => {
              const isBuy   = t.action === 'BUY';
              const tPnlPos = (t.pnl ?? 0) >= 0;
              return (
                <div key={i} style={{
                  borderRadius: 10, padding: '12px 14px',
                  background: isDark ? 'rgba(255,255,255,0.04)' : '#f8fafc',
                  borderLeft: `3px solid ${isBuy ? '#22c55e' : t.pnl === null ? '#6b7280' : tPnlPos ? '#22c55e' : '#ef4444'}`,
                  animation: i === 0 ? 'nd-slide-in 0.3s ease' : 'none',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{
                        padding: '2px 10px', borderRadius: 20, fontSize: 11, fontWeight: 700,
                        background: isBuy ? '#22c55e22' : '#ef444422',
                        color: isBuy ? '#22c55e' : '#ef4444',
                      }}>{isBuy ? '▲ BUY' : '▼ SELL'}</span>
                      <span style={{ fontWeight: 600, fontSize: 13 }}>{t.time} IST</span>
                      <span style={{ color: 'var(--nd-text-2)', fontSize: 13 }}>@₹{t.price}</span>
                      <span style={{ color: 'var(--nd-text-3)', fontSize: 12 }}>Qty: {t.quantity}</span>
                      <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 20, background: isDark ? 'rgba(255,255,255,0.06)' : '#e2e8f0', color: 'var(--nd-text-3)' }}>
                        Conf {t.confidence}%
                      </span>
                    </div>
                    {t.pnl !== null && (
                      <span style={{ fontWeight: 800, fontSize: 14, color: tPnlPos ? '#22c55e' : '#ef4444' }}>
                        {tPnlPos ? '+' : ''}{inr(t.pnl)} ({tPnlPos ? '+' : ''}{(t.pnlPct ?? 0).toFixed(2)}%)
                      </span>
                    )}
                  </div>
                  <div style={{
                    marginTop: 8, fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.55,
                    padding: '6px 10px', borderRadius: 6,
                    background: isDark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.03)',
                    borderLeft: `2px solid ${isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)'}`,
                  }}>
                    🤖 {t.reason}
                  </div>
                  {t.indicators && Object.keys(t.indicators).length > 0 && (
                    <div style={{ display: 'flex', gap: 12, marginTop: 7, flexWrap: 'wrap' }}>
                      {(['vwap','sma5','sma20','rsi','mom5'] as const).map(k =>
                        t.indicators[k] !== undefined ? (
                          <span key={k} style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>
                            <strong style={{ textTransform: 'uppercase' }}>{k}</strong>{' '}
                            {k === 'rsi' ? Number(t.indicators[k]).toFixed(1)
                              : k === 'mom5' ? `${Number(t.indicators[k]) >= 0 ? '+' : ''}${Number(t.indicators[k]).toFixed(2)}%`
                              : `₹${t.indicators[k]}`}
                          </span>
                        ) : null
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
//  EQUITY CURVE  (strategy backtest)
// ═══════════════════════════════════════════════════════════════════════════════
const EquityCurve: React.FC<{ curve: EquityPoint[]; initialCapital: number; trades: BacktestTrade[]; theme: string }> = ({ curve, initialCapital, trades, theme }) => {
  if (curve.length < 2) return null;
  const W = 800, H = 300, PL = 70, PR = 20, PT = 20, PB = 40;
  const plotW = W - PL - PR, plotH = H - PT - PB;
  const allV = curve.flatMap(p => [p.portfolio, p.benchmark]);
  const minV = Math.min(...allV) * 0.985, maxV = Math.max(...allV) * 1.015;
  const sx = (i: number) => PL + (i / (curve.length - 1)) * plotW;
  const sy = (v: number) => PT + plotH - ((v - minV) / (maxV - minV)) * plotH;
  const portPts  = curve.map((p, i) => `${sx(i).toFixed(1)},${sy(p.portfolio).toFixed(1)}`).join(' ');
  const benchPts = curve.map((p, i) => `${sx(i).toFixed(1)},${sy(p.benchmark).toFixed(1)}`).join(' ');
  const yLabels = Array.from({ length: 5 }, (_, i) => {
    const v = minV + (maxV - minV) * (i / 4);
    return { y: sy(v), label: v >= 1_000_000 ? `₹${(v/1e6).toFixed(2)}M` : `₹${(v/1000).toFixed(0)}K` };
  });
  const xStep  = Math.max(1, Math.floor(curve.length / 6));
  const xLabels = Array.from({ length: 7 }, (_, i) => {
    const idx = Math.min(i * xStep, curve.length - 1);
    return { x: sx(idx), label: curve[idx].date.slice(5) };
  });
  const textColor = theme === 'dark' ? '#9CA3AF' : '#6B7280';
  const gridColor = theme === 'dark' ? '#374151' : '#E5E7EB';
  const refY = sy(initialCapital);
  const dateIndex = new Map(curve.map((p, i) => [p.date, i]));
  const tradeMarkers = trades.flatMap(t => {
    const marks: {x:number;y:number;color:string;label:string}[] = [];
    const ei = dateIndex.get(t.entryDate); if (ei !== undefined) marks.push({ x: sx(ei), y: sy(curve[ei].portfolio), color: '#22C55E', label: 'B' });
    const xi = dateIndex.get(t.exitDate);  if (xi !== undefined) marks.push({ x: sx(xi), y: sy(curve[xi].portfolio), color: t.type === 'WIN' ? '#3B82F6' : '#EF4444', label: 'S' });
    return marks;
  });
  const portColor = curve[curve.length - 1].portfolio >= initialCapital ? '#3B82F6' : '#EF4444';
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 300 }}>
      {yLabels.map((l, i) => <line key={i} x1={PL} x2={W-PR} y1={l.y} y2={l.y} stroke={gridColor} strokeDasharray="4" strokeWidth="1" />)}
      <line x1={PL} x2={W-PR} y1={refY} y2={refY} stroke="#6B7280" strokeDasharray="8,4" strokeWidth="1.5" opacity="0.5" />
      <polyline points={benchPts} fill="none" stroke="#9CA3AF" strokeWidth="1.5" strokeDasharray="6,3" />
      <polyline points={portPts}  fill="none" stroke={portColor} strokeWidth="2.5" />
      {tradeMarkers.map((m, i) => <circle key={i} cx={m.x} cy={m.y} r="4" fill={m.color} opacity="0.85" />)}
      {yLabels.map((l, i) => <text key={i} x={PL-5} y={l.y+4} textAnchor="end" fontSize="10" fill={textColor}>{l.label}</text>)}
      {xLabels.map((l, i) => <text key={i} x={l.x} y={H-8} textAnchor="middle" fontSize="10" fill={textColor}>{l.label}</text>)}
      <line x1={PL} x2={PL+24} y1={PT+12} y2={PT+12} stroke={portColor} strokeWidth="2.5" />
      <text x={PL+28} y={PT+16} fontSize="11" fill={textColor}>Strategy</text>
      <line x1={PL+90} x2={PL+114} y1={PT+12} y2={PT+12} stroke="#9CA3AF" strokeWidth="1.5" strokeDasharray="6,3" />
      <text x={PL+118} y={PT+16} fontSize="11" fill={textColor}>Buy &amp; Hold</text>
    </svg>
  );
};

// ── Helpers ────────────────────────────────────────────────────────────────────
const MCard: React.FC<{ label: string; value: string; sub?: string; color?: string }> = ({ label, value, sub, color = '' }) => (
  <div className="nd-metric">
    <p className="nd-metric-label">{label}</p>
    <p className="nd-metric-value" style={{ fontSize: 18, color: color.includes('green') ? 'var(--nd-green)' : color.includes('red') ? 'var(--nd-red)' : color.includes('yellow') ? '#ca8a04' : 'var(--nd-text-1)' }}>
      {value}
    </p>
    {sub && <p className="nd-metric-sub">{sub}</p>}
  </div>
);

const SignalBadge: React.FC<{ signal: string }> = ({ signal }) => {
  const bg   = signal === 'BUY' ? 'var(--nd-green)' : signal === 'SELL' ? 'var(--nd-red)' : '#ca8a04';
  const icon = signal === 'BUY' ? 'trending_up' : signal === 'SELL' ? 'trending_down' : 'pause_circle';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 18px', borderRadius: 100, background: bg, color: '#fff', fontWeight: 700, fontSize: 16 }}>
      <span className="material-icons" style={{ fontSize: 18 }}>{icon}</span>
      {signal}
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
//  MAIN PAGE
// ═══════════════════════════════════════════════════════════════════════════════
const BacktestPage: React.FC = () => {
  const { theme } = useAppStore();
  const isDark = theme === 'dark';

  const [pageTab, setPageTab] = useState<'autopilot'|'strategy'>('autopilot');

  // ── AI Autopilot state ──────────────────────────────────────────────────────
  const [apSymbol,    setApSymbol]    = useState('SBIN');
  const [apDate,      setApDate]      = useState(() => {
    const d = new Date(); d.setDate(d.getDate() - 3);
    return d.toISOString().slice(0, 10);
  });
  const [apStartTime, setApStartTime] = useState('09:15');
  const [apCapital,   setApCapital]   = useState('50000');
  const [apSession,   setApSession]   = useState<any>(null);
  const [apRunning,   setApRunning]   = useState(false);
  const [apError,     setApError]     = useState<string | null>(null);

  const handleAutopilot = async () => {
    setApRunning(true);
    setApError(null);
    setApSession(null);
    try {
      const r = await apiService.progressiveStart({
        symbol:     apSymbol,
        date:       apDate,
        start_time: apStartTime,
        capital:    parseFloat(apCapital) || 50_000,
      });
      if (!r.data || !r.data.candles || r.data.candles.length === 0) {
        setApError('No intraday data available for this date');
        return;
      }
      setApSession({
        initialData: r.data,
        symbol:      apSymbol,
        date:        apDate,
        capital:     parseFloat(apCapital) || 50_000,
      });
    } catch (err: any) {
      setApError(err?.response?.data?.detail || err?.message || 'Failed to start progressive session');
    } finally {
      setApRunning(false);
    }
  };

  // ── Strategy backtest state ─────────────────────────────────────────────────
  const [symbol,     setSymbol]     = useState('SBIN');
  const [strategy,   setStrategy]   = useState('sma_crossover');
  const [startDate,  setStartDate]  = useState(() => { const d = new Date(); d.setFullYear(d.getFullYear()-1); return d.toISOString().slice(0,10); });
  const [endDate,    setEndDate]    = useState(() => new Date().toISOString().slice(0,10));
  const [capital,    setCapital]    = useState('100000');
  const [commission, setCommission] = useState('0.1');
  const [paramVals,  setParamVals]  = useState<Record<string, number>>({});
  const [result,     setResult]     = useState<BacktestResult | null>(null);
  const [liveSignal, setLiveSignal] = useState<LiveSignal | null>(null);
  const [running,    setRunning]    = useState(false);
  const [loadSig,    setLoadSig]    = useState(false);
  const [error,      setError]      = useState<string | null>(null);
  const [tradeFilter, setTradeFilter] = useState<'ALL'|'WIN'|'LOSS'>('ALL');
  const liveTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    const defs = STRATEGY_DEFS[strategy]?.params ?? {};
    setParamVals(Object.fromEntries(Object.entries(defs).map(([k, v]) => [k, v.default])));
  }, [strategy]);

  useEffect(() => {
    clearTimeout(liveTimer.current);
    liveTimer.current = setTimeout(async () => {
      setLoadSig(true);
      try { const r = await apiService.getLiveSignal(symbol, strategy, paramVals); if (r.data) setLiveSignal(r.data); }
      catch {}
      finally { setLoadSig(false); }
    }, 800);
    return () => clearTimeout(liveTimer.current);
  }, [symbol, strategy, JSON.stringify(paramVals)]);

  const handleRun = useCallback(async () => {
    setRunning(true); setError(null); setResult(null);
    try {
      const r = await apiService.runBacktest({ symbol, strategy, start_date: startDate, end_date: endDate, initial_capital: parseFloat(capital)||100_000, commission: (parseFloat(commission)||0.1)/100, params: paramVals });
      if (r.data) setResult(r.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Backtest failed');
    } finally { setRunning(false); }
  }, [symbol, strategy, startDate, endDate, capital, commission, paramVals]);

  const strat = STRATEGY_DEFS[strategy];
  const m = result?.metrics;
  const filteredTrades = result?.trades.filter(t => tradeFilter === 'ALL' ? true : t.type === tradeFilter) ?? [];

  // Shared input style
  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 12px', borderRadius: 8, fontSize: 13,
    background: isDark ? 'rgba(255,255,255,0.06)' : '#f8fafc',
    border: `1px solid ${isDark ? 'rgba(255,255,255,0.12)' : '#e2e8f0'}`,
    color: 'var(--nd-text-1)', outline: 'none',
  };
  const labelStyle: React.CSSProperties = {
    fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)',
    textTransform: 'uppercase', letterSpacing: '0.05em',
    marginBottom: 4, display: 'block',
  };

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">Backtesting & Day Trading</h1>
        <p className="nd-page-sub">AI-powered intraday simulation with live replay + historical strategy backtesting.</p>
      </div>

      {/* Tab switcher */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 24, background: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)', borderRadius: 12, padding: 4, width: 'fit-content' }}>
        {([['autopilot','AI Live Trading','auto_awesome'],['strategy','Strategy Backtest','timeline']] as const).map(([tab, label, icon]) => (
          <button key={tab} onClick={() => setPageTab(tab)} style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '8px 20px', borderRadius: 10, border: 'none',
            background: pageTab === tab ? 'var(--nd-primary)' : 'transparent',
            color: pageTab === tab ? '#000' : 'var(--nd-text-2)',
            fontWeight: pageTab === tab ? 700 : 500, fontSize: 13, cursor: 'pointer',
          }}>
            <span className="material-icons" style={{ fontSize: 16 }}>{icon}</span>
            {label}
          </button>
        ))}
      </div>

      {/* ══════════════════════════════════════════════════════════════════════ */}
      {/* TAB 1: AI LIVE TRADING                                               */}
      {/* ══════════════════════════════════════════════════════════════════════ */}
      {pageTab === 'autopilot' && (
        <div>
          {/* Config — only shown when no session yet */}
          {!apSession && !apRunning && (
            <div className="nd-card" style={{ padding: '20px 24px', marginBottom: 20 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-primary)' }}>settings</span>
                Configure AI Live Session
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-end' }}>
                <div style={{ flex: '1 1 130px' }}>
                  <label style={labelStyle}>Stock</label>
                  <select value={apSymbol} onChange={e => setApSymbol(e.target.value)} style={inputStyle}>
                    <optgroup label="My Portfolio">
                      {PORTFOLIO_STOCKS.map(s => <option key={s}>{s}</option>)}
                    </optgroup>
                    <optgroup label="NSE Stocks">
                      {ALL_STOCKS.filter(s => !PORTFOLIO_STOCKS.includes(s)).map(s => <option key={s}>{s}</option>)}
                    </optgroup>
                  </select>
                </div>
                <div style={{ flex: '1 1 150px' }}>
                  <label style={labelStyle}>Trading Date</label>
                  <input type="date" value={apDate} onChange={e => setApDate(e.target.value)}
                    style={inputStyle} max={new Date().toISOString().slice(0,10)} />
                </div>
                <div style={{ flex: '1 1 130px' }}>
                  <label style={labelStyle}>Start Watching From</label>
                  <select value={apStartTime} onChange={e => setApStartTime(e.target.value)} style={inputStyle}>
                    {TIME_OPTIONS.map(t => (
                      <option key={t} value={t}>{t} IST</option>
                    ))}
                  </select>
                </div>
                <div style={{ flex: '1 1 120px' }}>
                  <label style={labelStyle}>Capital (₹)</label>
                  <input type="number" value={apCapital} onChange={e => setApCapital(e.target.value)}
                    style={inputStyle} min={5000} step={5000} />
                </div>
                <div style={{ flex: '0 0 auto' }}>
                  <button onClick={handleAutopilot} style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '10px 28px',
                    background: 'var(--nd-primary)', color: '#000', border: 'none', borderRadius: 10,
                    fontWeight: 700, fontSize: 14, cursor: 'pointer',
                  }}>
                    <span className="material-icons" style={{ fontSize: 18 }}>play_arrow</span>
                    Start Live Session
                  </button>
                </div>
              </div>

              {apError && (
                <div style={{ marginTop: 12, padding: '10px 14px', borderRadius: 8, background: '#fef2f2', color: '#dc2626', fontSize: 12, border: '1px solid #fecaca' }}>
                  {apError}
                </div>
              )}
            </div>
          )}

          {/* Loading */}
          {apRunning && (
            <div className="nd-card" style={{ padding: 52, textAlign: 'center' }}>
              <div style={{ fontSize: 44, marginBottom: 16 }}>🤖</div>
              <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>Loading intraday data for {apSymbol}…</div>
              <div style={{ fontSize: 13, color: 'var(--nd-text-3)', marginBottom: 20 }}>
                Fetching 5-min candles for {apDate} · Preparing live replay session
              </div>
              <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                {[0,1,2].map(i => (
                  <div key={i} style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--nd-primary)', animation: `nd-pulse ${0.6+i*0.2}s ease-in-out infinite alternate` }} />
                ))}
              </div>
            </div>
          )}

          {/* Live Replay */}
          {apSession && !apRunning && (
            <LiveReplayView
              initialData={apSession.initialData}
              symbol={apSession.symbol}
              date={apSession.date}
              capital={apSession.capital}
              theme={theme}
              onReset={() => setApSession(null)}
            />
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════ */}
      {/* TAB 2: STRATEGY BACKTEST                                             */}
      {/* ══════════════════════════════════════════════════════════════════════ */}
      {pageTab === 'strategy' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
          <div className="nd-card lg:col-span-1 p-6" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <h2 className="nd-section-title" style={{ margin: 0 }}>Configuration</h2>
            <div>
              <label className="nd-field-label">Stock</label>
              <select value={symbol} onChange={e => setSymbol(e.target.value)} className="nd-input">
                <optgroup label="My Portfolio">{PORTFOLIO_STOCKS.map(s => <option key={s}>{s}</option>)}</optgroup>
                <optgroup label="NSE Stocks">{ALL_STOCKS.filter(s => !PORTFOLIO_STOCKS.includes(s)).map(s => <option key={s}>{s}</option>)}</optgroup>
              </select>
            </div>
            <div>
              <label className="nd-field-label">Strategy</label>
              <select value={strategy} onChange={e => setStrategy(e.target.value)} className="nd-input">
                {Object.entries(STRATEGY_DEFS).map(([k, v]) => <option key={k} value={k}>{v.name}</option>)}
              </select>
              <p style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 4 }}>{strat?.description}</p>
            </div>
            {Object.entries(strat?.params ?? {}).map(([k, p]) => (
              <div key={k}>
                <label className="nd-field-label">{p.label}: <strong>{paramVals[k] ?? p.default}</strong></label>
                <input type="range" min={p.min} max={p.max} step={p.step} value={paramVals[k] ?? p.default}
                  onChange={e => setParamVals(prev => ({ ...prev, [k]: p.type === 'int' ? parseInt(e.target.value) : parseFloat(e.target.value) }))}
                  className="nd-slider" />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--nd-text-3)' }}>
                  <span>{p.min}</span><span>{p.max}</span>
                </div>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1 }}><label className="nd-field-label">From</label><input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="nd-input" /></div>
              <div style={{ flex: 1 }}><label className="nd-field-label">To</label><input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="nd-input" /></div>
            </div>
            <div><label className="nd-field-label">Initial Capital (₹)</label><input type="number" value={capital} onChange={e => setCapital(e.target.value)} className="nd-input" min={10000} step={10000} /></div>
            <div><label className="nd-field-label">Commission (%)</label><input type="number" value={commission} onChange={e => setCommission(e.target.value)} className="nd-input" min={0} max={5} step={0.01} /></div>
            <button onClick={handleRun} disabled={running} className="nd-btn nd-btn-primary" style={{ width: '100%', padding: '10px 0', fontWeight: 700, fontSize: 14 }}>
              {running ? 'Running…' : 'Run Backtest'}
            </button>
            {error && <div style={{ padding: 12, borderRadius: 8, background: '#fef2f2', color: '#dc2626', fontSize: 12, border: '1px solid #fecaca' }}>{error}</div>}
          </div>

          <div className="lg:col-span-2" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            <div className="nd-card p-6">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
                <h2 className="nd-section-title" style={{ margin: 0 }}>Live Signal — {symbol}</h2>
                {loadSig && <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Refreshing…</span>}
              </div>
              {liveSignal ? (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
                    <SignalBadge signal={liveSignal.signal} />
                    <div>
                      <span style={{ fontSize: 28, fontWeight: 800 }}>{inr(liveSignal.lastPrice)}</span>
                      <span style={{ fontSize: 12, color: 'var(--nd-text-3)', marginLeft: 8 }}>{liveSignal.strategy?.replace(/_/g,' ')} · {liveSignal.candleCount} daily candles</span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                    {Object.entries(liveSignal.indicators ?? {}).map(([k, v]) => v !== null && (
                      <div key={k} className="nd-metric" style={{ minWidth: 100, padding: 12 }}>
                        <p className="nd-metric-label" style={{ textTransform: 'uppercase', fontSize: 10 }}>{k.replace(/_/g,' ')}</p>
                        <p className="nd-metric-value" style={{ fontSize: 16 }}>{typeof v === 'number' ? v.toFixed(2) : String(v)}</p>
                      </div>
                    ))}
                  </div>
                  {liveSignal.recentSignals && (
                    <>
                      <p style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>Recent Signals</p>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {liveSignal.recentSignals.map((s: any, i: number) => (
                          <div key={i} style={{
                            padding: '6px 12px', borderRadius: 8, textAlign: 'center', minWidth: 80,
                            background: s.signal === 'BUY' ? '#dcfce7' : s.signal === 'SELL' ? '#fee2e2' : isDark ? 'rgba(255,255,255,0.06)' : '#f8fafc',
                            border: `1px solid ${s.signal === 'BUY' ? '#86efac' : s.signal === 'SELL' ? '#fca5a5' : isDark ? 'rgba(255,255,255,0.1)' : '#e2e8f0'}`,
                          }}>
                            <div style={{ fontWeight: 700, fontSize: 12, color: s.signal === 'BUY' ? '#16a34a' : s.signal === 'SELL' ? '#dc2626' : 'var(--nd-text-2)' }}>{s.signal}</div>
                            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{s.date?.slice(5)}</div>
                            <div style={{ fontSize: 10, color: 'var(--nd-text-2)', fontWeight: 600 }}>{inr(s.close)}</div>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                  <p style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 12 }}>Paper trading only — no real orders are placed.</p>
                </>
              ) : (
                <div style={{ textAlign: 'center', padding: 24, color: 'var(--nd-text-3)' }}>Loading signal…</div>
              )}
            </div>

            {m && result && (
              <>
                <div className="nd-card p-6">
                  <h2 className="nd-section-title" style={{ margin: '0 0 16px' }}>Results — {result.strategyName} on {result.symbol}</h2>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 0 }}>
                    <MCard label="Total Return" value={pct(m.totalReturnPct)} color={m.totalReturnPct >= 0 ? 'green' : 'red'} sub={`vs B&H ${pct(m.buyHoldReturnPct)}`} />
                    <MCard label="CAGR" value={pct(m.cagr)} color={m.cagr >= 0 ? 'green' : 'red'} />
                    <MCard label="Sharpe Ratio" value={m.sharpeRatio.toFixed(3)} color={m.sharpeRatio >= 1 ? 'green' : m.sharpeRatio >= 0 ? 'yellow' : 'red'} />
                    <MCard label="Max Drawdown" value={`-${m.maxDrawdownPct.toFixed(2)}%`} color="red" />
                    <MCard label="Win Rate" value={`${m.winRate.toFixed(1)}%`} color={m.winRate >= 50 ? 'green' : 'red'} sub={`${m.winningTrades}W / ${m.losingTrades}L`} />
                    <MCard label="Profit Factor" value={m.profitFactor.toFixed(2)} color={m.profitFactor >= 1 ? 'green' : 'red'} />
                    <MCard label="Total Trades" value={String(m.totalTrades)} sub={`Avg hold ${m.avgHoldingDays}d`} />
                    <MCard label="Final Value" value={inr(m.finalValue)} sub={`Started: ${inr(m.initialCapital)}`} />
                  </div>
                </div>
                {result.equityCurve && (
                  <div className="nd-card p-6">
                    <h2 className="nd-section-title" style={{ margin: '0 0 16px' }}>Equity Curve</h2>
                    <EquityCurve curve={result.equityCurve} initialCapital={m.initialCapital} trades={result.trades} theme={theme} />
                  </div>
                )}
                {result.trades.length > 0 && (
                  <div className="nd-card p-6">
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
                      <h2 className="nd-section-title" style={{ margin: 0 }}>Trade Log ({filteredTrades.length})</h2>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {(['ALL','WIN','LOSS'] as const).map(f => (
                          <button key={f} onClick={() => setTradeFilter(f)} style={{
                            padding: '4px 12px', borderRadius: 20, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                            background: tradeFilter === f ? 'var(--nd-primary)' : isDark ? 'rgba(255,255,255,0.06)' : '#f1f5f9',
                            color: tradeFilter === f ? '#000' : 'var(--nd-text-2)',
                          }}>{f}</button>
                        ))}
                      </div>
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                          <tr style={{ color: 'var(--nd-text-3)', borderBottom: '1px solid var(--nd-border)' }}>
                            {['Entry','Exit','Entry ₹','Exit ₹','Shares','P&L','P&L %','Hold','Result'].map(h => (
                              <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {filteredTrades.map((t, i) => (
                            <tr key={i} style={{ borderBottom: '1px solid var(--nd-border)', color: 'var(--nd-text-2)' }}>
                              <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>{t.entryDate}</td>
                              <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>{t.exitDate}</td>
                              <td style={{ padding: '6px 10px' }}>{inr(t.entryPrice)}</td>
                              <td style={{ padding: '6px 10px' }}>{inr(t.exitPrice)}</td>
                              <td style={{ padding: '6px 10px' }}>{t.shares}</td>
                              <td style={{ padding: '6px 10px', fontWeight: 600, color: t.pnl >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{t.pnl >= 0 ? '+' : ''}{inr(t.pnl)}</td>
                              <td style={{ padding: '6px 10px', color: t.pnlPct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{pct(t.pnlPct)}</td>
                              <td style={{ padding: '6px 10px' }}>{t.holdingDays}d</td>
                              <td style={{ padding: '6px 10px' }}>
                                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 20, background: t.type === 'WIN' ? '#dcfce7' : '#fee2e2', color: t.type === 'WIN' ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{t.type}</span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default BacktestPage;
