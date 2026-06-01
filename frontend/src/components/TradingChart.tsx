import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  createChart, ColorType, CrosshairMode, IChartApi, ISeriesApi, UTCTimestamp,
} from 'lightweight-charts';
import apiService from '../services/api';

/**
 * The ONE trading chart used across the app (Live Sessions, Orders, …).
 * Any visual or behavioural enhancement made here reflects everywhere.
 *
 * Data sources (flexible):
 *  - pass `candles` directly, OR give `symbol` + `date` and it fetches them.
 *  - pass `markers` directly, OR give round-trip `trades` and it derives
 *    BUY (entry) / SELL (exit) markers snapped to the nearest candle.
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

interface Props {
  candles?: ChartCandle[];          // explicit candles (overrides fetch)
  prevDayCandles?: ChartCandle[];
  symbol?: string;                  // fetch source: symbol + date
  date?: string;                    // YYYY-MM-DD
  markers?: TradeMarker[];          // explicit markers (overrides trades)
  trades?: RoundTripTrade[];        // derive entry/exit markers from round-trips
  height?: number;
  isDark?: boolean;
}

const TradingChart: React.FC<Props> = ({
  candles, prevDayCandles = [], symbol, date, markers, trades, height = 420, isDark = true,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const csRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const didFitRef = useRef(false);

  // ── Candle source: explicit prop, or fetch by symbol + date ────────────────
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

  // ── Markers: explicit, or derived from round-trip trades ───────────────────
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
      rightPriceScale: { borderColor: isDark ? 'rgba(42,46,57,0.8)' : 'rgba(0,0,0,0.12)', scaleMargins: { top: 0.1, bottom: 0.25 } },
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
    volRef.current = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, height]);

  // ── Push data when candles / markers change ────────────────────────────────
  useEffect(() => {
    const cs = csRef.current, vol = volRef.current, chart = chartRef.current;
    if (!cs || !vol || !chart) return;

    const seen = new Set<number>();
    const rows = [...prevDayCandles, ...effCandles]
      .filter(c => c && typeof c.timestamp === 'number')
      .sort((a, b) => a.timestamp - b.timestamp)
      .filter(c => { if (seen.has(c.timestamp)) return false; seen.add(c.timestamp); return true; });

    cs.setData(rows.map(c => ({ time: (c.timestamp + IST_OFFSET) as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })));
    vol.setData(rows.map(c => ({
      time: (c.timestamp + IST_OFFSET) as UTCTimestamp,
      value: c.volume || 0,
      color: c.close >= c.open ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.35)',
    })));

    cs.setMarkers(
      [...effMarkers].sort((a, b) => a.timestamp - b.timestamp).map(m => ({
        time: (m.timestamp + IST_OFFSET) as UTCTimestamp,
        position: (m.action === 'BUY' ? 'belowBar' : 'aboveBar') as 'belowBar' | 'aboveBar',
        color: m.action === 'BUY' ? '#26a69a' : '#ef5350',
        shape: (m.action === 'BUY' ? 'arrowUp' : 'arrowDown') as 'arrowUp' | 'arrowDown',
        text: m.text || `${m.action}${m.price ? ' ₹' + m.price.toFixed(2) : ''}`,
      })),
    );

    if (!didFitRef.current && rows.length) { chart.timeScale().fitContent(); didFitRef.current = true; }
  }, [effCandles, prevDayCandles, effMarkers]);

  return (
    <div style={{ position: 'relative', width: '100%', height }}>
      <div ref={containerRef} style={{ width: '100%', height }} />
      {(loading || effCandles.length === 0) && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--nd-text-3)', fontSize: 13, pointerEvents: 'none', textAlign: 'center', padding: 12 }}>
          {loading ? 'Loading chart…' : (note || 'No chart data')}
        </div>
      )}
    </div>
  );
};

export default TradingChart;
