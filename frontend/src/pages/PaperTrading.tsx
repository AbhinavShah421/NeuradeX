import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  createChart, ColorType, CrosshairMode, IChartApi, UTCTimestamp,
} from 'lightweight-charts';
import { useAppStore } from '../stores/appStore';
import apiService from '../services/api';

const inr = (v: number) =>
  `₹${Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const IST_OFFSET = 19800; // UTC+5:30 in seconds

const STOCKS = [
  'SBIN','IDBI','SUZLON','INDUSINDBK','TMPV','PNB','FEDERALBNK','TMCV',
  'IREDA','ZEEL','IOB','JKTYRE','RELIANCE','TCS','INFY','HDFCBANK',
  'ICICIBANK','BAJFINANCE','WIPRO','KOTAKBANK','TATAMOTORS','MARUTI','SUNPHARMA',
];

// ── Candle aggregation ─────────────────────────────────────────────────────────
interface IntradayCandle {
  time: string; timestamp: number;
  open: number; high: number; low: number; close: number; volume: number;
}
interface DayTrade {
  time: string; timestamp: number; action: string; price: number; quantity: number;
  confidence: number; reason: string; pnl: number | null; pnlPct: number | null;
  candleIndex: number; indicators: Record<string, any>;
}

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

function visibleBarsToFactor(bars: number) {
  if (bars <= 40)  return 1;
  if (bars <= 80)  return 2;
  if (bars <= 150) return 3;
  return 6;
}

function tfLabel(f: number) {
  return f === 1 ? '5m' : f === 2 ? '10m' : f === 3 ? '15m' : '30m';
}

// Stable chart canvas — never re-renders on parent state changes
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
//  LIVE TRADING VIEW — real-time AI paper trading against live Groww data
// ═══════════════════════════════════════════════════════════════════════════════
const LiveTradingView: React.FC<{
  initialData: any;
  symbol: string;
  capital: number;
  theme: string;
  onReset: () => void;
}> = ({ initialData, symbol, capital, theme, onReset }) => {
  const isDark = theme === 'dark';

  // ── Chart refs ──────────────────────────────────────────────────────────────
  const chartRef       = useRef<IChartApi | null>(null);
  const csRef          = useRef<any>(null);
  const volRef         = useRef<any>(null);
  const containerRef   = useRef<HTMLDivElement>(null);
  const stepTimeoutRef = useRef<ReturnType<typeof setTimeout>>();
  const tickIntervalRef = useRef<ReturnType<typeof setInterval>>();

  // ── Session refs ────────────────────────────────────────────────────────────
  const candlesRef         = useRef<IntradayCandle[]>(initialData.candles);
  const currentIdxRef      = useRef<number>(initialData.candles.length - 1);
  const aggFactorRef       = useRef(1);
  const stepPendingRef     = useRef(false);
  const tickPendingRef     = useRef(false);
  const positionRef        = useRef<DayTrade | null>(null);
  const revealedTradesRef  = useRef<{ candleIndex: number; action: string; price: number }[]>([]);
  const cashRef            = useRef<number>(initialData.cash);
  const tradesRef          = useRef<any[]>(initialData.trades);
  const isSessionEndedRef  = useRef<boolean>(initialData.isSessionEnded);
  const sessionPositionRef = useRef<any>(initialData.position);
  const secsUntilNextRef   = useRef<number>(initialData.secsUntilNext ?? 0);
  const lastStepAtRef      = useRef<number>(Date.now());
  const countdownElRef     = useRef<HTMLSpanElement>(null);
  const istClockElRef      = useRef<HTMLSpanElement>(null);
  const livePriceElRef     = useRef<HTMLSpanElement>(null);
  const liveChangeElRef    = useRef<HTMLSpanElement>(null);

  // ── React state (UI only) ──────────────────────────────────────────────────
  const [currentCandle,  setCurrentCandle]  = useState<IntradayCandle | null>(
    candlesRef.current[currentIdxRef.current] ?? null
  );
  const [currentIdx,     setCurrentIdx]     = useState<number>(initialData.candles.length - 1);
  const [isSessionEnded, setIsSessionEnded] = useState<boolean>(initialData.isSessionEnded);
  const [aggFactor,      setAggFactor]      = useState(1);
  const [position,       setPosition]       = useState<DayTrade | null>(null);
  const [closedTrades,   setClosedTrades]   = useState<DayTrade[]>([]);
  const [visibleDecs,    setVisibleDecs]    = useState<DayTrade[]>([]);
  const [chartType,      setChartType]      = useState<'candle' | 'line'>('candle');
  const [agentPending,   setAgentPending]   = useState(false);
  const [, setDataSource]  = useState<string>(initialData.dataSource ?? 'groww');
  // tick / signal state
  const [tickInterval,   setTickInterval]   = useState(2);            // seconds
  const [liveSignal,     setLiveSignal]     = useState<string>('HOLD');
  const [liveIndicators, setLiveIndicators] = useState<Record<string, any>>({});
  const [livePrice,      setLivePrice]      = useState<number>(0);
  const [orderConfirm,   setOrderConfirm]   = useState<{ action: string; qty: number } | null>(null);
  const [orderResult,    setOrderResult]    = useState<string | null>(null);
  const [orderLoading,   setOrderLoading]   = useState(false);
  const [quoteData,      setQuoteData]      = useState<{
    dayOpen?: number; dayHigh?: number; dayLow?: number; dayVolume?: number;
    bidPrice?: number; askPrice?: number;
    upperCircuit?: number; lowerCircuit?: number;
  }>({});

  const displayClose   = currentCandle?.close ?? 0;
  const sessionEnded   = isSessionEnded;
  const unrealPnl      = position ? (displayClose - position.price) * position.quantity : 0;
  const unrealPct      = position ? (unrealPnl / (position.price * position.quantity)) * 100 : 0;
  const totalClosedPnl = closedTrades.reduce((s, t) => s + (t.pnl ?? 0), 0);

  // Handle initial trade from start response (keys already camelCased)
  useEffect(() => {
    if (initialData.tradeExecuted) {
      const t   = initialData.tradeExecuted;
      const dec = initialData.agentDecision ?? {};
      const c   = initialData.latestCandle ?? initialData.candles?.[initialData.candles.length - 1];
      const idx = currentIdxRef.current;
      const dayTrade: DayTrade = {
        time: c.time, timestamp: c.timestamp ?? 0,
        action: t.action, price: t.price, quantity: t.quantity,
        confidence: dec.confidence, reason: dec.reason,
        pnl: null, pnlPct: null,
        candleIndex: idx, indicators: initialData.indicators ?? {},
      };
      if (t.action === 'BUY') {
        positionRef.current = dayTrade;
        setPosition(dayTrade);
        setVisibleDecs([dayTrade]);
        revealedTradesRef.current = [{ candleIndex: idx, action: 'BUY', price: t.price }];
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Chart marker helpers ───────────────────────────────────────────────────
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

  // ── Tick: poll LTP every N seconds, update chart + signal ────────────────
  const doTick = useCallback(async () => {
    if (tickPendingRef.current) return;
    tickPendingRef.current = true;
    try {
      const sp  = sessionPositionRef.current;
      const res = await apiService.paperTradingTick(symbol, {
        position:    sp?.status ?? 'NONE',
        entry_price: sp?.entryPrice ?? 0,
        quantity:    sp?.quantity   ?? 0,
      });
      const d = res.data;
      const price = d.price;

      // Update live price DOM refs (zero React re-renders)
      if (livePriceElRef.current)  livePriceElRef.current.textContent  = `₹${price.toFixed(2)}`;
      if (liveChangeElRef.current) {
        // changePct from backend may be a fraction (0.0014) or a percentage (0.14)
        // backend now returns it as percent directly; guard either way
        const raw  = d.changePct ?? 0;
        const pct  = Math.abs(raw) < 1 ? raw * 100 : raw;
        const sign = pct >= 0 ? '+' : '';
        liveChangeElRef.current.textContent = `${sign}${pct.toFixed(2)}%`;
        liveChangeElRef.current.style.color = pct >= 0 ? '#22c55e' : '#ef4444';
      }

      // Update chart from server-returned tick candles
      if (d.candles && d.candles.length > 0) {
        const prevLen = candlesRef.current.length;
        candlesRef.current = d.candles;
        const idx    = d.candles.length - 1;
        currentIdxRef.current = idx;
        const factor = aggFactorRef.current;

        if (prevLen === 0) {
          // First candles — full setData + fit
          csRef.current?.setData(buildAggCandles(d.candles, idx, factor));
          volRef.current?.setData(buildAggVolume(d.candles, idx, factor));
          chartRef.current?.timeScale().fitContent();
        } else if (d.candles.length > prevLen + 1) {
          // Multiple new bars added (e.g. first tick after Yahoo-seeded start) —
          // rebuild series but preserve viewport position
          csRef.current?.setData(buildAggCandles(d.candles, idx, factor));
          volRef.current?.setData(buildAggVolume(d.candles, idx, factor));
          applyMarkers(factor);
          chartRef.current?.timeScale().scrollToRealTime();
        } else {
          // 0 or 1 new bar — cheap incremental update
          const aggStart = Math.floor(idx / factor) * factor;
          const group    = d.candles.slice(aggStart, idx + 1);
          csRef.current?.update({
            time:  (d.candles[aggStart].timestamp + IST_OFFSET) as UTCTimestamp,
            open:  d.candles[aggStart].open,
            high:  Math.max(...group.map((g: IntradayCandle) => g.high)),
            low:   Math.min(...group.map((g: IntradayCandle) => g.low)),
            close: price,
          });
        }
        setCurrentIdx(idx);
        setCurrentCandle(d.candles[idx]);
      }

      setLivePrice(price);
      setLiveSignal(d.signal ?? 'HOLD');
      setLiveIndicators(d.indicators ?? {});
      setQuoteData({
        dayOpen:       d.dayOpen,
        dayHigh:       d.dayHigh,
        dayLow:        d.dayLow,
        dayVolume:     d.dayVolume,
        bidPrice:      d.bidPrice,
        askPrice:      d.askPrice,
        upperCircuit:  d.upperCircuit,
        lowerCircuit:  d.lowerCircuit,
      });
    } catch {
      // tick errors are silent — we just skip this tick
    } finally {
      tickPendingRef.current = false;
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Tick interval — starts on mount, restarts when interval changes ────────
  useEffect(() => {
    if (isSessionEndedRef.current) return;
    clearInterval(tickIntervalRef.current);
    doTick(); // immediate first fetch
    tickIntervalRef.current = setInterval(doTick, tickInterval * 1000);
    return () => clearInterval(tickIntervalRef.current);
  }, [tickInterval, doTick]);

  // ── Live step: fetch next real Groww candle + run agent ────────────────────
  const doLiveStep = useCallback(async () => {
    if (stepPendingRef.current || isSessionEndedRef.current) return;
    stepPendingRef.current = true;
    setAgentPending(true);
    try {
      const sp  = sessionPositionRef.current;
      const res = await apiService.paperTradingStep({
        symbol, capital,
        cash:        cashRef.current,
        position:    sp.status as 'NONE' | 'LONG',
        quantity:    sp.quantity ?? 0,
        entry_price: sp.entryPrice ?? 0,
        entry_time:  sp.entryTime ?? null,
        trades:      tradesRef.current,
      });
      const d = res.data;

      candlesRef.current = d.candles;
      const allCandles = candlesRef.current;
      const idx = allCandles.length - 1;
      const c   = d.latestCandle;
      currentIdxRef.current = idx;

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
      chartRef.current?.timeScale().scrollToRealTime();

      // Sync session state (keys already camelCased by axios interceptor)
      cashRef.current            = d.cash;
      tradesRef.current          = d.trades;
      isSessionEndedRef.current  = d.isSessionEnded ?? false;
      sessionPositionRef.current = d.position;
      secsUntilNextRef.current   = d.secsUntilNext ?? 300;
      lastStepAtRef.current      = Date.now();
      setDataSource(d.dataSource ?? 'groww');

      if (d.tradeExecuted) {
        const t         = d.tradeExecuted;
        const lastTrade = d.trades[d.trades.length - 1];
        const dayTrade: DayTrade = {
          time: c.time, timestamp: c.timestamp ?? 0,
          action: t.action, price: t.price, quantity: t.quantity,
          confidence: (d.agentDecision ?? d.agent_decision)?.confidence ?? 65,
          reason:     (d.agentDecision ?? d.agent_decision)?.reason     ?? '',
          pnl:    lastTrade?.pnl    ?? null,
          pnlPct: lastTrade?.pnlPct ?? lastTrade?.pnl_pct ?? null,
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

      setCurrentCandle(d.latestCandle);
      setCurrentIdx(idx);
      if (d.isSessionEnded) setIsSessionEnded(true);

      // Schedule next step
      if (!d.isSessionEnded) {
        const delay = Math.max(5_000, (d.secsUntilNext ?? 300) * 1000 + 1_000);
        clearTimeout(stepTimeoutRef.current);
        stepTimeoutRef.current = setTimeout(() => doLiveStep(), delay);
      }
    } catch (err) {
      console.error('Paper trading step failed:', err);
      // Retry after 30s on error
      clearTimeout(stepTimeoutRef.current);
      stepTimeoutRef.current = setTimeout(() => doLiveStep(), 30_000);
    } finally {
      stepPendingRef.current = false;
      setAgentPending(false);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Chart init ─────────────────────────────────────────────────────────────
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
      upColor:         '#26a69a', downColor:       '#ef5350',
      borderUpColor:   '#26a69a', borderDownColor: '#ef5350',
      wickUpColor:     '#26a69a', wickDownColor:   '#ef5350',
      borderVisible: true, wickVisible: true,
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
    applyMarkers(1);
    chart.timeScale().fitContent();

    let rafId: number | null = null;
    let lastAggSwitch = 0;
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      if (rafId !== null) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        const range = chart.timeScale().getVisibleLogicalRange();
        if (!range) return;
        if (Date.now() - lastAggSwitch < 400) return;
        const visibleBars = Math.max(1, Math.round(range.to - range.from));
        const newFactor   = visibleBarsToFactor(visibleBars);
        if (newFactor === aggFactorRef.current) return;
        lastAggSwitch = Date.now();
        aggFactorRef.current = newFactor;
        setAggFactor(newFactor);
        const idx = currentIdxRef.current;
        cs.setData(buildAggCandles(candlesRef.current, idx, newFactor));
        vs.setData(buildAggVolume(candlesRef.current, idx, newFactor));
        applyMarkers(newFactor);
      });
    });

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    ro.observe(containerRef.current);

    const el = containerRef.current;
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
      e.preventDefault();
      const ts    = chart.timeScale();
      const range = ts.getVisibleLogicalRange();
      if (!range) return;
      const pxPerBar = el.clientWidth / Math.max(1, range.to - range.from);
      const shift    = e.deltaX / pxPerBar;
      ts.setVisibleLogicalRange({ from: range.from + shift, to: range.to + shift });
    };
    el.addEventListener('wheel', onWheel, { passive: false });

    return () => {
      el.removeEventListener('wheel', onWheel);
      if (rafId !== null) cancelAnimationFrame(rafId);
      ro.disconnect();
      chart.remove();
    };
  }, [isDark, chartType]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Schedule first auto-step on mount ─────────────────────────────────────
  useEffect(() => {
    if (isSessionEndedRef.current) return;
    const delay = Math.max(5_000, (secsUntilNextRef.current) * 1000 + 1_000);
    lastStepAtRef.current = Date.now();
    stepTimeoutRef.current = setTimeout(() => doLiveStep(), delay);
    return () => clearTimeout(stepTimeoutRef.current);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Live IST clock & countdown — DOM writes only, zero React re-renders ──
  useEffect(() => {
    const id = setInterval(() => {
      // IST clock
      const ist = new Date(Date.now() + 19800 * 1000);
      if (istClockElRef.current) {
        istClockElRef.current.textContent =
          `${String(ist.getUTCHours()).padStart(2,'0')}:${String(ist.getUTCMinutes()).padStart(2,'0')}:${String(ist.getUTCSeconds()).padStart(2,'0')} IST`;
      }
      // Countdown to next candle
      if (countdownElRef.current) {
        if (isSessionEndedRef.current) { countdownElRef.current.textContent = ''; return; }
        const elapsed  = Date.now() - lastStepAtRef.current;
        const totalMs  = secsUntilNextRef.current * 1000;
        const remaining = Math.max(0, totalMs - elapsed);
        const secs      = Math.ceil(remaining / 1000);
        countdownElRef.current.textContent = secs > 1
          ? (secs >= 60 ? `next in ${Math.floor(secs/60)}m ${secs%60}s` : `next in ${secs}s`)
          : '';
      }
    }, 500);
    return () => clearInterval(id);
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
    <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>

      {/* ══ LEFT — Chart (60%) ═══════════════════════════════════════════════ */}
      <div className="nd-chart-col" style={{ flex: '0 0 60%', minWidth: 0, position: 'relative' }}>
        <StableChart containerRef={containerRef} isDark={isDark} />

        {/* Scroll arrows */}
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

        {/* ── Header card ────────────────────────────────────────────────────── */}
        <div className="nd-card" style={{ padding: '14px 18px' }}>

          {/* Row 1: Symbol + live price (DOM-written) */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 800, fontSize: 15 }}>{symbol}</span>
            <span ref={livePriceElRef} style={{ fontSize: 22, fontWeight: 900, color: 'var(--nd-text-1)' }}>
              ₹{(livePrice || displayClose).toFixed(2)}
            </span>
            <span ref={liveChangeElRef} style={{ fontSize: 13, fontWeight: 700, color: '#22c55e' }} />
            {currentCandle && <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{currentCandle.time} IST</span>}
            <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 20, background: isDark ? 'rgba(34,197,94,0.12)' : '#f0fdf4', color: '#22c55e', fontWeight: 700 }}>
              🟢 Groww Live
            </span>
          </div>

          {/* Row 1b: Quote strip — day O/H/L/V + bid/ask */}
          {(quoteData.dayOpen || quoteData.bidPrice) && (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 8, fontSize: 10 }}>
              {quoteData.dayOpen  && <span><span style={{ color: 'var(--nd-text-3)' }}>O </span><span style={{ fontWeight: 700 }}>₹{quoteData.dayOpen.toFixed(2)}</span></span>}
              {quoteData.dayHigh  && <span><span style={{ color: '#22c55e' }}>H </span><span style={{ fontWeight: 700 }}>₹{quoteData.dayHigh.toFixed(2)}</span></span>}
              {quoteData.dayLow   && <span><span style={{ color: '#ef4444' }}>L </span><span style={{ fontWeight: 700 }}>₹{quoteData.dayLow.toFixed(2)}</span></span>}
              {quoteData.dayVolume && <span><span style={{ color: 'var(--nd-text-3)' }}>Vol </span><span style={{ fontWeight: 700 }}>{quoteData.dayVolume.toLocaleString('en-IN')}</span></span>}
              {quoteData.bidPrice && <span style={{ borderLeft: `1px solid ${isDark ? 'rgba(255,255,255,0.1)' : '#e2e8f0'}`, paddingLeft: 10 }}><span style={{ color: '#22c55e' }}>Bid </span><span style={{ fontWeight: 700, color: '#22c55e' }}>₹{quoteData.bidPrice.toFixed(2)}</span></span>}
              {quoteData.askPrice && <span><span style={{ color: '#ef4444' }}>Ask </span><span style={{ fontWeight: 700, color: '#ef4444' }}>₹{quoteData.askPrice.toFixed(2)}</span></span>}
              {quoteData.upperCircuit && <span style={{ borderLeft: `1px solid ${isDark ? 'rgba(255,255,255,0.1)' : '#e2e8f0'}`, paddingLeft: 10 }}><span style={{ color: 'var(--nd-text-3)' }}>UC </span><span style={{ fontWeight: 600, color: '#22c55e' }}>₹{quoteData.upperCircuit.toFixed(2)}</span></span>}
              {quoteData.lowerCircuit && <span><span style={{ color: 'var(--nd-text-3)' }}>LC </span><span style={{ fontWeight: 600, color: '#ef4444' }}>₹{quoteData.lowerCircuit.toFixed(2)}</span></span>}
            </div>
          )}

          {/* Row 2: Chart controls + fetch rate + stop */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
            <div style={{ display: 'flex', gap: 3, background: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)', borderRadius: 8, padding: 3 }}>
              {(['candle', 'line'] as const).map(t => (
                <button key={t} onClick={() => setChartType(t)} style={{ padding: '4px 10px', borderRadius: 6, border: 'none', fontSize: 11, fontWeight: 600, cursor: 'pointer', background: chartType === t ? 'var(--nd-primary)' : 'transparent', color: chartType === t ? '#000' : 'var(--nd-text-2)' }}>
                  {t === 'candle' ? '🕯' : '📈'}
                </button>
              ))}
            </div>
            <span style={{ fontSize: 11, fontWeight: 800, padding: '3px 8px', borderRadius: 6, background: isDark ? 'rgba(99,102,241,0.2)' : '#ede9fe', color: '#6366f1' }}>{tfLabel(aggFactor)}</span>

            {/* Fetch rate selector */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 10, color: 'var(--nd-text-3)', whiteSpace: 'nowrap' }}>Fetch rate:</span>
              <div style={{ display: 'flex', gap: 2, background: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)', borderRadius: 8, padding: 2 }}>
                {([1, 2, 5, 10, 30] as const).map(s => (
                  <button key={s} onClick={() => setTickInterval(s)} style={{ padding: '3px 7px', borderRadius: 5, border: 'none', fontSize: 10, fontWeight: 700, cursor: 'pointer', background: tickInterval === s ? 'var(--nd-primary)' : 'transparent', color: tickInterval === s ? '#000' : 'var(--nd-text-2)' }}>{s}s</button>
                ))}
              </div>
            </div>

            <button onClick={onReset} style={{ padding: '4px 12px', borderRadius: 8, fontSize: 11, fontWeight: 600, cursor: 'pointer', border: `1px solid ${isDark ? 'rgba(255,255,255,0.12)' : '#e2e8f0'}`, background: 'transparent', color: 'var(--nd-text-3)', marginLeft: 'auto' }}>Stop</button>
          </div>

          {/* Row 3: Status dot + progress */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            {(() => {
              const col = sessionEnded ? '#9ca3af' : agentPending ? '#eab308' : '#22c55e';
              return (
                <>
                  <div style={{ width: 8, height: 8, borderRadius: '50%', background: col, boxShadow: !sessionEnded ? `0 0 6px ${col}` : 'none', animation: !sessionEnded ? 'nd-pulse 1.2s infinite' : 'none' }} />
                  <span style={{ fontSize: 11, fontWeight: 800, textTransform: 'uppercase', color: col }}>
                    {sessionEnded ? 'Market Closed' : agentPending ? 'AI Thinking…' : 'Live'}
                  </span>
                </>
              );
            })()}
          </div>
          <div style={{ height: 3, background: isDark ? 'rgba(255,255,255,0.06)' : '#f1f5f9', borderRadius: 2 }}>
            <div style={{ height: '100%', width: `${Math.min(100, (currentIdx / 374) * 100)}%`, background: 'var(--nd-primary)', borderRadius: 2, transition: 'width 0.25s linear' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 3, fontSize: 10, color: 'var(--nd-text-3)' }}>
            <span>09:15</span>
            <span style={{ display: 'flex', gap: 8 }}>
              <span ref={istClockElRef} style={{ color: '#22c55e', fontWeight: 700 }} />
              <span ref={countdownElRef} style={{ color: 'var(--nd-primary)', fontWeight: 700 }} />
            </span>
            <span>15:25</span>
          </div>
        </div>

        {/* ── Live Signal Card ────────────────────────────────────────────────── */}
        {!sessionEnded && (() => {
          const sigColor  = liveSignal === 'BUY' ? '#22c55e' : liveSignal === 'SELL' ? '#ef4444' : '#6b7280';
          const sigBg     = liveSignal === 'BUY' ? (isDark ? 'rgba(34,197,94,0.12)' : '#f0fdf4') : liveSignal === 'SELL' ? (isDark ? 'rgba(239,68,68,0.12)' : '#fef2f2') : (isDark ? 'rgba(255,255,255,0.04)' : '#f8fafc');
          const sp        = sessionPositionRef.current;
          const canBuy    = liveSignal === 'BUY'  && sp?.status === 'NONE';
          const canSell   = liveSignal === 'SELL' && sp?.status === 'LONG';
          const suggestQty = canBuy ? Math.max(1, Math.floor(cashRef.current * 0.95 / Math.max(1, livePrice || 1))) : (sp?.quantity ?? 0);
          return (
            <div className="nd-card" style={{ padding: '14px 16px', border: `1px solid ${sigColor}44`, background: sigBg }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 18, fontWeight: 900, color: sigColor, letterSpacing: '0.08em' }}>{liveSignal}</span>
                  <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>AI Technical Signal</span>
                </div>
                <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>every {tickInterval}s</span>
              </div>

              {/* Indicators row */}
              {Object.keys(liveIndicators).length > 0 && (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
                  {(['vwap','rsi','sma5','sma20','mom5'] as const).map(k => liveIndicators[k] !== undefined ? (
                    <div key={k} style={{ fontSize: 10 }}>
                      <span style={{ color: 'var(--nd-text-3)', textTransform: 'uppercase', fontWeight: 700 }}>{k} </span>
                      <span style={{ color: 'var(--nd-text-1)', fontWeight: 600 }}>
                        {k === 'rsi' ? Number(liveIndicators[k]).toFixed(1)
                          : k === 'mom5' ? `${Number(liveIndicators[k]) >= 0 ? '+' : ''}${Number(liveIndicators[k]).toFixed(2)}%`
                          : `₹${liveIndicators[k]}`}
                      </span>
                    </div>
                  ) : null)}
                  <div style={{ fontSize: 10 }}>
                    <span style={{ color: 'var(--nd-text-3)', textTransform: 'uppercase', fontWeight: 700 }}>vs VWAP </span>
                    <span style={{ color: liveIndicators.aboveVwap ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                      {liveIndicators.aboveVwap ? 'Above' : 'Below'}
                    </span>
                  </div>
                </div>
              )}

              {/* Order action buttons */}
              {(canBuy || canSell) && !orderConfirm && (
                <button onClick={() => setOrderConfirm({ action: liveSignal, qty: suggestQty })} style={{
                  width: '100%', padding: '10px', borderRadius: 8, border: 'none', cursor: 'pointer',
                  fontWeight: 800, fontSize: 14,
                  background: canBuy ? '#22c55e' : '#ef4444',
                  color: '#fff', boxShadow: `0 4px 12px ${canBuy ? '#22c55e' : '#ef4444'}44`,
                }}>
                  {canBuy ? `▲ Place BUY Order @ ₹${(livePrice || displayClose).toFixed(2)}` : `▼ Place SELL Order @ ₹${(livePrice || displayClose).toFixed(2)}`}
                </button>
              )}

              {/* Confirmation dialog */}
              {orderConfirm && !orderResult && (
                <div style={{ padding: '12px', borderRadius: 8, background: isDark ? 'rgba(0,0,0,0.3)' : 'rgba(0,0,0,0.06)', border: `1px solid ${orderConfirm.action === 'BUY' ? '#22c55e' : '#ef4444'}` }}>
                  <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
                    Confirm {orderConfirm.action} {orderConfirm.qty} × {symbol} @ MARKET
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 12 }}>
                    Estimated value: ₹{(orderConfirm.qty * (livePrice || displayClose)).toLocaleString('en-IN', { maximumFractionDigits: 0 })} · Product: MIS (Intraday)
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button
                      disabled={orderLoading}
                      onClick={async () => {
                        setOrderLoading(true);
                        try {
                          const r = await apiService.paperTradingPlaceOrder({
                            symbol, action: orderConfirm.action, quantity: orderConfirm.qty,
                            order_type: 'MARKET', product: 'MIS',
                          });
                          setOrderResult(`✅ Order placed! ID: ${r.data?.growwResponse?.orderId ?? r.data?.growwResponse?.order_id ?? 'submitted'}`);
                          setOrderConfirm(null);
                        } catch (e: any) {
                          setOrderResult(`❌ ${e?.response?.data?.detail ?? e?.message ?? 'Order failed'}`);
                          setOrderConfirm(null);
                        } finally {
                          setOrderLoading(false);
                        }
                      }}
                      style={{ flex: 1, padding: '8px', borderRadius: 7, border: 'none', cursor: orderLoading ? 'wait' : 'pointer', fontWeight: 700, fontSize: 12, background: orderConfirm.action === 'BUY' ? '#22c55e' : '#ef4444', color: '#fff' }}
                    >
                      {orderLoading ? 'Placing…' : `Confirm ${orderConfirm.action}`}
                    </button>
                    <button onClick={() => setOrderConfirm(null)} style={{ padding: '8px 14px', borderRadius: 7, border: `1px solid ${isDark ? 'rgba(255,255,255,0.15)' : '#e2e8f0'}`, background: 'transparent', cursor: 'pointer', fontSize: 12, color: 'var(--nd-text-2)' }}>Cancel</button>
                  </div>
                </div>
              )}

              {/* Order result toast */}
              {orderResult && (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', borderRadius: 8, background: isDark ? 'rgba(255,255,255,0.06)' : '#f8fafc', fontSize: 12 }}>
                  <span>{orderResult}</span>
                  <button onClick={() => setOrderResult(null)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--nd-text-3)', fontSize: 16, lineHeight: 1 }}>×</button>
                </div>
              )}
            </div>
          );
        })()}

        {/* ── Live Position Card ────────────────────────────────────────────── */}
        {position && (
          <div className="nd-card" style={{ padding: '18px 20px', border: '1px solid #22c55e44', boxShadow: '0 0 20px rgba(34,197,94,0.12)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#22c55e', boxShadow: '0 0 8px #22c55e', animation: 'nd-pulse 1.2s infinite' }} />
              <span style={{ fontWeight: 800, fontSize: 14, color: '#22c55e' }}>AI HOLDING — {symbol}</span>
              <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Entered at {position.time} IST</span>
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
            <div style={{ marginTop: 10, fontSize: 12, color: 'var(--nd-text-3)', fontStyle: 'italic' }}>🤖 Entry: {position.reason}</div>
          </div>
        )}

        {/* ── Post-Sell Observation ─────────────────────────────────────────── */}
        {closedTrades.length > 0 && !position && !sessionEnded && (() => {
          const lastSell     = closedTrades[closedTrades.length - 1];
          const currentPrice = displayClose > 0 ? displayClose : lastSell.price;
          const ifHeldPnl    = (currentPrice - lastSell.price) * lastSell.quantity;
          const ifHeldPct    = ((currentPrice - lastSell.price) / lastSell.price) * 100;
          const aiRight      = ifHeldPnl <= 0;
          return (
            <div className="nd-card" style={{ padding: '18px 20px', border: `1px solid ${isDark ? 'rgba(99,102,241,0.35)' : '#c7d2fe'}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
                <div style={{ width: 9, height: 9, borderRadius: '50%', background: '#6366f1', boxShadow: '0 0 8px #6366f1', animation: 'nd-pulse 1.4s infinite' }} />
                <span style={{ fontWeight: 800, fontSize: 14, color: '#6366f1' }}>Post-Sell Observation</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 16, marginBottom: 12 }}>
                {[
                  { label: 'AI Sold At',    value: `₹${lastSell.price.toFixed(2)}`, color: 'var(--nd-text-1)' },
                  { label: 'Current Price', value: `₹${currentPrice.toFixed(2)}`,   color: currentPrice >= lastSell.price ? '#ef4444' : '#22c55e' },
                  { label: 'If Still Held', value: `${ifHeldPnl >= 0 ? '+' : ''}${inr(ifHeldPnl)} (${ifHeldPct >= 0 ? '+' : ''}${ifHeldPct.toFixed(2)}%)`, color: ifHeldPnl >= 0 ? '#ef4444' : '#22c55e' },
                  { label: 'AI Call',       value: aiRight ? '✓ Correct Sell' : '✗ Sold Early', color: aiRight ? '#22c55e' : '#ef4444' },
                ].map(({ label, value, color }) => (
                  <div key={label}>
                    <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 3 }}>{label}</div>
                    <div style={{ fontSize: 15, fontWeight: 700, color }}>{value}</div>
                  </div>
                ))}
              </div>
            </div>
          );
        })()}

        {/* ── Session Complete ──────────────────────────────────────────────── */}
        {sessionEnded && (
          <div className="nd-card" style={{ padding: '18px 20px', borderLeft: `3px solid ${totalClosedPnl >= 0 ? '#22c55e' : '#ef4444'}` }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 12 }}>✅ Trading Session Complete</div>
            <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Total P&L</div>
                <div style={{ fontSize: 28, fontWeight: 900, color: totalClosedPnl >= 0 ? '#22c55e' : '#ef4444' }}>{totalClosedPnl >= 0 ? '+' : ''}{inr(totalClosedPnl)}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Trades</div>
                <div style={{ fontSize: 28, fontWeight: 900 }}>{closedTrades.length}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Win Rate</div>
                <div style={{ fontSize: 28, fontWeight: 900, color: '#22c55e' }}>
                  {closedTrades.length > 0 ? `${Math.round(closedTrades.filter(t => (t.pnl ?? 0) > 0).length / closedTrades.length * 100)}%` : '—'}
                </div>
              </div>
            </div>
          </div>
        )}

      </div>{/* end right col */}
    </div>{/* end 60/40 row */}

    {/* ── AI Decision Log ─────────────────────────────────────────────────── */}
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
                    <span style={{ padding: '2px 10px', borderRadius: 20, fontSize: 11, fontWeight: 700, background: isBuy ? '#22c55e22' : '#ef444422', color: isBuy ? '#22c55e' : '#ef4444' }}>{isBuy ? '▲ BUY' : '▼ SELL'}</span>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{t.time} IST</span>
                    <span style={{ color: 'var(--nd-text-2)', fontSize: 13 }}>@₹{t.price}</span>
                    <span style={{ color: 'var(--nd-text-3)', fontSize: 12 }}>Qty: {t.quantity}</span>
                    <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 20, background: isDark ? 'rgba(255,255,255,0.06)' : '#e2e8f0', color: 'var(--nd-text-3)' }}>Conf {t.confidence}%</span>
                  </div>
                  {t.pnl !== null && (
                    <span style={{ fontWeight: 800, fontSize: 14, color: tPnlPos ? '#22c55e' : '#ef4444' }}>
                      {tPnlPos ? '+' : ''}{inr(t.pnl)} ({tPnlPos ? '+' : ''}{(t.pnlPct ?? 0).toFixed(2)}%)
                    </span>
                  )}
                </div>
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.55, padding: '6px 10px', borderRadius: 6, background: isDark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.03)', borderLeft: `2px solid ${isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)'}` }}>
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
//  PAPER TRADING SETUP PAGE
// ═══════════════════════════════════════════════════════════════════════════════
const PaperTrading: React.FC = () => {
  const { theme } = useAppStore();
  const isDark = theme === 'dark';

  // ── Setup form state ───────────────────────────────────────────────────────
  const [ptSymbol,       setPtSymbol]       = useState('SBIN');
  const [ptCapital,      setPtCapital]      = useState('50000');
  const [loading,        setLoading]        = useState(false);
  const [error,          setError]          = useState('');
  const [waitingForData, setWaitingForData] = useState(false);
  const [retryCountdown, setRetryCountdown] = useState(0);
  const [marketStatus,   setMarketStatus]   = useState<any>(null);

  // ── Active session ─────────────────────────────────────────────────────────
  const [session, setSession] = useState<any | null>(null);

  // Check market status on mount
  useEffect(() => {
    apiService.paperTradingStatus()
      .then(r => setMarketStatus(r.data))
      .catch(() => {});
  }, []);

  // Auto-retry when waiting for Groww data — counts down then retries start
  const retryTimerRef = useRef<ReturnType<typeof setInterval>>();
  useEffect(() => {
    if (!waitingForData) { clearInterval(retryTimerRef.current); return; }
    setRetryCountdown(20);
    retryTimerRef.current = setInterval(() => {
      setRetryCountdown(n => {
        if (n <= 1) {
          clearInterval(retryTimerRef.current);
          setWaitingForData(false);
          setError('');
          // trigger start after state settles
          setTimeout(() => handleStartInner(), 50);
          return 0;
        }
        return n - 1;
      });
    }, 1000);
    return () => clearInterval(retryTimerRef.current);
  }, [waitingForData]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleStartInner = async () => {
    setLoading(true);
    setError('');
    try {
      const r = await apiService.paperTradingStart({
        symbol:  ptSymbol,
        capital: parseFloat(ptCapital) || 50_000,
      });
      const d = r.data;
      setSession({
        ...d,
        isSessionEnded: d.isSessionEnded ?? false,
        secsUntilNext:  d.secsUntilNext  ?? 60,
        dataSource:     d.dataSource     ?? 'groww',
      });
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? 'Failed to start session');
    } finally {
      setLoading(false);
    }
  };

  const handleStart = () => {
    setWaitingForData(false);
    setError('');
    handleStartInner();
  };

  if (session) {
    return (
      <div style={{ maxWidth: 1400, margin: '0 auto', padding: '0 20px' }}>
        <LiveTradingView
          initialData={session}
          symbol={ptSymbol}
          capital={parseFloat(ptCapital) || 50_000}
          theme={theme}
          onReset={() => setSession(null)}
        />
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 560, margin: '40px auto', padding: '0 20px' }}>
      <div className="nd-card" style={{ padding: '32px 36px' }}>

        {/* Header */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <span className="material-icons" style={{ fontSize: 24, color: 'var(--nd-primary)' }}>show_chart</span>
            <h2 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Paper Trading</h2>
          </div>
          <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-3)', lineHeight: 1.5 }}>
            Live AI-assisted day trading against real Groww market data. No real money — practice only.
          </p>
        </div>

        {/* Market status banner */}
        {marketStatus && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
            borderRadius: 8, marginBottom: 20,
            background: marketStatus.isMarketOpen
              ? (isDark ? 'rgba(34,197,94,0.1)' : '#f0fdf4')
              : (isDark ? 'rgba(239,68,68,0.1)' : '#fef2f2'),
            border: `1px solid ${marketStatus.isMarketOpen ? '#22c55e44' : '#ef444444'}`,
          }}>
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: marketStatus.isMarketOpen ? '#22c55e' : '#ef4444', boxShadow: marketStatus.isMarketOpen ? '0 0 6px #22c55e' : 'none', animation: marketStatus.isMarketOpen ? 'nd-pulse 1.4s infinite' : 'none' }} />
            <span style={{ fontSize: 13, fontWeight: 600, color: marketStatus.isMarketOpen ? '#22c55e' : '#ef4444' }}>
              {marketStatus.marketStatus === 'open'        ? `Market Open — ${marketStatus.istNow} IST`
              : marketStatus.marketStatus === 'pre_market' ? `Pre-Market — opens at 09:15 IST (now ${marketStatus.istNow})`
              : marketStatus.marketStatus === 'weekend'    ? 'Weekend — market closed'
              :                                              `Market Closed — ${marketStatus.istNow} IST`}
            </span>
          </div>
        )}

        {/* Form */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--nd-text-2)', marginBottom: 6 }}>Stock Symbol</label>
            <select
              value={ptSymbol}
              onChange={e => setPtSymbol(e.target.value)}
              style={{ width: '100%', padding: '10px 12px', borderRadius: 8, border: `1px solid ${isDark ? 'rgba(255,255,255,0.12)' : '#e2e8f0'}`, background: isDark ? 'rgba(255,255,255,0.05)' : '#fff', color: 'var(--nd-text-1)', fontSize: 14 }}
            >
              {STOCKS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--nd-text-2)', marginBottom: 6 }}>Capital (₹)</label>
            <input
              type="number" min="5000" step="5000"
              value={ptCapital}
              onChange={e => setPtCapital(e.target.value)}
              style={{ width: '100%', padding: '10px 12px', borderRadius: 8, border: `1px solid ${isDark ? 'rgba(255,255,255,0.12)' : '#e2e8f0'}`, background: isDark ? 'rgba(255,255,255,0.05)' : '#fff', color: 'var(--nd-text-1)', fontSize: 14, boxSizing: 'border-box' }}
            />
          </div>

          <div style={{ padding: '12px 14px', borderRadius: 8, background: isDark ? 'rgba(255,255,255,0.03)' : '#f8fafc', border: `1px solid ${isDark ? 'rgba(255,255,255,0.06)' : '#e2e8f0'}`, fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
            <strong style={{ color: 'var(--nd-text-2)' }}>How it works:</strong> Real 1-minute candles are fetched live from Groww. The AI makes a BUY/SELL/HOLD decision every minute and auto-advances — no play/pause needed.
          </div>

          {waitingForData && (
            <div style={{ padding: '14px 16px', borderRadius: 8, background: isDark ? 'rgba(234,179,8,0.1)' : '#fefce8', border: '1px solid #eab30844', color: isDark ? '#fde68a' : '#92400e' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#eab308', animation: 'nd-pulse 1.2s infinite' }} />
                <span style={{ fontWeight: 700, fontSize: 13 }}>Waiting for Groww market data…</span>
              </div>
              <div style={{ fontSize: 12, opacity: 0.85, marginBottom: 10 }}>
                Groww hasn't published 1-minute candle data yet for {ptSymbol}. This is normal in the first few minutes after market open. Auto-retrying in {retryCountdown}s.
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={() => { clearInterval(retryTimerRef.current); setWaitingForData(false); handleStartInner(); }}
                  style={{ padding: '6px 14px', borderRadius: 7, border: '1px solid #eab308', background: 'transparent', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: isDark ? '#fde68a' : '#92400e' }}>
                  Retry Now
                </button>
                <button onClick={() => { clearInterval(retryTimerRef.current); setWaitingForData(false); setError(''); }}
                  style={{ padding: '6px 14px', borderRadius: 7, border: `1px solid ${isDark ? 'rgba(255,255,255,0.12)' : '#e2e8f0'}`, background: 'transparent', cursor: 'pointer', fontSize: 12, color: 'var(--nd-text-3)' }}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          {error && !waitingForData && (
            <div style={{ padding: '10px 14px', borderRadius: 8, background: isDark ? 'rgba(239,68,68,0.1)' : '#fef2f2', border: '1px solid #ef444444', color: '#ef4444', fontSize: 13 }}>
              {error}
            </div>
          )}

          <button
            onClick={handleStart}
            disabled={loading}
            style={{
              padding: '12px 24px', borderRadius: 10, border: 'none', cursor: loading ? 'wait' : 'pointer',
              fontWeight: 700, fontSize: 14,
              background: loading ? (isDark ? 'rgba(255,255,255,0.08)' : '#e2e8f0') : 'var(--nd-primary)',
              color: loading ? 'var(--nd-text-3)' : '#000',
              transition: 'opacity 0.15s',
            }}
          >
            {loading ? 'Starting session…' : 'Start Live Paper Trading'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default PaperTrading;
