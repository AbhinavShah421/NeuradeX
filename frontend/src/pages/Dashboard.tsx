import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import apiService from '../services/api';
import { Stock, Prediction } from '../types';

const inr = (v: number) =>
  `₹${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// ── Types ─────────────────────────────────────────────────────────────────────

interface StockEntry {
  symbol: string;
  name: string;
  sector: string;
  exchange: string;
}

interface PriceInfo {
  price: number;
  changePct: number;
}

// ── Shared Tab Bar ─────────────────────────────────────────────────────────────

const TABS = [
  { id: 'watchlist', label: 'AI Watchlist',  icon: 'auto_awesome' },
  { id: 'directory', label: 'All Stocks',    icon: 'format_list_bulleted' },
] as const;
type TabId = typeof TABS[number]['id'];

// ── Exchange badge ─────────────────────────────────────────────────────────────

const ExBadge = ({ ex }: { ex: string }) => {
  const color = ex === 'NSE' ? '#3b82f6' : ex === 'BSE' ? '#f59e0b' : '#8b5cf6';
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, padding: '2px 5px', borderRadius: 3,
      background: `${color}18`, color, border: `1px solid ${color}40`,
      letterSpacing: 0.4,
    }}>{ex}</span>
  );
};

// ── AI Watchlist Tab ──────────────────────────────────────────────────────────

const WatchlistTab = ({ stocks, predictions, loading, error }: {
  stocks: Stock[];
  predictions: Record<string, Prediction>;
  loading: boolean;
  error: string | null;
}) => (
  <div>
    {error && (
      <div className="nd-alert nd-alert-error" style={{ marginBottom: 12 }}>
        <span className="material-icons">error_outline</span> {error}
      </div>
    )}

    {loading ? (
      <div style={{ padding: 48, textAlign: 'center', color: 'var(--nd-text-3)' }}>
        <span className="material-icons nd-spin" style={{ fontSize: 22, verticalAlign: 'middle', marginRight: 8 }}>autorenew</span>
        Loading market data…
      </div>
    ) : (
      <div style={{ overflowX: 'auto' }}>
        <table className="nd-table">
          <thead>
            <tr>
              <th>Company</th>
              <th className="text-right">Price</th>
              <th className="text-right">Day Change</th>
              <th className="text-right">High</th>
              <th className="text-right">Low</th>
              <th className="text-right">Volume</th>
              <th className="text-right">AI Signal</th>
              <th className="text-right">Confidence</th>
              <th className="text-right">Target</th>
            </tr>
          </thead>
          <tbody>
            {stocks.map(stock => {
              const pred = predictions[stock.symbol];
              return (
                <tr key={stock.symbol}>
                  <td>
                    <Link to={`/stocks/${stock.symbol}`} style={{ textDecoration: 'none' }}>
                      <p style={{ fontWeight: 600, color: 'var(--nd-text-1)', fontSize: 13.5 }}>{stock.name}</p>
                      <p style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginTop: 2 }}>
                        {stock.symbol}{stock.sector && <span> · {stock.sector}</span>}
                      </p>
                    </Link>
                  </td>
                  <td className="text-right" style={{ fontWeight: 600, fontSize: 13.5 }}>{inr(stock.price)}</td>
                  <td className="text-right">
                    <p style={{ fontSize: 13, fontWeight: 500, color: stock.change >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                      {stock.change >= 0 ? '+' : ''}{inr(Math.abs(stock.change))}
                    </p>
                    <p style={{ fontSize: 11.5, color: stock.change >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                      ({stock.change >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%)
                    </p>
                  </td>
                  <td className="text-right" style={{ fontSize: 13, color: 'var(--nd-text-2)' }}>{inr(stock.high)}</td>
                  <td className="text-right" style={{ fontSize: 13, color: 'var(--nd-text-2)' }}>{inr(stock.low)}</td>
                  <td className="text-right" style={{ fontSize: 12.5, color: 'var(--nd-text-2)' }}>
                    {(stock.volume / 1_000_000).toFixed(2)}M
                  </td>
                  <td className="text-right">
                    {pred ? (
                      <span className={`nd-badge ${pred.prediction === 'UP' ? 'nd-badge-green' : pred.prediction === 'DOWN' ? 'nd-badge-red' : 'nd-badge-gray'}`}>
                        {pred.prediction === 'UP' ? '▲' : pred.prediction === 'DOWN' ? '▼' : '—'} {pred.prediction}
                      </span>
                    ) : <span style={{ color: 'var(--nd-text-3)' }}>—</span>}
                  </td>
                  <td className="text-right" style={{ fontWeight: 500, fontSize: 13 }}>
                    {pred ? `${(pred.confidence * 100).toFixed(0)}%` : '—'}
                  </td>
                  <td className="text-right" style={{ fontWeight: 500, fontSize: 13 }}>
                    {pred ? inr(pred.targetPrice) : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

// ── All Stocks Directory Tab ───────────────────────────────────────────────────

const EX_ALL = 'All';

const DirectoryTab: React.FC = () => {
  const [stocks, setStocks]           = useState<StockEntry[]>([]);
  const [total, setTotal]             = useState(0);
  const [pages, setPages]             = useState(1);
  const [sectors, setSectors]         = useState<string[]>([]);
  const [prices, setPrices]           = useState<Record<string, PriceInfo>>({});
  const [loading, setLoading]         = useState(false);
  const [priceLoading, setPriceLoading] = useState(false);
  const [query, setQuery]             = useState('');
  const [sector, setSector]           = useState('');
  const [exchange, setExchange]       = useState('');
  const [page, setPage]               = useState(1);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchDirectory = useCallback(async (q: string, sec: string, ex: string, pg: number) => {
    setLoading(true);
    try {
      const json = await apiService.getDirectoryList({
        page: pg, limit: 50,
        ...(q   ? { q }        : {}),
        ...(sec ? { sector: sec } : {}),
        ...(ex && ex !== EX_ALL ? { exchange: ex } : {}),
      });
      setStocks(json.data ?? []);
      setTotal(json.total ?? 0);
      setPages(json.pages ?? 1);
      if (json.sectors?.length) setSectors(json.sectors);
      if (json.data?.length) {
        setPriceLoading(true);
        const syms = json.data.map((s: StockEntry) => s.symbol);
        const prJson = await apiService.getDirectoryPrices(syms);
        setPrices(prJson.data ?? {});
        setPriceLoading(false);
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setPage(1);
      fetchDirectory(query, sector, exchange, 1);
    }, query ? 300 : 0);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, sector, exchange, fetchDirectory]);

  useEffect(() => {
    fetchDirectory(query, sector, exchange, page);
  }, [page]); // eslint-disable-line react-hooks/exhaustive-deps

  const chgColor = (v?: number) =>
    v == null ? 'var(--nd-text-3)' : v >= 0 ? 'var(--nd-green)' : 'var(--nd-red)';

  return (
    <div>
      {/* Filters row */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        {/* Search */}
        <div style={{ position: 'relative', flex: '1 1 220px', maxWidth: 280 }}>
          <span className="material-icons" style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', fontSize: 16, color: 'var(--nd-text-3)', pointerEvents: 'none' }}>search</span>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search symbol or name…"
            style={{
              width: '100%', boxSizing: 'border-box',
              paddingLeft: 30, paddingRight: 10, paddingTop: 7, paddingBottom: 7,
              fontSize: 13, borderRadius: 8, border: '1px solid var(--nd-border)',
              background: 'var(--nd-surface)', color: 'var(--nd-text-1)', outline: 'none',
            }}
          />
        </div>

        {/* Sector */}
        <select
          value={sector}
          onChange={e => setSector(e.target.value)}
          style={{ fontSize: 12, padding: '7px 10px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: 'var(--nd-text-1)', cursor: 'pointer' }}
        >
          <option value="">All Sectors</option>
          {sectors.map(s => <option key={s} value={s}>{s}</option>)}
        </select>

        {/* Exchange toggle */}
        <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--nd-border)' }}>
          {[EX_ALL, 'NSE', 'BSE'].map(ex => (
            <button
              key={ex}
              onClick={() => setExchange(ex === EX_ALL ? '' : ex)}
              style={{
                padding: '6px 14px', fontSize: 12, fontWeight: 500, cursor: 'pointer', border: 'none',
                background: (exchange === ex || (ex === EX_ALL && !exchange)) ? 'var(--nd-accent)' : 'var(--nd-surface)',
                color:      (exchange === ex || (ex === EX_ALL && !exchange)) ? '#fff' : 'var(--nd-text-2)',
                transition: 'all 0.15s',
              }}
            >{ex}</button>
          ))}
        </div>

        <span style={{ fontSize: 12, color: 'var(--nd-text-3)', marginLeft: 'auto' }}>
          {total} stocks
        </span>
      </div>

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table className="nd-table">
          <thead>
            <tr>
              <th style={{ width: 40, textAlign: 'center' }}>#</th>
              <th>Symbol</th>
              <th>Company</th>
              <th>Sector</th>
              <th style={{ textAlign: 'center' }}>Exchange</th>
              <th style={{ textAlign: 'right' }}>Price</th>
              <th style={{ textAlign: 'right' }}>Change %</th>
              <th style={{ textAlign: 'center' }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)' }}>
                  <span className="material-icons nd-spin" style={{ fontSize: 20, verticalAlign: 'middle', marginRight: 6 }}>autorenew</span>
                  Loading stocks…
                </td>
              </tr>
            ) : stocks.length === 0 ? (
              <tr>
                <td colSpan={8} style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)' }}>
                  No stocks found. Try adjusting your filters.
                </td>
              </tr>
            ) : stocks.map((s, idx) => {
              const pi  = prices[s.symbol];
              const row = (page - 1) * 50 + idx + 1;
              return (
                <tr key={s.symbol}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-bg)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  style={{ transition: 'background 0.1s' }}
                >
                  <td style={{ textAlign: 'center', color: 'var(--nd-text-3)', fontSize: 11 }}>{row}</td>
                  <td style={{ fontWeight: 700, fontSize: 13, color: 'var(--nd-accent)', fontFamily: 'monospace' }}>{s.symbol}</td>
                  <td style={{ fontSize: 13, color: 'var(--nd-text-1)', maxWidth: 220 }}>{s.name}</td>
                  <td style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>{s.sector}</td>
                  <td style={{ textAlign: 'center' }}><ExBadge ex={s.exchange} /></td>
                  <td style={{ textAlign: 'right', fontWeight: 600, fontSize: 13 }}>
                    {priceLoading ? <span style={{ color: 'var(--nd-text-3)', fontSize: 11 }}>…</span> : pi ? inr(pi.price) : '—'}
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 600, fontSize: 13, color: chgColor(pi?.changePct) }}>
                    {pi != null ? `${pi.changePct >= 0 ? '+' : ''}${pi.changePct.toFixed(2)}%` : '—'}
                  </td>
                  <td style={{ textAlign: 'center' }}>
                    <Link to={`/stocks/${s.symbol}`} style={{
                      fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 6,
                      background: 'var(--nd-accent)18', color: 'var(--nd-accent)',
                      border: '1px solid var(--nd-accent)40', textDecoration: 'none',
                    }}>View</Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div style={{ padding: '12px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4 }}>
          <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
            Page {page} of {pages} · {total} stocks
          </span>
          <div style={{ display: 'flex', gap: 4 }}>
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
              style={{ padding: '4px 12px', fontSize: 12, borderRadius: 6, cursor: page === 1 ? 'default' : 'pointer', border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: page === 1 ? 'var(--nd-text-3)' : 'var(--nd-text-1)', opacity: page === 1 ? 0.5 : 1 }}>
              ← Prev
            </button>
            {Array.from({ length: Math.min(7, pages) }, (_, i) => {
              let p: number;
              if (pages <= 7)       p = i + 1;
              else if (page <= 4)   p = i + 1;
              else if (page >= pages - 3) p = pages - 6 + i;
              else                  p = page - 3 + i;
              return (
                <button key={p} onClick={() => setPage(p)}
                  style={{ width: 30, height: 28, fontSize: 12, borderRadius: 6, cursor: 'pointer', border: '1px solid var(--nd-border)', background: p === page ? 'var(--nd-accent)' : 'var(--nd-surface)', color: p === page ? '#fff' : 'var(--nd-text-2)', fontWeight: p === page ? 700 : 400 }}>
                  {p}
                </button>
              );
            })}
            <button onClick={() => setPage(p => Math.min(pages, p + 1))} disabled={page === pages}
              style={{ padding: '4px 12px', fontSize: 12, borderRadius: 6, cursor: page === pages ? 'default' : 'pointer', border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: page === pages ? 'var(--nd-text-3)' : 'var(--nd-text-1)', opacity: page === pages ? 0.5 : 1 }}>
              Next →
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

// ── Metric evidence modal ─────────────────────────────────────────────────────
// Shows exactly how each headline number is derived from real stored data.

const MetricModal: React.FC<{ cardId: string; stats: any; onClose: () => void }> = ({ cardId, stats, onClose }) => {
  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
  const Row: React.FC<{ k: string; v: React.ReactNode; c?: string }> = ({ k, v, c }) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '9px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 13 }}>
      <span style={{ color: 'var(--nd-text-3)' }}>{k}</span>
      <span style={{ color: c || 'var(--nd-text-1)', fontWeight: 600, textAlign: 'right' }}>{v}</span>
    </div>
  );

  const META: Record<string, { title: string; icon: string; how: string; rows: () => React.ReactNode }> = {
    accuracy: {
      title: 'Model Accuracy', icon: 'psychology',
      how: 'Share of the AI engine’s recorded predictions whose outcome was correct (from the agent learning loop). It rises as the agents learn from each backtest, paper trade and live session.',
      rows: () => (<>
        <Row k="Correct predictions" v={stats.correctPredictions} c="var(--nd-green)" />
        <Row k="Total predictions" v={stats.totalPredictions} />
        <Row k="Accuracy" v={pct(stats.accuracyRate)} c="var(--nd-green)" />
      </>),
    },
    win: {
      title: 'Win Rate', icon: 'emoji_events',
      how: 'Share of closed trades that were profitable, computed from every recorded trade (Live, Paper, Backtest).',
      rows: () => (<>
        <Row k="Winning trades" v={stats.winningTrades} c="var(--nd-green)" />
        <Row k="Losing trades" v={stats.losingTrades} c="var(--nd-red)" />
        <Row k="Total closed trades" v={stats.totalTrades} />
        <Row k="Win rate" v={pct(stats.winRate)} c="var(--nd-green)" />
        {Array.isArray(stats.bySource) && stats.bySource.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 6 }}>By source</div>
            {stats.bySource.map((b: any) => (
              <Row key={b.source} k={`${b.source} (${b.trades})`} v={`${(b.winRate * 100).toFixed(1)}% · ${b.avgReturn >= 0 ? '+' : ''}${b.avgReturn}%`} />
            ))}
          </div>
        )}
      </>),
    },
    return: {
      title: 'Average Return', icon: 'trending_up',
      how: 'Mean P&L % across all closed trades. Best/worst show the spread the average is drawn from.',
      rows: () => (<>
        <Row k="Average return / trade" v={`${stats.averageReturn >= 0 ? '+' : ''}${stats.averageReturn}%`} c={stats.averageReturn >= 0 ? 'var(--nd-green)' : 'var(--nd-red)'} />
        <Row k="Std deviation" v={`${stats.returnStd}%`} />
        <Row k="Best trade" v={`+${stats.bestTradePct}%`} c="var(--nd-green)" />
        <Row k="Worst trade" v={`${stats.worstTradePct}%`} c="var(--nd-red)" />
        <Row k="Across" v={`${stats.totalTrades} trades`} />
      </>),
    },
    sharpe: {
      title: 'Sharpe Ratio', icon: 'analytics',
      how: 'Risk-adjusted return = mean return ÷ standard deviation of returns (per trade). Higher means more consistent returns for the risk taken.',
      rows: () => (<>
        <Row k="Sharpe (mean ÷ std)" v={stats.sharpeRatio} c="var(--nd-purple)" />
        <Row k="Mean return" v={`${stats.averageReturn}%`} />
        <Row k="Return std" v={`${stats.returnStd}%`} />
        <Row k="Max drawdown (₹)" v={`₹${stats.maxDrawdown?.toLocaleString('en-IN')}`} c="var(--nd-red)" />
      </>),
    },
  };
  const m = META[cardId];
  if (!m) return null;

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}>
      <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 460, maxHeight: '88vh', overflow: 'auto', boxShadow: '0 24px 64px #00000060' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="material-icons" style={{ color: 'var(--nd-green)' }}>{m.icon}</span>
            <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>{m.title}</span>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer' }}>
            <span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 20 }}>close</span>
          </button>
        </div>
        <div style={{ padding: '14px 20px' }}>
          <p style={{ margin: '0 0 14px', fontSize: 12.5, lineHeight: 1.6, color: 'var(--nd-text-2)' }}>{m.how}</p>
          {m.rows()}
          <div style={{ marginTop: 14, fontSize: 11, color: 'var(--nd-text-3)', background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px' }}>
            Computed live from <strong>{stats.totalTrades}</strong> closed trades and <strong>{stats.totalPredictions}</strong> predictions — no hard-coded values.
            {!stats.hasData && ' Run a backtest or session to start populating these.'}
          </div>
        </div>
      </div>
    </div>
  );
};

// ── Dashboard Page ────────────────────────────────────────────────────────────

const Dashboard: React.FC = () => {
  const [activeTab, setActiveTab]     = useState<TabId>('watchlist');
  const [stocks, setStocks]           = useState<Stock[]>([]);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState<string | null>(null);
  const [predictions, setPredictions] = useState<Record<string, Prediction>>({});
  const [accuracyStats, setAccuracyStats] = useState<any>(null);
  const [selectedCard, setSelectedCard] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await apiService.getStocks();
        if (res.data) {
          setStocks(res.data);
          for (const stock of res.data) {
            try {
              const pr = await apiService.getPrediction(stock.symbol);
              if (pr.data) setPredictions(prev => ({ ...prev, [stock.symbol]: pr.data! }));
            } catch { /* skip */ }
          }
        }
      } catch { setError('Failed to fetch market data'); }
      finally   { setLoading(false); }
    })();
    apiService.getAccuracyStats().then(r => { if (r.data) setAccuracyStats(r.data); }).catch(() => {});
  }, []);

  const STAT_CARDS = accuracyStats ? [
    { id: 'accuracy', label: 'Model Accuracy', value: `${(accuracyStats.accuracyRate * 100).toFixed(1)}%`, icon: 'psychology',    color: 'var(--nd-green)',  bg: 'var(--nd-green-50)' },
    { id: 'win',      label: 'Win Rate',       value: `${(accuracyStats.winRate * 100).toFixed(1)}%`,        icon: 'emoji_events', color: 'var(--nd-green)',  bg: 'var(--nd-green-50)' },
    { id: 'return',   label: 'Avg Return',     value: `${accuracyStats.averageReturn?.toFixed(2)}%`,          icon: 'trending_up',  color: 'var(--nd-blue)',   bg: '#e3f2fd'            },
    { id: 'sharpe',   label: 'Sharpe Ratio',   value: accuracyStats.sharpeRatio?.toFixed(2),                  icon: 'analytics',    color: 'var(--nd-purple)', bg: '#f5f3ff'            },
  ] : [];

  return (
    <div>
      {/* Page heading */}
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">Market Overview</h1>
        <p className="nd-page-sub">Real-time NSE · BSE stock data with AI-generated predictions</p>
      </div>

      {/* Accuracy stat cards — click any to see the evidence */}
      {STAT_CARDS.length > 0 && (
        <div className="nd-grid-4" style={{ gap: 12, marginBottom: 20 }}>
          {STAT_CARDS.map(s => (
            <div key={s.label} className="nd-card" onClick={() => setSelectedCard(s.id)}
              style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px', cursor: 'pointer', transition: 'box-shadow 0.15s' }}
              onMouseEnter={e => (e.currentTarget.style.boxShadow = 'var(--nd-shadow-md)')}
              onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}>
              <div className="nd-icon-chip" style={{ background: s.bg }}>
                <span className="material-icons" style={{ color: s.color }}>{s.icon}</span>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p className="nd-label">{s.label}</p>
                <p style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{s.value}</p>
              </div>
              <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>info</span>
            </div>
          ))}
        </div>
      )}

      {selectedCard && accuracyStats && (
        <MetricModal cardId={selectedCard} stats={accuracyStats} onClose={() => setSelectedCard(null)} />
      )}

      {/* Tabbed card */}
      <div className="nd-card" style={{ padding: 0 }}>

        {/* Tab bar */}
        <div style={{ display: 'flex', borderBottom: '1px solid var(--nd-border)', padding: '0 20px' }}>
          {TABS.map(tab => {
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '13px 18px', fontSize: 13, fontWeight: active ? 700 : 500,
                  cursor: 'pointer', border: 'none', background: 'transparent',
                  color: active ? 'var(--nd-accent)' : 'var(--nd-text-3)',
                  borderBottom: active ? '2px solid var(--nd-accent)' : '2px solid transparent',
                  marginBottom: -1, transition: 'color 0.15s',
                }}
              >
                <span className="material-icons" style={{ fontSize: 16 }}>{tab.icon}</span>
                {tab.label}
                {tab.id === 'watchlist' && (
                  <span style={{ fontSize: 11, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '1px 7px', color: 'var(--nd-text-3)', fontWeight: 400 }}>
                    {stocks.length}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Tab content */}
        <div style={{ padding: '16px 20px 20px' }}>
          {activeTab === 'watchlist' && (
            <WatchlistTab
              stocks={stocks}
              predictions={predictions}
              loading={loading}
              error={error}
            />
          )}
          {activeTab === 'directory' && <DirectoryTab />}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
