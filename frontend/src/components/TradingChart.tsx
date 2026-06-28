import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  createChart, ColorType, CrosshairMode, IChartApi, ISeriesApi, UTCTimestamp,
} from 'lightweight-charts';
import apiService from '../services/api';

/**
 * The ONE trading chart used across the app (Live Sessions, Orders, …).
 * Any visual or behavioural enhancement made here reflects everywhere.
 *
 * Advanced terminal features:
 *  - Overlays: VWAP, SMA5/SMA20, Bollinger Bands (toggleable).
 *  - Live crosshair legend: OHLC + change% + indicator values at the cursor.
 *
 * Data sources (flexible):
 *  - pass `candles` directly, OR give `symbol` + `date` and it fetches them.
 *  - pass `markers` directly, OR give round-trip `trades`.
 */
export interface ChartCandle {
  time?: string; timestamp: number;
  open: number; high: number; low: number; close: number; volume?: number;
}
export interface TradeMarker {
  timestamp: number; action: 'BUY' | 'SELL'; price?: number; text?: string;
}
export interface RoundTripTrade {
  timestampOpen?: string; timestampClose?: string; entryPrice?: number; exitPrice?: number;
}

const IST_OFFSET = 19800; // seconds — render timestamps in IST
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const p2 = (n: number) => String(n).padStart(2, '0');

function snap(candles: ChartCandle[], iso?: string): number | null {
  if (!iso || !candles.length) return null;
  const target = Math.floor(new Date(iso).getTime() / 1000);
  let best = candles[0].timestamp, bestD = Infinity;
  for (const c of candles) { const d = Math.abs(c.timestamp - target); if (d < bestD) { bestD = d; best = c.timestamp; } }
  return best;
}

// ── Indicator math ───────────────────────────────────────────────────────────
function sma(vals: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(vals.length).fill(null);
  let sum = 0;
  for (let i = 0; i < vals.length; i++) {
    sum += vals[i];
    if (i >= period) sum -= vals[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}
function bollinger(closes: number[], period = 20, mult = 2) {
  const mid = sma(closes, period);
  const upper: (number | null)[] = new Array(closes.length).fill(null);
  const lower: (number | null)[] = new Array(closes.length).fill(null);
  for (let i = period - 1; i < closes.length; i++) {
    const m = mid[i]; if (m == null) continue;
    let v = 0;
    for (let j = i - period + 1; j <= i; j++) v += (closes[j] - m) ** 2;
    const sd = Math.sqrt(v / period);
    upper[i] = m + mult * sd; lower[i] = m - mult * sd;
  }
  return { mid, upper, lower };
}
function vwapSeries(rows: ChartCandle[]): (number | null)[] {
  const out: (number | null)[] = new Array(rows.length).fill(null);
  let cumPV = 0, cumV = 0, cumTP = 0, n = 0;
  for (let i = 0; i < rows.length; i++) {
    const c = rows[i];
    const tp = (c.high + c.low + c.close) / 3;
    const v = c.volume || 0;
    cumPV += tp * v; cumV += v; cumTP += tp; n += 1;
    // True VWAP when volume exists; fall back to cumulative typical-price average
    // (the captured tick dataset is price-only until volume enrichment).
    out[i] = cumV > 0 ? cumPV / cumV : cumTP / n;
  }
  return out;
}
function emaSeries(vals: number[], period: number): number[] {
  const out = new Array(vals.length).fill(NaN);
  const k = 2 / (period + 1);
  let prev = vals[0];
  for (let i = 0; i < vals.length; i++) { prev = i === 0 ? vals[0] : vals[i] * k + prev * (1 - k); out[i] = prev; }
  return out;
}
function rsiSeries(closes: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  let avgG = 0, avgL = 0;
  for (let i = 1; i < closes.length; i++) {
    const ch = closes[i] - closes[i - 1];
    const g = Math.max(0, ch), l = Math.max(0, -ch);
    if (i <= period) { avgG += g / period; avgL += l / period; if (i === period) out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL); }
    else { avgG = (avgG * (period - 1) + g) / period; avgL = (avgL * (period - 1) + l) / period; out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL); }
  }
  return out;
}
function macdSeries(closes: number[]) {
  const e12 = emaSeries(closes, 12), e26 = emaSeries(closes, 26);
  const macd = closes.map((_, i) => e12[i] - e26[i]);
  const signal = emaSeries(macd, 9);
  const hist = macd.map((m, i) => m - signal[i]);
  return { macd, signal, hist };
}

interface Props {
  candles?: ChartCandle[];
  prevDayCandles?: ChartCandle[];
  symbol?: string;
  date?: string;
  markers?: TradeMarker[];
  trades?: RoundTripTrade[];
  height?: number;
  isDark?: boolean;
  showControls?: boolean;          // overlay toggle chips (default true)
  subPanes?: boolean;              // RSI + MACD sub-panes (default true)
}

type Legend = {
  o: number; h: number; l: number; c: number; chg: number;
  vwap: number | null; sma5: number | null; sma20: number | null;
  rsi: number | null; macd: number | null;
} | null;

const RSI_H = 96, MACD_H = 96;

const TradingChart: React.FC<Props> = ({
  candles, prevDayCandles = [], symbol, date, markers, trades,
  height = 420, isDark = true, showControls = true, subPanes = true,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const rsiContRef = useRef<HTMLDivElement>(null);
  const macdContRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const macdChartRef = useRef<IChartApi | null>(null);
  const csRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const lineRef = useRef<ISeriesApi<'Line'> | null>(null);
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const vwapRef = useRef<ISeriesApi<'Line'> | null>(null);
  const sma5Ref = useRef<ISeriesApi<'Line'> | null>(null);
  const sma20Ref = useRef<ISeriesApi<'Line'> | null>(null);
  const bbuRef = useRef<ISeriesApi<'Line'> | null>(null);
  const bblRef = useRef<ISeriesApi<'Line'> | null>(null);
  const rsiRef = useRef<ISeriesApi<'Line'> | null>(null);
  const macdRef = useRef<ISeriesApi<'Line'> | null>(null);
  const macdSigRef = useRef<ISeriesApi<'Line'> | null>(null);
  const macdHistRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const syncingRef = useRef(false);
  const didFitRef = useRef(false);

  const [overlays, setOverlays] = useState({ vwap: true, sma: true, bb: false });
  const [chartType, setChartType] = useState<'candles' | 'line'>('candles');
  const [legend, setLegend] = useState<Legend>(null);

  // ── Candle source ──────────────────────────────────────────────────────────
  const [fetched, setFetched] = useState<ChartCandle[]>([]);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const shouldFetch = candles === undefined && !!symbol && !!date;

  useEffect(() => {
    if (!shouldFetch) return;
    let alive = true;
    setLoading(true); setNote(null);
    (async () => {
      try {
        const r = await apiService.getIntradayCandles(symbol!, date!);
        const cs = (r as any).data?.candles ?? [];
        if (!alive) return;
        setFetched(cs);
        if (!cs.length) setNote('No chart available for this date.');
      } catch {
        if (alive) setNote('Chart unavailable for this date.');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [shouldFetch, symbol, date]);

  const effCandles = candles !== undefined ? candles : fetched;

  const effMarkers = useMemo<TradeMarker[]>(() => {
    if (markers) return markers;
    if (!trades) return [];
    const out: TradeMarker[] = [];
    for (const t of trades) {
      const e = snap(effCandles, t.timestampOpen);
      const x = snap(effCandles, t.timestampClose);
      if (e) out.push({ timestamp: e, action: 'BUY', price: t.entryPrice, text: `BUY ₹${t.entryPrice?.toFixed(2)}` });
      if (x && t.exitPrice) out.push({ timestamp: x, action: 'SELL', price: t.exitPrice, text: `SELL ₹${t.exitPrice.toFixed(2)}` });
    }
    return out;
  }, [markers, trades, effCandles]);

  // Sorted/deduped rows + indicator series (memoised; legend uses these).
  const { rows, ind } = useMemo(() => {
    const seen = new Set<number>();
    const rows = [...prevDayCandles, ...effCandles]
      .filter(c => c && typeof c.timestamp === 'number')
      .sort((a, b) => a.timestamp - b.timestamp)
      .filter(c => { if (seen.has(c.timestamp)) return false; seen.add(c.timestamp); return true; });
    const closes = rows.map(c => c.close);
    const s5 = sma(closes, 5), s20 = sma(closes, 20), bb = bollinger(closes, 20, 2), vw = vwapSeries(rows);
    const rsi = rsiSeries(closes, 14), mac = macdSeries(closes);
    const byTime: Record<number, { vwap: number | null; sma5: number | null; sma20: number | null; rsi: number | null; macd: number | null }> = {};
    rows.forEach((c, i) => { byTime[c.timestamp + IST_OFFSET] = { vwap: vw[i], sma5: s5[i], sma20: s20[i], rsi: rsi[i], macd: isFinite(mac.macd[i]) ? mac.macd[i] : null }; });
    return { rows, ind: { s5, s20, bb, vw, rsi, mac, byTime } };
  }, [effCandles, prevDayCandles]);

  // ── Create chart once ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: isDark ? '#131722' : '#ffffff' },
        textColor: isDark ? '#b2b5be' : '#131722',
        fontSize: 11, fontFamily: "'Inter', sans-serif",
      },
      grid: {
        vertLines: { color: isDark ? 'rgba(42,46,57,0.6)' : 'rgba(0,0,0,0.06)', style: 1 },
        horzLines: { color: isDark ? 'rgba(42,46,57,0.6)' : 'rgba(0,0,0,0.06)', style: 1 },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: isDark ? 'rgba(42,46,57,0.8)' : 'rgba(0,0,0,0.12)', scaleMargins: { top: 0.1, bottom: 0.25 }, minimumWidth: 64 } as any,
      localization: {
        timeFormatter: (t: number) => {
          const d = new Date(t * 1000);
          return `${p2(d.getUTCDate())} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()} · ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())} IST`;
        },
      },
      timeScale: {
        borderColor: isDark ? 'rgba(42,46,57,0.8)' : 'rgba(0,0,0,0.12)',
        timeVisible: true, secondsVisible: false, rightOffset: 6,
        tickMarkFormatter: (t: number, tickType: number) => {
          const d = new Date(t * 1000);
          if (tickType < 3) return `${p2(d.getUTCDate())} ${MON[d.getUTCMonth()]}`;
          return `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
        },
      },
      width: containerRef.current.clientWidth,
      height,
    });
    chartRef.current = chart;
    csRef.current = chart.addCandlestickSeries({
      upColor: '#26a69a', downColor: '#ef5350',
      borderUpColor: '#26a69a', borderDownColor: '#ef5350',
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    });
    lineRef.current = chart.addLineSeries({ color: '#42a5f5', lineWidth: 2, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: true });
    volRef.current = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });

    // Overlay line series
    bbuRef.current  = chart.addLineSeries({ color: 'rgba(120,144,156,0.55)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    bblRef.current  = chart.addLineSeries({ color: 'rgba(120,144,156,0.55)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    vwapRef.current = chart.addLineSeries({ color: '#ab47bc', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    sma5Ref.current  = chart.addLineSeries({ color: '#42a5f5', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    sma20Ref.current = chart.addLineSeries({ color: '#ffa726', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });

    // Live crosshair legend
    chart.subscribeCrosshairMove((param: any) => {
      if (!param.time || !csRef.current) { setLegend(null); return; }
      const bar = param.seriesData.get(csRef.current);
      if (!bar) { setLegend(null); return; }
      const o = bar.open, h = bar.high, l = bar.low, c = bar.close;
      const i = (ind.byTime as any)[param.time as number] || {};
      setLegend({ o, h, l, c, chg: o ? ((c - o) / o) * 100 : 0, vwap: i.vwap ?? null, sma5: i.sma5 ?? null, sma20: i.sma20 ?? null, rsi: i.rsi ?? null, macd: i.macd ?? null });
    });

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        const w = containerRef.current.clientWidth;
        chart.applyOptions({ width: w });
        rsiChartRef.current?.applyOptions({ width: w });
        macdChartRef.current?.applyOptions({ width: w });
      }
    });
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, height]);

  // ── RSI + MACD sub-panes (separate charts, time-synced with the price pane) ──
  useEffect(() => {
    if (!subPanes || !rsiContRef.current || !macdContRef.current || !chartRef.current) return;
    const mainW = containerRef.current?.clientWidth ?? 600;
    const common = {
      layout: { background: { type: ColorType.Solid, color: isDark ? '#131722' : '#ffffff' }, textColor: isDark ? '#b2b5be' : '#131722', fontSize: 10, fontFamily: "'Inter', sans-serif" },
      grid: { vertLines: { color: isDark ? 'rgba(42,46,57,0.4)' : 'rgba(0,0,0,0.05)' }, horzLines: { color: isDark ? 'rgba(42,46,57,0.4)' : 'rgba(0,0,0,0.05)' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: isDark ? 'rgba(42,46,57,0.8)' : 'rgba(0,0,0,0.12)', minimumWidth: 64 } as any,
      timeScale: { borderColor: isDark ? 'rgba(42,46,57,0.8)' : 'rgba(0,0,0,0.12)', visible: false },
      handleScale: true, handleScroll: true, width: mainW,
    };
    const rsiChart = createChart(rsiContRef.current, { ...common, height: RSI_H } as any);
    const macdChart = createChart(macdContRef.current, { ...common, height: MACD_H, timeScale: { ...common.timeScale, visible: true, timeVisible: true } } as any);
    rsiChartRef.current = rsiChart; macdChartRef.current = macdChart;

    rsiRef.current = rsiChart.addLineSeries({ color: '#ab47bc', lineWidth: 1, priceLineVisible: false, lastValueVisible: true });
    rsiChart.priceScale('right').applyOptions({ autoScale: false } as any);
    rsiRef.current.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) } as any);
    macdHistRef.current = macdChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    macdRef.current = macdChart.addLineSeries({ color: '#42a5f5', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    macdSigRef.current = macdChart.addLineSeries({ color: '#ffa726', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

    // Time-sync all three panes (guard against feedback loops).
    const charts = [chartRef.current, rsiChart, macdChart];
    const unsubs: Array<() => void> = [];
    charts.forEach(src => {
      const ts = src.timeScale();
      const handler = (range: any) => {
        if (syncingRef.current || !range) return;
        syncingRef.current = true;
        charts.forEach(dst => { if (dst !== src) dst.timeScale().setVisibleLogicalRange(range); });
        syncingRef.current = false;
      };
      ts.subscribeVisibleLogicalRangeChange(handler);
      unsubs.push(() => ts.unsubscribeVisibleLogicalRangeChange(handler));
    });

    return () => { unsubs.forEach(u => u()); rsiChart.remove(); macdChart.remove(); rsiChartRef.current = null; macdChartRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, height, subPanes]);

  // ── Push data when candles / markers / overlays change ─────────────────────
  useEffect(() => {
    const cs = csRef.current, vol = volRef.current, chart = chartRef.current;
    if (!cs || !vol || !chart) return;

    const T = (ts: number) => (ts + IST_OFFSET) as UTCTimestamp;
    cs.setData(rows.map(c => ({ time: T(c.timestamp), open: c.open, high: c.high, low: c.low, close: c.close })));
    lineRef.current?.setData(chartType === 'line' ? rows.map(c => ({ time: T(c.timestamp), value: c.close })) as any : []);
    cs.applyOptions({ visible: chartType === 'candles' });
    vol.setData(rows.map(c => ({
      time: T(c.timestamp), value: c.volume || 0,
      color: c.close >= c.open ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.35)',
    })));

    const lineData = (arr: (number | null)[]) =>
      rows.map((c, i) => ({ time: T(c.timestamp), value: arr[i] })).filter(p => p.value != null) as any;

    vwapRef.current?.setData(overlays.vwap ? lineData(ind.vw) : []);
    sma5Ref.current?.setData(overlays.sma ? lineData(ind.s5) : []);
    sma20Ref.current?.setData(overlays.sma ? lineData(ind.s20) : []);
    bbuRef.current?.setData(overlays.bb ? lineData(ind.bb.upper) : []);
    bblRef.current?.setData(overlays.bb ? lineData(ind.bb.lower) : []);

    cs.setMarkers(
      [...effMarkers].sort((a, b) => a.timestamp - b.timestamp).map(m => ({
        time: T(m.timestamp),
        position: (m.action === 'BUY' ? 'belowBar' : 'aboveBar') as 'belowBar' | 'aboveBar',
        color: m.action === 'BUY' ? '#26a69a' : '#ef5350',
        shape: (m.action === 'BUY' ? 'arrowUp' : 'arrowDown') as 'arrowUp' | 'arrowDown',
        text: m.text || `${m.action}${m.price ? ' ₹' + m.price.toFixed(2) : ''}`,
      })),
    );

    if (!didFitRef.current && rows.length) { chart.timeScale().fitContent(); didFitRef.current = true; }
  }, [rows, ind, effMarkers, overlays, chartType]);

  // ── Push RSI / MACD data into the sub-panes ────────────────────────────────
  useEffect(() => {
    if (!subPanes) return;
    const T = (ts: number) => (ts + IST_OFFSET) as UTCTimestamp;
    const lineData = (arr: (number | null)[]) =>
      rows.map((c, i) => ({ time: T(c.timestamp), value: arr[i] })).filter(p => p.value != null && isFinite(p.value as number)) as any;
    rsiRef.current?.setData(lineData(ind.rsi));
    macdRef.current?.setData(lineData(ind.mac.macd));
    macdSigRef.current?.setData(lineData(ind.mac.signal));
    macdHistRef.current?.setData(rows.map((c, i) => ({
      time: T(c.timestamp), value: ind.mac.hist[i],
      color: ind.mac.hist[i] >= 0 ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
    })).filter(p => isFinite(p.value)) as any);
  }, [rows, ind, subPanes]);

  // Latest values for the static legend when not hovering
  const last = rows.length ? rows[rows.length - 1] : null;
  const lastInd = last ? (ind.byTime as any)[last.timestamp + IST_OFFSET] : null;
  const L = legend || (last ? {
    o: last.open, h: last.high, l: last.low, c: last.close,
    chg: last.open ? ((last.close - last.open) / last.open) * 100 : 0,
    vwap: lastInd?.vwap ?? null, sma5: lastInd?.sma5 ?? null, sma20: lastInd?.sma20 ?? null,
    rsi: lastInd?.rsi ?? null, macd: lastInd?.macd ?? null,
  } : null);

  const chip = (on: boolean, color: string, label: string, key: 'vwap' | 'sma' | 'bb') => (
    <button onClick={() => setOverlays(o => ({ ...o, [key]: !o[key] }))} style={{
      fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 6, cursor: 'pointer',
      border: `1px solid ${on ? color : 'var(--nd-border)'}`,
      background: on ? `${color}22` : 'transparent', color: on ? color : 'var(--nd-text-3)',
    }}>{label}</button>
  );

  const totalH = height + (subPanes ? RSI_H + MACD_H : 0);

  return (
    <div style={{ position: 'relative', width: '100%', height: totalH }}>
      <div ref={containerRef} style={{ width: '100%', height }} />

      {/* RSI + MACD sub-panes */}
      {subPanes && (
        <>
          <div style={{ position: 'relative' }}>
            <span style={{ position: 'absolute', top: 2, left: 10, zIndex: 3, fontSize: 9.5, fontWeight: 700, color: '#ab47bc', fontFamily: 'ui-monospace, monospace' }}>
              RSI 14 {L?.rsi != null ? L.rsi.toFixed(1) : ''}
            </span>
            <div ref={rsiContRef} style={{ width: '100%', height: RSI_H }} />
          </div>
          <div style={{ position: 'relative' }}>
            <span style={{ position: 'absolute', top: 2, left: 10, zIndex: 3, fontSize: 9.5, fontWeight: 700, color: '#42a5f5', fontFamily: 'ui-monospace, monospace' }}>
              MACD 12,26,9 {L?.macd != null ? L.macd.toFixed(2) : ''}
            </span>
            <div ref={macdContRef} style={{ width: '100%', height: MACD_H }} />
          </div>
        </>
      )}

      {/* Live crosshair legend (Bloomberg-style readout) */}
      {L && (
        <div style={{
          position: 'absolute', top: 8, left: 10, zIndex: 4, pointerEvents: 'none',
          fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 11,
          display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center',
          background: 'rgba(19,23,34,0.72)', border: '1px solid rgba(42,46,57,0.8)',
          borderRadius: 7, padding: '4px 9px', color: '#b2b5be',
        }}>
          <span>O<b style={{ color: '#d1d4dc' }}>{L.o?.toFixed(2)}</b></span>
          <span>H<b style={{ color: '#26a69a' }}>{L.h?.toFixed(2)}</b></span>
          <span>L<b style={{ color: '#ef5350' }}>{L.l?.toFixed(2)}</b></span>
          <span>C<b style={{ color: '#d1d4dc' }}>{L.c?.toFixed(2)}</b></span>
          <span style={{ color: L.chg >= 0 ? '#26a69a' : '#ef5350' }}>{L.chg >= 0 ? '+' : ''}{L.chg.toFixed(2)}%</span>
          {overlays.vwap && L.vwap != null && <span style={{ color: '#ab47bc' }}>VWAP {L.vwap.toFixed(2)}</span>}
          {overlays.sma && L.sma5 != null && <span style={{ color: '#42a5f5' }}>SMA5 {L.sma5.toFixed(2)}</span>}
          {overlays.sma && L.sma20 != null && <span style={{ color: '#ffa726' }}>SMA20 {L.sma20.toFixed(2)}</span>}
        </div>
      )}

      {/* Overlay toggle chips */}
      {showControls && (
        <div style={{ position: 'absolute', top: 8, right: 12, zIndex: 4, display: 'flex', gap: 5 }}>
          <button onClick={() => setChartType(t => (t === 'candles' ? 'line' : 'candles'))}
            title={chartType === 'candles' ? 'Switch to line' : 'Switch to candles'}
            style={{ fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 6, cursor: 'pointer', border: '1px solid var(--nd-border)', background: 'transparent', color: 'var(--nd-text-2)', display: 'flex', alignItems: 'center', gap: 3 }}>
            <span className="material-icons" style={{ fontSize: 12 }}>{chartType === 'candles' ? 'candlestick_chart' : 'show_chart'}</span>
          </button>
          {chip(overlays.vwap, '#ab47bc', 'VWAP', 'vwap')}
          {chip(overlays.sma, '#42a5f5', 'SMA', 'sma')}
          {chip(overlays.bb, '#78909c', 'BB', 'bb')}
        </div>
      )}

      {(loading || effCandles.length === 0) && (
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--nd-text-3)', fontSize: 13, pointerEvents: 'none', textAlign: 'center', padding: 12 }}>
          {loading ? 'Loading chart…' : (note || 'No chart data')}
        </div>
      )}
    </div>
  );
};

export default TradingChart;
