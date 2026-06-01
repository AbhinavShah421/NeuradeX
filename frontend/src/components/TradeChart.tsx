import React, { useEffect, useRef } from 'react';
import {
  createChart, ColorType, CrosshairMode, IChartApi,
  ISeriesApi, UTCTimestamp,
} from 'lightweight-charts';

export interface ChartCandle {
  time?: string; timestamp: number;
  open: number; high: number; low: number; close: number; volume?: number;
}
export interface TradeMarker {
  timestamp: number; action: 'BUY' | 'SELL'; price?: number; text?: string;
}

const IST_OFFSET = 19800; // seconds — render timestamps in IST
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const p2 = (n: number) => String(n).padStart(2, '0');

interface Props {
  candles: ChartCandle[];
  prevDayCandles?: ChartCandle[];
  markers?: TradeMarker[];
  height?: number;
  isDark?: boolean;
}

const TradeChart: React.FC<Props> = ({ candles, prevDayCandles = [], markers = [], height = 420, isDark = true }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const csRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const didFitRef = useRef(false);

  // Create chart once
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

  // Update data when candles/markers change
  useEffect(() => {
    const cs = csRef.current, vol = volRef.current, chart = chartRef.current;
    if (!cs || !vol || !chart) return;

    const all = [...prevDayCandles, ...candles];
    // De-dupe by timestamp and sort ascending (lightweight-charts requires it)
    const seen = new Set<number>();
    const rows = all
      .filter(c => c && typeof c.timestamp === 'number')
      .sort((a, b) => a.timestamp - b.timestamp)
      .filter(c => { const t = c.timestamp; if (seen.has(t)) return false; seen.add(t); return true; });

    cs.setData(rows.map(c => ({
      time: (c.timestamp + IST_OFFSET) as UTCTimestamp,
      open: c.open, high: c.high, low: c.low, close: c.close,
    })));
    vol.setData(rows.map(c => ({
      time: (c.timestamp + IST_OFFSET) as UTCTimestamp,
      value: c.volume || 0,
      color: c.close >= c.open ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.35)',
    })));

    if (markers.length) {
      const mk = [...markers]
        .sort((a, b) => a.timestamp - b.timestamp)
        .map(m => ({
          time: (m.timestamp + IST_OFFSET) as UTCTimestamp,
          position: (m.action === 'BUY' ? 'belowBar' : 'aboveBar') as 'belowBar' | 'aboveBar',
          color: m.action === 'BUY' ? '#26a69a' : '#ef5350',
          shape: (m.action === 'BUY' ? 'arrowUp' : 'arrowDown') as 'arrowUp' | 'arrowDown',
          text: m.text || `${m.action}${m.price ? ' ₹' + m.price.toFixed(2) : ''}`,
        }));
      cs.setMarkers(mk);
    } else {
      cs.setMarkers([]);
    }

    if (!didFitRef.current && rows.length) {
      chart.timeScale().fitContent();
      didFitRef.current = true;
    }
  }, [candles, prevDayCandles, markers]);

  return <div ref={containerRef} style={{ width: '100%', height }} />;
};

export default TradeChart;
