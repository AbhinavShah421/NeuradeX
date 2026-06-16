import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import apiService from '../services/api';
import ScanControl from '../components/ScanControl';

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

// ── All Stocks Directory Tab ───────────────────────────────────────────────────

const EX_ALL = 'All';

type SortCol = 'symbol' | 'name' | 'sector' | 'exchange' | 'price' | 'changePct';
type SortDir = 'asc' | 'desc';
type ChangeFilter = 'all' | 'gainers' | 'losers';

const SortIcon: React.FC<{ col: SortCol; active: SortCol | null; dir: SortDir }> = ({ col, active, dir }) => (
  <span style={{ fontSize: 11, marginLeft: 3, color: active === col ? 'var(--nd-accent)' : 'var(--nd-text-3)', verticalAlign: 'middle' }}>
    {active === col ? (dir === 'asc' ? '▲' : '▼') : '⇅'}
  </span>
);

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
  const [sortCol, setSortCol]         = useState<SortCol | null>(null);
  const [sortDir, setSortDir]         = useState<SortDir>('asc');
  const [changeFilter, setChangeFilter] = useState<ChangeFilter>('all');
  const [priceMin, setPriceMin]       = useState('');
  const [priceMax, setPriceMax]       = useState('');
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

  const toggleSort = (col: SortCol) => {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortCol(col); setSortDir('asc'); }
  };

  const thStyle = (col: SortCol, align: 'left' | 'center' | 'right' = 'left'): React.CSSProperties => ({
    textAlign: align, cursor: 'pointer', userSelect: 'none',
    color: sortCol === col ? 'var(--nd-accent)' : undefined,
    whiteSpace: 'nowrap',
  });

  const minP = priceMin !== '' ? parseFloat(priceMin) : null;
  const maxP = priceMax !== '' ? parseFloat(priceMax) : null;

  const displayStocks = [...stocks]
    .filter(s => {
      const pi = prices[s.symbol];
      if (changeFilter === 'gainers' && (pi == null || pi.changePct < 0)) return false;
      if (changeFilter === 'losers'  && (pi == null || pi.changePct >= 0)) return false;
      if (minP != null && (pi == null || pi.price < minP)) return false;
      if (maxP != null && (pi == null || pi.price > maxP)) return false;
      return true;
    })
    .sort((a, b) => {
      if (!sortCol) return 0;
      const pa = prices[a.symbol], pb = prices[b.symbol];
      let va: any, vb: any;
      if (sortCol === 'price')     { va = pa?.price ?? -Infinity; vb = pb?.price ?? -Infinity; }
      else if (sortCol === 'changePct') { va = pa?.changePct ?? -Infinity; vb = pb?.changePct ?? -Infinity; }
      else if (sortCol === 'symbol')   { va = a.symbol;   vb = b.symbol; }
      else if (sortCol === 'name')     { va = a.name;     vb = b.name; }
      else if (sortCol === 'sector')   { va = a.sector;   vb = b.sector; }
      else if (sortCol === 'exchange') { va = a.exchange; vb = b.exchange; }
      if (va < vb) return sortDir === 'asc' ? -1 : 1;
      if (va > vb) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });

  const hasFilters = changeFilter !== 'all' || priceMin !== '' || priceMax !== '';

  return (
    <div>
      {/* Filters row 1: search + sector + exchange */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
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
          {displayStocks.length !== stocks.length
            ? <>{displayStocks.length} <span style={{ opacity: 0.6 }}>of {total}</span></>
            : <>{total}</>} stocks
        </span>
      </div>

      {/* Filters row 2: price range + change direction */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        {/* Price range */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ fontSize: 11, color: 'var(--nd-text-3)', whiteSpace: 'nowrap' }}>Price ₹</span>
          <input
            type="number" min={0} value={priceMin} onChange={e => setPriceMin(e.target.value)}
            placeholder="Min"
            style={{ width: 72, fontSize: 12, padding: '5px 8px', borderRadius: 7, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: 'var(--nd-text-1)', outline: 'none' }}
          />
          <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>–</span>
          <input
            type="number" min={0} value={priceMax} onChange={e => setPriceMax(e.target.value)}
            placeholder="Max"
            style={{ width: 72, fontSize: 12, padding: '5px 8px', borderRadius: 7, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: 'var(--nd-text-1)', outline: 'none' }}
          />
        </div>

        {/* Change direction toggle */}
        <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--nd-border)' }}>
          {(['all', 'gainers', 'losers'] as ChangeFilter[]).map(cf => (
            <button key={cf} onClick={() => setChangeFilter(cf)}
              style={{
                padding: '5px 12px', fontSize: 12, fontWeight: 500, cursor: 'pointer', border: 'none',
                background: changeFilter === cf
                  ? (cf === 'gainers' ? 'var(--nd-green)' : cf === 'losers' ? 'var(--nd-red)' : 'var(--nd-accent)')
                  : 'var(--nd-surface)',
                color: changeFilter === cf ? '#fff' : 'var(--nd-text-2)',
                transition: 'all 0.15s',
              }}>
              {cf === 'all' ? 'All' : cf === 'gainers' ? '▲ Gainers' : '▼ Losers'}
            </button>
          ))}
        </div>

        {/* Clear filters */}
        {(hasFilters || sortCol) && (
          <button onClick={() => { setChangeFilter('all'); setPriceMin(''); setPriceMax(''); setSortCol(null); setSortDir('asc'); }}
            style={{ fontSize: 11, padding: '5px 10px', borderRadius: 7, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: 'var(--nd-text-3)', cursor: 'pointer' }}>
            Clear
          </button>
        )}
      </div>

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table className="nd-table">
          <thead>
            <tr>
              <th style={{ width: 40, textAlign: 'center' }}>#</th>
              <th style={thStyle('symbol')} onClick={() => toggleSort('symbol')}>
                Symbol <SortIcon col="symbol" active={sortCol} dir={sortDir} />
              </th>
              <th style={thStyle('name')} onClick={() => toggleSort('name')}>
                Company <SortIcon col="name" active={sortCol} dir={sortDir} />
              </th>
              <th style={thStyle('sector')} onClick={() => toggleSort('sector')}>
                Sector <SortIcon col="sector" active={sortCol} dir={sortDir} />
              </th>
              <th style={thStyle('exchange', 'center')} onClick={() => toggleSort('exchange')}>
                Exchange <SortIcon col="exchange" active={sortCol} dir={sortDir} />
              </th>
              <th style={thStyle('price', 'right')} onClick={() => toggleSort('price')}>
                Price <SortIcon col="price" active={sortCol} dir={sortDir} />
              </th>
              <th style={thStyle('changePct', 'right')} onClick={() => toggleSort('changePct')}>
                Change % <SortIcon col="changePct" active={sortCol} dir={sortDir} />
              </th>
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
            ) : displayStocks.length === 0 ? (
              <tr>
                <td colSpan={8} style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)' }}>
                  No stocks found. Try adjusting your filters.
                </td>
              </tr>
            ) : displayStocks.map((s, idx) => {
              const pi  = prices[s.symbol];
              const row = sortCol ? idx + 1 : (page - 1) * 50 + idx + 1;
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

// ── Autopilot banner ──────────────────────────────────────────────────────────

const TradeGateCard: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { const r = await apiService.getTradeGate(); setData((r as any).data); } catch {}
  }, []);
  useEffect(() => { load(); }, [load]);
  const pick = async (mode: string) => {
    if (busy || mode === data?.mode) return;
    setBusy(true);
    try { await apiService.setTradeGate(mode); await load(); } catch {} finally { setBusy(false); }
  };
  if (!data) return null;
  const opts: any[] = data.options ?? [];
  const active = opts.find(o => o.id === data.mode);
  return (
    <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <div className="nd-icon-chip"><span className="material-icons" style={{ color: 'var(--nd-text-2)' }}>tune</span></div>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Trade Gate</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>How selective entries are — applies to paper, backtest & autopilot</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 6, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: 4 }}>
        {opts.map(o => {
          const on = o.id === data.mode;
          return (
            <button key={o.id} onClick={() => pick(o.id)} disabled={busy}
              style={{ flex: 1, padding: '8px 6px', borderRadius: 7, border: 'none', cursor: busy ? 'wait' : 'pointer', fontSize: 13, fontWeight: 600,
                background: on ? 'var(--nd-green)' : 'transparent', color: on ? '#fff' : 'var(--nd-text-2)', transition: 'all 0.15s' }}>
              {o.label}
            </button>
          );
        })}
      </div>
      {active && <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', marginTop: 8, lineHeight: 1.5 }}>{active.desc}</div>}
    </div>
  );
};

const APToggle: React.FC<{ on: boolean; busy: boolean; onClick: () => void }> = ({ on, busy, onClick }) => (
  <button onClick={onClick} disabled={busy}
    style={{ width: 46, height: 26, borderRadius: 14, border: 'none', cursor: busy ? 'wait' : 'pointer', position: 'relative', background: on ? 'var(--nd-green)' : 'var(--nd-border)', transition: 'background 0.2s', flexShrink: 0 }}>
    <span style={{ position: 'absolute', top: 3, left: on ? 23 : 3, width: 20, height: 20, borderRadius: '50%', background: '#fff', transition: 'left 0.2s', boxShadow: '0 1px 3px #0003' }} />
  </button>
);

const APRow: React.FC<{ icon: string; title: string; desc: string; on: boolean; busy: boolean; onToggle: () => void; first?: boolean }> =
  ({ icon, title, desc, on, busy, onToggle, first }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0', borderTop: first ? 'none' : '1px solid var(--nd-border)' }}>
    <span className="material-icons" style={{ fontSize: 18, color: on ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>{icon}</span>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)', display: 'flex', alignItems: 'center', gap: 6 }}>
        {title}{on && <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--nd-green)', border: '1px solid var(--nd-green)', borderRadius: 4, padding: '0 4px' }}>ON</span>}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{desc}</div>
    </div>
    <APToggle on={on} busy={busy} onClick={onToggle} />
  </div>
);

const AutopilotBanner: React.FC = () => {
  const [ap, setAp] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { const r = await apiService.getAutopilot(); setAp((r as any).data); } catch {}
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);
  const toggle = async (mode: 'paper' | 'backtest', next: boolean) => {
    setBusy(mode);
    try { const r = await apiService.setAutopilot(next, mode); setAp((r as any).data); } catch {} finally { setBusy(null); }
  };
  const resetCursor = async () => {
    setBusy('reset');
    try { const r = await apiService.resetBacktestCursor(); setAp((r as any).data); } catch {} finally { setBusy(null); }
  };
  const setPaperTiming = async (mode: 'normal' | 'aggressive') => {
    setBusy('timing');
    try { const r = await apiService.setAutopilotPaperTiming(mode); setAp((r as any).data); } catch {} finally { setBusy(null); }
  };
  if (!ap) return null;
  const paper = ap.paper ?? {};
  const bt = ap.backtest ?? {};
  const anyOn = paper.enabled || bt.enabled;

  const paperDesc = paper.enabled
    ? (paper.marketOpen
        ? `Paper-trading ${paper.running ?? 0} of ${paper.watchlistSize ?? 0} watchlist stocks live`
        : `Market closed — will paper-trade all ${paper.watchlistSize ?? 0} watchlist stocks at open`)
    : 'Live paper-trade the whole watchlist during market hours';

  const btDesc = bt.enabled
    ? (bt.activeWindow === false
        ? `Paused for paper-trading hours — resumes after close · ${bt.completedDays ?? 0} days trained`
        : (bt.running ?? 0) > 0
          ? `Replaying ${bt.queueDate ?? bt.cursor} at ${bt.speed ?? 1}× · ${bt.queuePending ?? 0}/${bt.queueTotal ?? 0} sessions left · ${bt.completedDays ?? 0} days trained`
          : `Next day: ${bt.cursor ?? '—'} · ${bt.completedDays ?? 0} days trained so far`)
    : 'Replays past days (walking back) outside market hours to train on dense real data';

  return (
    <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 20, borderLeft: `3px solid ${anyOn ? 'var(--nd-green)' : 'var(--nd-border)'}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
        <div className="nd-icon-chip" style={{ background: anyOn ? 'var(--nd-green-50)' : 'var(--nd-surface)' }}>
          <span className="material-icons" style={{ color: anyOn ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>smart_toy</span>
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Autopilot</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Auto-trades the AI watchlist to keep training the agents</div>
        </div>
      </div>
      <APRow first icon="sync" title="Paper (live)" desc={paperDesc}
        on={!!paper.enabled} busy={busy === 'paper'} onToggle={() => toggle('paper', !paper.enabled)} />

      {/* Paper entry-timing mode */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0 8px 30px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>Entry timing</span>
        <div style={{ display: 'flex', gap: 2, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: 2 }}>
          {(['normal', 'aggressive'] as const).map(m => {
            const active = (paper.timingMode ?? 'normal') === m;
            return (
              <button key={m} onClick={() => setPaperTiming(m)} disabled={busy === 'timing'}
                style={{ padding: '3px 12px', borderRadius: 6, border: 'none', cursor: busy === 'timing' ? 'wait' : 'pointer', fontSize: 11, fontWeight: 600, textTransform: 'capitalize',
                  background: active ? (m === 'aggressive' ? '#f59e0b' : 'var(--nd-green)') : 'transparent',
                  color: active ? '#fff' : 'var(--nd-text-2)' }}>
                {m}
              </button>
            );
          })}
        </div>
        <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)' }}>
          {(paper.timingMode ?? 'normal') === 'aggressive' ? 'looser triggers — more trades' : 'standard triggers'}
        </span>
      </div>

      <APRow icon="history" title="Backtest (1× replay)" desc={btDesc}
        on={!!bt.enabled} busy={busy === 'backtest'} onToggle={() => toggle('backtest', !bt.enabled)} />
      {/* Next trade date + reset */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0 0 30px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>
          Next trade date:&nbsp;
          <strong style={{ color: 'var(--nd-text-2)', fontFamily: 'monospace' }}>{bt.cursor ?? '—'}</strong>
        </span>
        <button onClick={resetCursor} disabled={busy === 'reset'}
          title="Reset the backtest walk to the last trading day before today"
          style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 6,
            border: '1px solid var(--nd-border)', background: 'var(--nd-surface)',
            color: 'var(--nd-text-2)', cursor: busy === 'reset' ? 'wait' : 'pointer', fontSize: 11, fontWeight: 600 }}>
          <span className="material-icons" style={{ fontSize: 13 }}>{busy === 'reset' ? 'hourglass_top' : 'restart_alt'}</span>
          {busy === 'reset' ? 'Resetting…' : 'Reset to last trading day'}
        </button>
      </div>
    </div>
  );
};

// ── Learning curve ────────────────────────────────────────────────────────────

type LcMetric = 'equity' | 'rolling' | 'cumulative';

const SOURCE_PRESETS: Record<string, string> = {
  All: 'PAPER,LIVE,REPLAY',
  Paper: 'PAPER,LIVE',
  Replay: 'REPLAY',
};

const EVENT_COLOR: Record<string, string> = {
  scanner: '#3b82f6', trading: '#f59e0b', learning: '#a855f7', update: '#94a3b8',
};

const LearningCurveCard: React.FC<{ embedded?: boolean }> = ({ embedded }) => {
  const [data, setData] = useState<any>(null);
  const [metric, setMetric] = useState<LcMetric>('equity');
  const [srcKey, setSrcKey] = useState<string>('All');
  const [hovEv, setHovEv] = useState<{ x: number; ev: any } | null>(null);
  const rootCls = embedded ? undefined : 'nd-card';
  const rootStyle: React.CSSProperties = embedded
    ? { padding: '16px 18px', position: 'relative' }
    : { padding: '16px 18px', marginBottom: 20, position: 'relative' };

  useEffect(() => {
    apiService.learningCurve(SOURCE_PRESETS[srcKey], 50)
      .then(r => setData((r as any).data)).catch(() => {});
  }, [srcKey]);

  const pts: any[] = data?.points ?? [];
  const events: any[] = data?.events ?? [];
  const bySource: any[] = data?.bySource ?? [];
  if (pts.length < 2) {
    return (
      <div className={rootCls} style={{ padding: '16px 18px' }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>System Learning Curve</div>
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 6 }}>
          Not enough {srcKey.toLowerCase()} trades yet to plot.
        </div>
      </div>
    );
  }

  const val = (p: any): number =>
    metric === 'equity' ? p.cumEquity
      : metric === 'rolling' ? p.rollWinRate * 100
        : p.cumWinRate * 100;
  const isPct = metric !== 'equity';
  const color = metric === 'equity' ? 'var(--nd-green)' : metric === 'rolling' ? '#3b82f6' : '#a855f7';

  const W = 600, H = 170, PL = 42, PR = 12, PT = 16, PB = 26;
  const ys = pts.map(val);
  const t0 = Date.parse(pts[0].ts) || 0;
  const t1 = Date.parse(pts[pts.length - 1].ts) || (pts.length - 1);
  const span = (t1 - t0) || 1;
  const pad = (Math.max(...ys) - Math.min(...ys)) * 0.08 || 1;
  let yMin = Math.min(...ys) - pad, yMax = Math.max(...ys) + pad;
  if (isPct) { yMin = Math.max(0, yMin); yMax = Math.min(100, yMax); }
  const tx = (ts: string | number) => {
    const t = typeof ts === 'number' ? ts : (Date.parse(ts) || t0);
    return PL + Math.max(0, Math.min(1, (t - t0) / span)) * (W - PL - PR);
  };
  const sx = (i: number) => tx(pts[i].ts);
  const sy = (v: number) => PT + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PT - PB);
  const line = pts.map((p, i) => `${sx(i).toFixed(1)},${sy(val(p)).toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1];
  const fmt = (v: number) => isPct ? `${v.toFixed(0)}%` : `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`;
  const headline = isPct ? `${val(last).toFixed(1)}%`
    : `${val(last) >= 0 ? '+' : ''}${val(last).toFixed(1)}%`;
  const trend = val(last) - val(pts[Math.max(0, pts.length - 6)]);

  const tabBtn = (label: string, active: boolean, onClick: () => void) => (
    <button key={label} onClick={onClick} style={{
      padding: '3px 10px', fontSize: 11, fontWeight: 600, borderRadius: 6, cursor: 'pointer',
      border: `1px solid ${active ? color : 'var(--nd-border)'}`,
      background: active ? color : 'transparent',
      color: active ? '#fff' : 'var(--nd-text-2)',
    }}>{label}</button>
  );

  return (
    <div className={rootCls} style={rootStyle}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>System Learning Curve</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
            {metric === 'equity' ? 'Cumulative return (equity) — rises even when win-rate is below 50%'
              : metric === 'rolling' ? 'Trailing-50-trade win-rate — recent skill, not dragged by old trades'
                : 'Cumulative win-rate over all trades (lagging)'}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 18, fontWeight: 700, color }}>{headline}</div>
          <div style={{ fontSize: 11, color: trend >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
            {trend >= 0 ? '▲' : '▼'} {fmt(Math.abs(trend))} recent · {data.totalTrades} trades
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        {tabBtn('Equity', metric === 'equity', () => setMetric('equity'))}
        {tabBtn('Rolling WR', metric === 'rolling', () => setMetric('rolling'))}
        {tabBtn('Cumulative WR', metric === 'cumulative', () => setMetric('cumulative'))}
        <span style={{ width: 1, background: 'var(--nd-border)', margin: '0 4px' }} />
        {Object.keys(SOURCE_PRESETS).map(k => tabBtn(k, srcKey === k, () => setSrcKey(k)))}
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 160 }} preserveAspectRatio="none"
        onMouseLeave={() => setHovEv(null)}>
        {[yMin, (yMin + yMax) / 2, yMax].map((v, i) => (
          <g key={i}>
            <line x1={PL} y1={sy(v)} x2={W - PR} y2={sy(v)} stroke="var(--nd-border)" strokeWidth="0.5" />
            <text x={4} y={sy(v) + 3} fontSize="9" fill="var(--nd-text-3)">{fmt(v)}</text>
          </g>
        ))}
        {metric === 'equity' && yMin < 0 && yMax > 0 && (
          <line x1={PL} y1={sy(0)} x2={W - PR} y2={sy(0)} stroke="var(--nd-text-3)" strokeWidth="0.6" strokeDasharray="3 3" />
        )}
        {/* System-update event markers */}
        {events.map((ev, i) => {
          const t = Date.parse(ev.occurredAt);
          if (isNaN(t) || t < t0 - span * 0.02 || t > t1 + span * 0.02) return null;
          const x = tx(t);
          const c = EVENT_COLOR[ev.category] || EVENT_COLOR.update;
          return (
            <g key={i} style={{ cursor: 'pointer' }}
              onMouseEnter={() => setHovEv({ x, ev })} onMouseLeave={() => setHovEv(null)}>
              <line x1={x} y1={PT} x2={x} y2={H - PB} stroke={c} strokeWidth="1" strokeDasharray="3 3" opacity={0.8} />
              <polygon points={`${x - 3},${PT} ${x + 3},${PT} ${x},${PT + 5}`} fill={c} />
              <rect x={x - 7} y={PT} width={14} height={H - PB - PT} fill="transparent" />
            </g>
          );
        })}
        <polyline points={line} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={sx(pts.length - 1)} cy={sy(val(last))} r="3.5" fill={color} />
        {/* x-axis date labels — only the endpoints (time-clustered data makes a
            middle label collide with the end one). */}
        <text x={PL} y={H - 8} fontSize="9" fill="var(--nd-text-3)" textAnchor="start">{pts[0].date}</text>
        {pts[pts.length - 1].date !== pts[0].date && (
          <text x={W - PR} y={H - 8} fontSize="9" fill="var(--nd-text-3)" textAnchor="end">{pts[pts.length - 1].date}</text>
        )}
      </svg>

      {hovEv && (
        <div style={{
          position: 'absolute', left: `${(hovEv.x / W) * 100}%`, top: 96, transform: 'translateX(-50%)',
          background: 'var(--nd-bg-2, #1b2330)', border: '1px solid var(--nd-border)', borderRadius: 8,
          padding: '8px 10px', maxWidth: 260, zIndex: 10, boxShadow: '0 6px 20px rgba(0,0,0,.35)',
          pointerEvents: 'none',
        }}>
          <div style={{ fontSize: 10, color: EVENT_COLOR[hovEv.ev.category] || '#94a3b8', fontWeight: 700, textTransform: 'uppercase' }}>
            {hovEv.ev.category}
          </div>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)' }}>{hovEv.ev.title}</div>
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginBottom: 4 }}>
            {new Date(hovEv.ev.occurredAt).toLocaleString()}
          </div>
          <div style={{ fontSize: 11, color: 'var(--nd-text-2)', lineHeight: 1.35 }}>{hovEv.ev.detail}</div>
        </div>
      )}

      {/* Per-source breakdown — exposes that win-rate ≠ profitability */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
        {bySource.map((s) => (
          <div key={s.source} style={{
            border: '1px solid var(--nd-border)', borderRadius: 8, padding: '5px 9px', fontSize: 11,
          }}>
            <span style={{ fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.source}</span>
            <span style={{ color: 'var(--nd-text-3)' }}> · {s.trades} trades</span>
            <span style={{ color: 'var(--nd-text-2)' }}> · WR {(s.winRate * 100).toFixed(0)}%</span>
            <span style={{ color: s.expectancy >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 600 }}>
              {' · exp '}{s.expectancy >= 0 ? '+' : ''}{s.expectancy.toFixed(2)}%/trade
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

// ── AI scan accuracy (predicted vs actual, per trade-day) ──────────────────────

const ScanAccuracyCard: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [show, setShow] = useState<{ intraday: boolean; delivery: boolean; committed: boolean }>(
    { intraday: true, delivery: true, committed: true });
  useEffect(() => { apiService.scanEvaluation().then(r => setData((r as any).data)).catch(() => {}); }, []);

  const intraday: any[] = data?.trend ?? [];
  const delivery: any[] = data?.deliveryTrend ?? [];
  const committed: any[] = data?.committedTrend ?? [];
  const target: number = (data?.target ?? 0.9) * 100;
  const ov = data?.overall, ovd = data?.overallDelivery, ovc = data?.overallCommitted;

  const series = [
    { key: 'committed', label: 'High-conviction', color: '#a855f7', pts: committed, on: show.committed, overall: ovc },
    { key: 'intraday', label: 'Intraday', color: '#22c55e', pts: intraday, on: show.intraday, overall: ov },
    { key: 'delivery', label: 'Delivery', color: '#3b82f6', pts: delivery, on: show.delivery, overall: ovd },
  ];
  const allPts = series.filter(s => s.on).flatMap(s => s.pts);
  const hasData = allPts.length >= 1;

  const W = 600, H = 170, PL = 40, PR = 12, PT = 16, PB = 26;
  // x-axis = union of dates across both series, ordered
  const dates = Array.from(new Set([...intraday, ...delivery, ...committed].map(p => p.date))).filter(Boolean).sort();
  const xi = (d: string) => dates.length <= 1 ? PL + (W - PL - PR) / 2 : PL + (dates.indexOf(d) / (dates.length - 1)) * (W - PL - PR);
  const ys = [...allPts.map(p => p.accuracy * 100), target];
  const yMin = Math.max(0, Math.min(...ys) - 8), yMax = Math.min(100, Math.max(...ys) + 8);
  const sy = (v: number) => PT + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PT - PB);

  const latestAcc = (s: any) => s.pts.length ? s.pts[s.pts.length - 1].accuracy * 100 : null;
  const commAcc = ovc?.accuracy != null ? ovc.accuracy * 100 : null;
  const commBelow = commAcc != null && commAcc < target;

  return (
    <div className="nd-card" style={{ padding: '16px 18px', marginBottom: 0 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI Scan Accuracy</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Graded vs the actual move · <span style={{ color: '#a855f7' }}>High-conviction</span> is the selective tier tuned to the {target.toFixed(0)}% target</div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {series.map(s => {
            const la = latestAcc(s);
            return (
              <button key={s.key} onClick={() => setShow(p => ({ ...p, [s.key]: !(p as any)[s.key] }))} style={{
                padding: '4px 9px', borderRadius: 7, cursor: 'pointer', textAlign: 'right',
                border: `1px solid ${s.on ? s.color : 'var(--nd-border)'}`,
                background: s.on ? `${s.color}1a` : 'transparent', opacity: s.on ? 1 : 0.5,
              }}>
                <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{s.label}</div>
                <div style={{ fontSize: 15, fontWeight: 700, color: s.color }}>{la != null ? `${la.toFixed(0)}%` : '—'}</div>
              </button>
            );
          })}
        </div>
      </div>

      {!hasData ? (
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', padding: '24px 0' }}>
          No graded scans yet — the morning watchlist is graded after each close (delivery picks after a {data?.latest?.horizon_days ?? 5}-day hold).
        </div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 160 }} preserveAspectRatio="none">
          {[yMin, (yMin + yMax) / 2, yMax].map((v, i) => (
            <g key={i}>
              <line x1={PL} y1={sy(v)} x2={W - PR} y2={sy(v)} stroke="var(--nd-border)" strokeWidth="0.5" />
              <text x={4} y={sy(v) + 3} fontSize="9" fill="var(--nd-text-3)">{v.toFixed(0)}%</text>
            </g>
          ))}
          {/* target line */}
          <line x1={PL} y1={sy(target)} x2={W - PR} y2={sy(target)} stroke="#f59e0b" strokeWidth="1" strokeDasharray="4 3" opacity={0.8} />
          <text x={W - PR} y={sy(target) - 3} fontSize="9" fill="#f59e0b" textAnchor="end">target {target.toFixed(0)}%</text>
          {series.filter(s => s.on && s.pts.length).map(s => {
            const line = s.pts.map(p => `${xi(p.date).toFixed(1)},${sy(p.accuracy * 100).toFixed(1)}`).join(' ');
            return (
              <g key={s.key}>
                {s.pts.length > 1
                  ? <polyline points={line} fill="none" stroke={s.color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  : null}
                {s.pts.map((p, i) => (
                  <circle key={i} cx={xi(p.date)} cy={sy(p.accuracy * 100)} r="3"
                    fill={p.meetsTarget ? s.color : '#ef4444'} stroke={s.color} strokeWidth="1" />
                ))}
              </g>
            );
          })}
          {dates.length > 0 && [dates[0], dates[dates.length - 1]].map((d, k) => (
            <text key={k} x={xi(d)} y={H - 8} fontSize="9" fill="var(--nd-text-3)" textAnchor={k === 0 ? 'start' : 'end'}>{d}</text>
          ))}
        </svg>
      )}

      {commBelow ? (
        <div style={{ marginTop: 8, fontSize: 11, color: '#d8b4fe', background: '#a855f715', border: '1px solid #a855f733', borderRadius: 8, padding: '6px 9px' }}>
          The high-conviction tier is at {commAcc!.toFixed(0)}% vs the {target.toFixed(0)}% target — the selectivity bar auto-tightens each session (fewer, higher-confluence picks) to close the gap. Broad intraday/delivery accuracy stays ~50% by nature and isn't traded.
        </div>
      ) : commAcc != null ? (
        <div style={{ marginTop: 8, fontSize: 11, color: '#86efac', background: '#22c55e15', border: '1px solid #22c55e33', borderRadius: 8, padding: '6px 9px' }}>
          ✓ High-conviction tier at {commAcc.toFixed(0)}% — meeting the {target.toFixed(0)}% target. Only these committed picks are acted on.
        </div>
      ) : null}
    </div>
  );
};

// ── Pattern Recognition Model (dedicated, continuously-learning) ───────────────

const PatternModelCard: React.FC<{ embedded?: boolean }> = ({ embedded }) => {
  const [status, setStatus] = useState<any>(null);
  const [curve, setCurve] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setStatus(await apiService.patternModelStatus()); } catch {}
    try { const c = await apiService.patternModelCurve(); setCurve((c as any).data?.points ?? []); } catch {}
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const train = async () => {
    setBusy(true); setMsg(null);
    try { await apiService.trainPatternModel({ lookbackDays: 365, horizon: 3 }); setMsg('Training started — patterns only. Accuracy updates as it learns.'); }
    catch { setMsg('Could not start training.'); }
    setTimeout(() => { setBusy(false); load(); }, 2500);
  };

  const m = status?.model ?? {};
  const recent = m.recentAccuracy != null ? m.recentAccuracy * 100 : null;
  const lifetime = m.lifetimeAccuracy != null ? m.lifetimeAccuracy * 100 : null;
  const hcAcc = m.highConfAccuracy != null ? m.highConfAccuracy * 100 : null;
  const hcCov = m.highConfCoverage != null ? m.highConfCoverage * 100 : null;
  const slice = status?.lastTrain?.universeSlice;
  const running = status?.running;

  // sparkline of batch (generalisation) accuracy over training snapshots
  const pts = curve.filter(p => p.batchAccuracy != null);
  const W = 560, H = 90, PL = 30, PR = 8, PT = 8, PB = 14;
  const ys = pts.map(p => p.batchAccuracy * 100);
  const yMin = pts.length ? Math.max(0, Math.min(...ys) - 5) : 40;
  const yMax = pts.length ? Math.min(100, Math.max(...ys) + 5) : 60;
  const sx = (i: number) => PL + (pts.length <= 1 ? 0.5 : i / (pts.length - 1)) * (W - PL - PR);
  const sy = (v: number) => PT + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PT - PB);
  const line = pts.map((p, i) => `${sx(i).toFixed(1)},${sy(p.batchAccuracy * 100).toFixed(1)}`).join(' ');

  return (
    <div className={embedded ? undefined : 'nd-card'} style={{ padding: '16px 18px', marginBottom: embedded ? 0 : 20 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Pattern Recognition Model</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
            Learns price <span style={{ color: '#06b6d4' }}>patterns only</span> across the full NSE universe. <span style={{ color: '#a855f7' }}>High-confidence</span> = accuracy when the model is sure (it abstains otherwise)
          </div>
        </div>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#a855f7' }}>{hcAcc != null ? `${hcAcc.toFixed(1)}%` : '—'}</div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>high-confidence{hcCov != null ? ` · ${hcCov.toFixed(0)}% of picks` : ''}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#06b6d4' }}>{recent != null ? `${recent.toFixed(1)}%` : '—'}</div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>overall · {lifetime != null ? `${lifetime.toFixed(0)}% life` : 'untrained'}</div>
          </div>
          <button onClick={train} disabled={busy || running} style={{
            padding: '6px 12px', fontSize: 12, fontWeight: 600, borderRadius: 7, cursor: busy || running ? 'default' : 'pointer',
            border: '1px solid #06b6d4', background: busy || running ? 'transparent' : '#06b6d4',
            color: busy || running ? '#06b6d4' : '#fff', opacity: busy || running ? 0.6 : 1,
          }}>{running ? 'Training…' : busy ? 'Starting…' : 'Train now'}</button>
        </div>
      </div>

      {pts.length >= 2 ? (
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 90 }} preserveAspectRatio="none">
          {[yMin, (yMin + yMax) / 2, yMax].map((v, i) => (
            <g key={i}>
              <line x1={PL} y1={sy(v)} x2={W - PR} y2={sy(v)} stroke="var(--nd-border)" strokeWidth="0.5" />
              <text x={2} y={sy(v) + 3} fontSize="8" fill="var(--nd-text-3)">{v.toFixed(0)}%</text>
            </g>
          ))}
          <polyline points={line} fill="none" stroke="#06b6d4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx={sx(pts.length - 1)} cy={sy(pts[pts.length - 1].batchAccuracy * 100)} r="3" fill="#06b6d4" />
        </svg>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', padding: '14px 0' }}>
          {m.trained ? 'Learning — train again to extend the curve.' : 'Not trained yet. Click "Train now" (or let the backtest autopilot train it) to start pattern learning.'}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 4 }}>
        {m.nSamples != null ? `${m.nSamples.toLocaleString()} patterns learned` : ''}
        {pts.length ? ` · ${pts.length} rounds` : ''}
        {slice ? ` · universe ${slice}` : ''}
        {msg ? ` · ${msg}` : ''}
      </div>
    </div>
  );
};

// ── AI Watchlist tab (self-running scanner output + evidence) ──────────────────

const ACTION_BG: Record<string, string> = { BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b' };
const GRADE_COLOR: Record<string, string> = { A: '#22c55e', B: '#3b82f6', C: '#f59e0b', D: '#94a3b8' };
// Hold-cap presets (minutes) for inline auto-trading of a watchlist stock
const HOLD_CAPS = [15, 30, 60, 0] as const;   // 0 = no cap

const SignalScorePanel: React.FC<{ ev: any }> = ({ ev }) => {
  const [open, setOpen] = useState(false);
  const latest = ev?.latest;
  const overall = ev?.overall;
  const haveScore = latest || (overall && overall.accuracy != null);
  if (!haveScore) {
    return (
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 12, fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.5 }}>
        <span className="material-icons" style={{ fontSize: 17, color: 'var(--nd-blue)' }}>insights</span>
        <span>After the market closes, the scanner grades each morning pick against the actual day move and shows a <strong>signal score</strong> here — that accuracy then calibrates future scans so the system keeps learning.</span>
      </div>
    );
  }
  const acc = (latest?.accuracy ?? overall?.accuracy ?? 0) as number;
  const color = acc >= 0.6 ? 'var(--nd-green)' : acc >= 0.45 ? '#d98c00' : 'var(--nd-red)';
  const results: any[] = latest?.results ?? [];
  return (
    <div style={{ border: '1px solid var(--nd-border)', borderRadius: 12, marginBottom: 12, overflow: 'hidden', background: 'var(--nd-surface)' }}>
      <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px', cursor: 'pointer' }}>
        <span className="material-icons" style={{ fontSize: 19, color }}>verified</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>
            Last signal score{latest?.date ? ` · ${latest.date}` : ''}
          </div>
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
            {(latest?.picks ?? overall?.picks ?? 0)} picks graded vs the actual day move
            {overall?.days ? ` · ${overall.days}-day avg ${((overall.accuracy ?? 0) * 100).toFixed(0)}%` : ''}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 18, fontWeight: 800, color }}>{(acc * 100).toFixed(0)}%</div>
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>accuracy</div>
        </div>
        {results.length > 0 && (
          <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-3)' }}>{open ? 'expand_less' : 'expand_more'}</span>
        )}
      </div>
      {open && results.length > 0 && (
        <div style={{ borderTop: '1px solid var(--nd-border)', padding: '8px 14px 12px' }}>
          {latest?.avgRealizedReturnPct != null && (
            <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', marginBottom: 8 }}>
              Avg realised return in the predicted direction:&nbsp;
              <strong style={{ color: latest.avgRealizedReturnPct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                {latest.avgRealizedReturnPct >= 0 ? '+' : ''}{latest.avgRealizedReturnPct}%
              </strong>
            </div>
          )}
          {results.map((r) => (
            <div key={r.symbol} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12 }}>
              <span className="material-icons" style={{ fontSize: 15, color: r.correct ? 'var(--nd-green)' : 'var(--nd-red)' }}>{r.correct ? 'check_circle' : 'cancel'}</span>
              <span style={{ fontWeight: 700, color: 'var(--nd-text-1)', width: 90 }}>{r.symbol}</span>
              <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 4, background: `${ACTION_BG[r.action]}1a`, color: ACTION_BG[r.action] }}>{r.action}</span>
              <span style={{ flex: 1 }} />
              <span style={{ color: r.dayReturnPct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 600 }}>
                day {r.dayReturnPct >= 0 ? '+' : ''}{r.dayReturnPct}%
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ── Grade badge (A/B/C/D win-probability quality) ─────────────────────────────
const GradeBadge: React.FC<{ grade?: string; winProb?: number }> = ({ grade, winProb }) => {
  if (!grade) return null;
  const c = GRADE_COLOR[grade] ?? '#94a3b8';
  return (
    <span title={winProb != null ? `Win probability ${(winProb * 100).toFixed(0)}%` : 'Quality grade'}
      style={{ fontSize: 10, fontWeight: 800, padding: '1px 6px', borderRadius: 4, background: `${c}1f`, color: c, border: `1px solid ${c}55`, letterSpacing: 0.3 }}>
      {grade}{winProb != null ? ` · ${(winProb * 100).toFixed(0)}%` : ''}
    </span>
  );
};

// ── Shared stock row used across all watchlist tabs ───────────────────────────
const WatchlistRow: React.FC<{ w: any; i: number; onClick: () => void; badge?: React.ReactNode; onAutoTrade?: (sym: string) => void; tradingSym?: string | null }> = ({ w, i, onClick, badge, onAutoTrade, tradingSym }) => {
  const started = tradingSym === w.symbol;
  return (
  <div onClick={onClick}
    style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 4px', borderTop: i ? '1px solid var(--nd-border)' : 'none', cursor: 'pointer' }}>
    <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-3)', width: 20 }}>#{i + 1}</span>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>{w.symbol}</span>
        <GradeBadge grade={w.grade} winProb={w.winProbability} />
        {badge}
      </div>
      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{w.name}</div>
      {w.news && ((w.news.catalyst && w.news.catalyst !== 'none') || w.news.summary) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2, fontSize: 10.5, fontWeight: 600, color: ACTION_BG[w.news.action] ?? 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          <span className="material-icons" style={{ fontSize: 12, flexShrink: 0 }}>article</span>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {(w.news.catalyst && w.news.catalyst !== 'none') ? w.news.catalyst : w.news.summary}
          </span>
        </div>
      )}
    </div>
    <div style={{ textAlign: 'right', flexShrink: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)' }}>₹{w.price?.toLocaleString('en-IN')}</div>
      <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
        {w.winProbability != null ? `win ${(w.winProbability * 100).toFixed(0)}%` : `conf ${(w.confidence * 100).toFixed(0)}%`}
      </div>
    </div>
    <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 5, background: `${ACTION_BG[w.action]}1a`, color: ACTION_BG[w.action] }}>{w.action}</span>
    {onAutoTrade && (
      <button onClick={e => { e.stopPropagation(); onAutoTrade(w.symbol); }} disabled={started}
        title="Auto paper-trade this stock with the selected hold cap"
        style={{ display: 'flex', alignItems: 'center', gap: 3, padding: '4px 8px', borderRadius: 6, border: `1px solid ${started ? 'var(--nd-green)' : 'var(--nd-border)'}`, background: started ? 'var(--nd-green-50)' : 'var(--nd-surface)', color: started ? 'var(--nd-green)' : 'var(--nd-text-2)', cursor: started ? 'default' : 'pointer', fontSize: 11, fontWeight: 600, flexShrink: 0 }}>
        <span className="material-icons" style={{ fontSize: 13 }}>{started ? 'check' : 'play_arrow'}</span>
        {started ? 'Started' : 'Auto'}
      </button>
    )}
    <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>chevron_right</span>
  </div>
  );
};

// What changed between the last two completed scans — rank moves, new entrants,
// drop-offs, each with the reason derived from the scoring components.
const ScanDiffPanel: React.FC<{ diff: any }> = ({ diff }) => {
  const [open, setOpen] = useState(false);
  if (!diff) return null;
  if (!diff.available) {
    return (
      <div className="nd-card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 12, color: 'var(--nd-text-3)' }}>
        {diff.message || 'No previous scan to compare yet.'}
      </div>
    );
  }
  const moved: any[] = diff.moved ?? [];
  const entered: any[] = diff.entered ?? [];
  const dropped: any[] = diff.dropped ?? [];
  const ups = moved.filter(m => m.direction === 'up');
  const downs = moved.filter(m => m.direction === 'down');
  const c = diff.counts ?? { moved: moved.length, entered: entered.length, dropped: dropped.length };

  const Row: React.FC<{ m: any; kind: 'up' | 'down' | 'in' | 'out' }> = ({ m, kind }) => {
    const color = kind === 'up' ? '#22c55e' : kind === 'down' ? '#ef4444' : kind === 'in' ? '#3b82f6' : '#94a3b8';
    const badge = kind === 'up' ? `▲ ${m.delta}` : kind === 'down' ? `▼ ${Math.abs(m.delta)}` : kind === 'in' ? 'NEW' : 'OUT';
    return (
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4px 0', borderBottom: '1px solid var(--nd-border)' }}>
        <span style={{ fontSize: 10, fontWeight: 700, color, minWidth: 38 }}>{badge}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)', minWidth: 80 }}>{m.symbol}</span>
        <span style={{ fontSize: 11, color: 'var(--nd-text-3)', minWidth: 78 }}>
          {kind === 'out' ? `was #${m.prevRank}` : kind === 'in' ? `#${m.rank}` : `#${m.prevRank}→#${m.rank}`}
        </span>
        <span style={{ fontSize: 11, color: 'var(--nd-text-2)' }}>{m.reason || ''}</span>
      </div>
    );
  };

  return (
    <div className="nd-card" style={{ padding: '12px 14px', marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', gap: 8 }}
        onClick={() => setOpen(o => !o)}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>What changed since the last scan</div>
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
            <span style={{ color: '#22c55e' }}>▲ {ups.length}</span> · <span style={{ color: '#ef4444' }}>▼ {downs.length}</span>
            {' · '}<span style={{ color: '#3b82f6' }}>{c.entered} new</span> · <span style={{ color: '#94a3b8' }}>{c.dropped} dropped</span>
          </div>
        </div>
        <span className="material-icons" style={{ color: 'var(--nd-text-3)' }}>{open ? 'expand_less' : 'expand_more'}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#22c55e', marginBottom: 4 }}>Climbed ({ups.length})</div>
            {ups.length ? ups.slice(0, 12).map((m, i) => <Row key={i} m={m} kind="up" />) : <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</div>}
            <div style={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', margin: '10px 0 4px' }}>Entered the board ({c.entered})</div>
            {entered.length ? entered.slice(0, 10).map((m, i) => <Row key={i} m={m} kind="in" />) : <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</div>}
          </div>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#ef4444', marginBottom: 4 }}>Slipped ({downs.length})</div>
            {downs.length ? downs.slice(0, 12).map((m, i) => <Row key={i} m={m} kind="down" />) : <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</div>}
            <div style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', margin: '10px 0 4px' }}>Dropped off ({c.dropped})</div>
            {dropped.length ? dropped.slice(0, 10).map((m, i) => <Row key={i} m={m} kind="out" />) : <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</div>}
          </div>
        </div>
      )}
    </div>
  );
};

const AiWatchlistTab: React.FC = () => {
  const [data, setData]         = useState<any>(null);
  const [evalData, setEvalData] = useState<any>(null);
  const [diff, setDiff]         = useState<any>(null);
  const [sel, setSel]           = useState<any>(null);
  const [tab, setTab]           = useState<'intraday' | 'delivery' | 'fno'>('intraday');
  const [holdCap, setHoldCap]   = useState<number>(30);     // per-trade hold cap (min)
  const [tradingSym, setTradingSym] = useState<string | null>(null);
  const [autoMsg, setAutoMsg]   = useState<string | null>(null);

  const startAuto = useCallback(async (sym: string) => {
    setAutoMsg(null);
    try {
      await apiService.sessionStart({ mode: 'paper', symbol: sym, capital: 50000, max_hold_minutes: holdCap });
      setTradingSym(sym);
      setAutoMsg(`Auto paper-trading ${sym} — exits any position after ${holdCap ? `${holdCap}m` : 'EOD'}.`);
      setTimeout(() => setAutoMsg(null), 6000);
    } catch (e: any) {
      setAutoMsg(`Could not start auto-trade for ${sym}: ${e?.response?.data?.detail || e?.message || 'error'}`);
      setTimeout(() => setAutoMsg(null), 6000);
    }
  }, [holdCap]);

  const load = useCallback(async () => {
    try { const r = await apiService.aiWatchlist(); setData((r as any).data); } catch {}
    try { const e = await apiService.scanEvaluation(); setEvalData((e as any).data); } catch {}
    try { const d = await apiService.scanDiff(); setDiff((d as any).data); } catch {}
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  const intraday: any[] = data?.intraday ?? data?.items ?? [];
  const delivery: any[] = data?.delivery ?? [];
  const fno: any[]      = data?.fno ?? [];

  const tabs = [
    { key: 'intraday', label: 'Best Intraday',  icon: 'bolt',       count: intraday.length },
    { key: 'delivery', label: 'Best Delivery',  icon: 'calendar_month', count: delivery.length },
    { key: 'fno',      label: 'Best F&O',        icon: 'auto_graph', count: fno.length },
  ] as const;

  const activeItems = tab === 'intraday' ? intraday : tab === 'delivery' ? delivery : fno;

  return (
    <div>
      {/* Header — centralized scan status + rescan */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <ScanControl align="right" />
      </div>

      <SignalScorePanel ev={evalData} />
      <ScanDiffPanel diff={diff} />

      {/* Category tabs */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 14, borderBottom: '1px solid var(--nd-border)', paddingBottom: 0 }}>
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '8px 14px', borderRadius: '8px 8px 0 0',
              border: '1px solid var(--nd-border)', borderBottom: tab === t.key ? '2px solid var(--nd-green)' : '1px solid var(--nd-border)',
              background: tab === t.key ? 'var(--nd-surface)' : 'transparent',
              cursor: 'pointer', fontSize: 12, fontWeight: tab === t.key ? 700 : 500,
              color: tab === t.key ? 'var(--nd-green)' : 'var(--nd-text-2)',
              marginBottom: -1,
            }}>
            <span className="material-icons" style={{ fontSize: 14 }}>{t.icon}</span>
            {t.label}
            {t.count > 0 && (
              <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 5px', borderRadius: 8, background: tab === t.key ? 'var(--nd-green)' : 'var(--nd-border)', color: tab === t.key ? '#fff' : 'var(--nd-text-3)' }}>{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab descriptions */}
      {tab === 'intraday' && (
        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 10, padding: '6px 10px', background: 'var(--nd-bg)', borderRadius: 6, borderLeft: '3px solid var(--nd-green)' }}>
          High-momentum stocks with above-average volume — suitable for same-day trades. Grades A/B are the highest win-probability setups. Click for full evidence.
        </div>
      )}

      {/* Top Conviction Picks + auto-trade controls (intraday only) */}
      {tab === 'intraday' && intraday.length > 0 && (() => {
        const top = intraday.filter((w: any) => w.grade === 'A' || w.grade === 'B').slice(0, 5);
        return (
          <div style={{ border: '1px solid var(--nd-border)', borderRadius: 12, padding: '12px 14px', marginBottom: 12, background: 'linear-gradient(135deg, rgba(34,197,94,0.06), rgba(59,130,246,0.05))' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: top.length ? 10 : 0 }}>
              <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-green)' }}>workspace_premium</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>Top Conviction Picks</span>
              {data?.gradeCounts && (
                <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                  {data.gradeCounts.A ?? 0}×A · {data.gradeCounts.B ?? 0}×B · {data.gradeCounts.C ?? 0}×C
                </span>
              )}
              <span style={{ flex: 1 }} />
              {/* Hold-cap selector */}
              <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Hold cap</span>
              <div style={{ display: 'flex', gap: 2, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: 2 }}>
                {HOLD_CAPS.map(h => (
                  <button key={h} onClick={() => setHoldCap(h)}
                    style={{ padding: '3px 9px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 11, fontWeight: 600,
                      background: holdCap === h ? 'var(--nd-green)' : 'transparent', color: holdCap === h ? '#fff' : 'var(--nd-text-2)' }}>
                    {h === 0 ? 'EOD' : `${h}m`}
                  </button>
                ))}
              </div>
            </div>
            {top.length > 0 ? (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {top.map((w: any) => (
                  <div key={w.symbol} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8 }}>
                    <GradeBadge grade={w.grade} winProb={w.winProbability} />
                    <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>{w.symbol}</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: ACTION_BG[w.action] }}>{w.action}</span>
                    <button onClick={() => startAuto(w.symbol)} disabled={tradingSym === w.symbol}
                      style={{ display: 'flex', alignItems: 'center', gap: 3, padding: '3px 8px', borderRadius: 6, border: `1px solid ${tradingSym === w.symbol ? 'var(--nd-green)' : 'var(--nd-border)'}`, background: tradingSym === w.symbol ? 'var(--nd-green-50)' : 'var(--nd-bg)', color: tradingSym === w.symbol ? 'var(--nd-green)' : 'var(--nd-text-2)', cursor: tradingSym === w.symbol ? 'default' : 'pointer', fontSize: 11, fontWeight: 600 }}>
                      <span className="material-icons" style={{ fontSize: 13 }}>{tradingSym === w.symbol ? 'check' : 'play_arrow'}</span>
                      {tradingSym === w.symbol ? 'Started' : 'Auto'}
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>No A/B-grade setups right now — only high win-probability names appear here.</div>
            )}
            {autoMsg && (
              <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--nd-text-2)', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '7px 10px' }}>
                {autoMsg}
              </div>
            )}
          </div>
        );
      })()}
      {tab === 'delivery' && (
        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 10, padding: '6px 10px', background: 'var(--nd-bg)', borderRadius: 6, borderLeft: '3px solid var(--nd-blue)' }}>
          Stocks in a confirmed uptrend with moderate volatility — suitable for multi-week holding. The <strong style={{ color: 'var(--nd-text-2)' }}>Safe ~X wks</strong> badge is the AI's estimated safe holding window.
        </div>
      )}
      {tab === 'fno' && (
        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 10, padding: '6px 10px', background: 'var(--nd-bg)', borderRadius: 6, borderLeft: '3px solid #a78bfa' }}>
          F&O-eligible stocks with a directional signal. Each row shows the recommended option (CE/PE), strike, expiry, and estimated safe holding days.
        </div>
      )}

      {/* Stock list */}
      {activeItems.length === 0 ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-text-3)', fontSize: 13 }}>
          {data ? 'No qualifying stocks in this category right now.' : 'The market scanner is warming up — results will appear shortly.'}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {activeItems.map((w: any, i: number) => {
            // ── Delivery badge ──────────────────────────────────────────────
            const deliveryBadge = tab === 'delivery' && w.deliveryWeeks > 0 ? (
              <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 8, background: 'rgba(59,130,246,0.12)', color: 'var(--nd-blue)', flexShrink: 0 }}>
                Safe ~{w.deliveryWeeks} wk{w.deliveryWeeks > 1 ? 's' : ''}
              </span>
            ) : null;

            // ── FNO badge ───────────────────────────────────────────────────
            const rec = w.fnoRecommendation;
            const fnoBadge = tab === 'fno' && rec ? (
              <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 8, background: 'rgba(167,139,250,0.15)', color: '#a78bfa', flexShrink: 0 }}>
                {rec.optionType} {rec.strike} · {rec.expiry} · {rec.safeDays}d
              </span>
            ) : null;

            return (
              <div key={w.symbol}>
                <WatchlistRow w={w} i={i} onClick={() => setSel(w)} badge={deliveryBadge ?? fnoBadge}
                  onAutoTrade={tab === 'intraday' ? startAuto : undefined} tradingSym={tradingSym} />
                {/* FNO rationale row */}
                {tab === 'fno' && rec && (
                  <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', padding: '0 4px 8px 52px', lineHeight: 1.5 }}>
                    {rec.rationale}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Metrics summary row for active tab */}
      {activeItems.length > 0 && tab === 'delivery' && (
        <div style={{ marginTop: 12, padding: '8px 12px', background: 'var(--nd-bg)', borderRadius: 8, fontSize: 11, color: 'var(--nd-text-3)', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          <span>Avg holding: <strong style={{ color: 'var(--nd-text-2)' }}>
            {Math.round(activeItems.reduce((s: number, i: any) => s + (i.deliveryWeeks || 0), 0) / activeItems.length)} wks
          </strong></span>
          <span>All in uptrend: <strong style={{ color: 'var(--nd-green)' }}>
            {activeItems.filter((i: any) => i.metrics?.smaTrend === 'up').length}/{activeItems.length}
          </strong></span>
        </div>
      )}

      {sel && <WatchlistEvidence stock={sel} scannedAt={data?.updatedAt} onClose={() => setSel(null)} />}
    </div>
  );
};

const WatchlistEvidence: React.FC<{ stock: any; scannedAt?: string; onClose: () => void }> = ({ stock, scannedAt, onClose }) => (
  <div onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}>
    <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 500, maxHeight: '88vh', overflow: 'auto', boxShadow: '0 24px 64px #00000060' }}>
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>{stock.symbol}</span>
          <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 5, background: `${ACTION_BG[stock.action]}1a`, color: ACTION_BG[stock.action] }}>{stock.action}</span>
          <GradeBadge grade={stock.grade} winProb={stock.winProbability} />
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer' }}><span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 20 }}>close</span></button>
      </div>
      <div style={{ padding: '14px 20px' }}>
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: 'var(--nd-text-2)', lineHeight: 1.6 }}>
          Why the AI picked this for <strong>intraday</strong>: the scanner judged a
          <strong> {stock.action}</strong> view at <strong>{(stock.confidence * 100).toFixed(0)}%</strong> confidence
          {stock.signalScore != null ? <> (signal score <strong>{Math.round(stock.signalScore)}</strong>)</> : null}.
          It weighs every indicator that moves price — liquidity, volatility, trend, momentum, MACD, RSI, the opening gap and the broader market — and only stocks that clear the liquidity + volatility bar make the list.
        </p>
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px', marginBottom: 12 }}>{stock.reasoning}</div>
        {stock.news && (
          <div style={{ border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 12, background: 'var(--nd-surface)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span className="material-icons" style={{ fontSize: 15, color: ACTION_BG[stock.news.action] ?? 'var(--nd-text-3)' }}>article</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', letterSpacing: 0.3 }}>NEWS SENTIMENT · LLM</span>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 4, background: `${ACTION_BG[stock.news.action]}1a`, color: ACTION_BG[stock.news.action] }}>{stock.news.action}</span>
              {stock.news.confidence != null && <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{(stock.news.confidence * 100).toFixed(0)}%</span>}
            </div>
            {stock.news.summary && <div style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.5, marginBottom: 6 }}>{stock.news.summary}</div>}
            {stock.news.catalyst && stock.news.catalyst !== 'none' && (
              <div style={{ fontSize: 11.5, marginBottom: 6 }}>
                <span style={{ color: 'var(--nd-text-3)' }}>Catalyst: </span>
                <strong style={{ color: ACTION_BG[stock.news.action] ?? 'var(--nd-text-1)' }}>{stock.news.catalyst}</strong>
              </div>
            )}
            {Array.isArray(stock.news.topHeadlines) && stock.news.topHeadlines.length > 0 && (
              <div style={{ marginTop: 4 }}>
                {stock.news.topHeadlines.map((h: string, i: number) => (
                  <div key={i} style={{ display: 'flex', gap: 6, fontSize: 11, color: 'var(--nd-text-3)', padding: '3px 0' }}>
                    <span style={{ color: 'var(--nd-text-4, var(--nd-text-3))' }}>•</span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{h}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 6 }}>
              {stock.news.headlinesCount} headlines{stock.news.updatedAt ? ` · ${new Date(stock.news.updatedAt).toLocaleString()}` : ''}
            </div>
          </div>
        )}
        {stock.metrics && (<>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', marginBottom: 8 }}>MARKET-INDICATOR EVIDENCE</div>
          {[
            ['Avg daily volume', `${(stock.metrics.avgVolume / 1e6).toFixed(2)}M`],
            ['Relative volume', stock.metrics.relVolume != null ? `${stock.metrics.relVolume}×` : '—'],
            ['Volatility (ATR)', `${stock.metrics.atrPct}%`],
            ['Avg daily range', `${stock.metrics.rangePct}%`],
            ['Trend (SMA20/50)', stock.metrics.smaTrend ? `${stock.metrics.smaTrend}${stock.metrics.sma20 ? ` · 20: ${stock.metrics.sma20}` : ''}` : '—'],
            ['MACD histogram', stock.metrics.macdHist != null ? `${stock.metrics.macdHist >= 0 ? '+' : ''}${stock.metrics.macdHist}` : '—'],
            ['RSI (14)', `${stock.metrics.rsi}`],
            ['Momentum (10d)', `${stock.metrics.momentumPct >= 0 ? '+' : ''}${stock.metrics.momentumPct}%`],
            ['Opening gap', stock.metrics.gapPct != null ? `${stock.metrics.gapPct >= 0 ? '+' : ''}${stock.metrics.gapPct}%` : '—'],
            ['Room to 20d high', stock.metrics.distFromHighPct != null ? `${stock.metrics.distFromHighPct}%` : '—'],
            ['Market regime', stock.metrics.marketRegime ?? '—'],
            ['Liquidity score', `${(stock.metrics.liquidityScore * 100).toFixed(0)}%`],
            ['Volatility score', `${(stock.metrics.volatilityScore * 100).toFixed(0)}%`],
          ].map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12 }}>
              <span style={{ color: 'var(--nd-text-3)' }}>{k}</span>
              <span style={{ color: 'var(--nd-text-1)', fontWeight: 600 }}>{v}</span>
            </div>
          ))}
        </>)}
        {Array.isArray(stock.agents) && stock.agents.length > 0 && (<>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', margin: '12px 0 8px' }}>AGENT BREAKDOWN</div>
          {stock.agents.map((a: any) => (
            <div key={a.agent} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--nd-border)' }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--nd-text-1)', textTransform: 'capitalize', width: 84 }}>{a.agent}</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: ACTION_BG[a.action] ?? 'var(--nd-text-2)', width: 40 }}>{a.action}</span>
              <span style={{ fontSize: 11, color: 'var(--nd-text-3)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.reasoning}</span>
            </div>
          ))}
        </>)}
        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--nd-text-3)' }}>
          Live scan from the stock-scanner service · {scannedAt ? new Date(scannedAt).toLocaleString() : ''}. No hard-coded values.
        </div>
      </div>
    </div>
  </div>
);

// ── System Startup Modal ──────────────────────────────────────────────────────

type SvcStatus = 'checking' | 'ok' | 'error';
interface SvcState { name: string; icon: string; status: SvcStatus; }

const MICROSERVICE_NAMES = [
  'Market Data', 'Technical Agent', 'Sentiment Agent', 'Macro Agent',
  'Pattern Agent', 'RL Agent', 'Ensemble Engine', 'Feedback Service', 'Model Trainer',
];

const INITIAL_SVCS: SvcState[] = [
  { name: 'Backend',          icon: 'dns',             status: 'checking' },
  { name: 'Market Data',      icon: 'candlestick_chart', status: 'checking' },
  { name: 'Technical Agent',  icon: 'show_chart',      status: 'checking' },
  { name: 'Sentiment Agent',  icon: 'article',         status: 'checking' },
  { name: 'Macro Agent',      icon: 'public',          status: 'checking' },
  { name: 'Pattern Agent',    icon: 'pattern',         status: 'checking' },
  { name: 'RL Agent',         icon: 'smart_toy',       status: 'checking' },
  { name: 'Ensemble Engine',  icon: 'hub',             status: 'checking' },
  { name: 'Feedback Service', icon: 'feedback',        status: 'checking' },
  { name: 'Model Trainer',    icon: 'model_training',  status: 'checking' },
  { name: 'LLM',              icon: 'psychology',      status: 'checking' },
];

// Shared poll logic extracted so both modal and status icon can reuse it
async function pollServices(): Promise<SvcState[]> {
  const next: SvcState[] = INITIAL_SVCS.map(s => ({ ...s, status: 'checking' as SvcStatus }));
  const set = (name: string, status: SvcStatus) => {
    const s = next.find(x => x.name === name);
    if (s) s.status = status;
  };
  await Promise.allSettled([
    apiService.healthCheck()
      .then(() => set('Backend', 'ok'))
      .catch(() => set('Backend', 'error')),
    apiService.getServicesHealth()
      .then(r => {
        const list: any[] = (r as any).data ?? r ?? [];
        for (const svc of list)
          if (MICROSERVICE_NAMES.includes(svc.name))
            set(svc.name, svc.status === 'ok' ? 'ok' : 'error');
        MICROSERVICE_NAMES.forEach(n => {
          const s = next.find(x => x.name === n);
          if (s && s.status === 'checking') s.status = 'error';
        });
      })
      .catch(() => MICROSERVICE_NAMES.forEach(n => set(n, 'error'))),
    apiService.getLlmStatus()
      .then(r => { const d = (r as any).data ?? r; set('LLM', d?.available ? 'ok' : 'error'); })
      .catch(() => set('LLM', 'error')),
  ]);
  return next;
}

const SystemStartupModal: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [svcs, setSvcs] = useState<SvcState[]>(INITIAL_SVCS.map(s => ({ ...s })));
  const [allLive, setAllLive] = useState(false);
  const doneRef = useRef(false);

  const poll = useCallback(async () => {
    if (doneRef.current) return;
    const next = await pollServices();
    setSvcs([...next]);
    if (next.every(s => s.status === 'ok') && !doneRef.current) {
      doneRef.current = true;
      setAllLive(true);
      setTimeout(onClose, 1600);
    }
  }, [onClose]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 2500);
    return () => clearInterval(id);
  }, [poll]);

  const okCount = svcs.filter(s => s.status === 'ok').length;
  const pct     = (okCount / svcs.length) * 100;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 2000,
      background: 'rgba(2,6,23,0.82)',
      backdropFilter: 'blur(14px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
    }}>
      {/* Animated gradient border wrapper */}
      <div className={`nd-startup-border${allLive ? ' nd-live' : ''}`}>
        {/* Dark inner panel */}
        <div style={{
          background: 'linear-gradient(160deg, #080d1a 0%, #0b1120 55%, #060a14 100%)',
          borderRadius: 19, overflow: 'hidden', position: 'relative',
        }}>

          {/* Subtle grid background */}
          <div style={{
            position: 'absolute', inset: 0, pointerEvents: 'none',
            backgroundImage: `
              linear-gradient(rgba(124,58,237,0.045) 1px, transparent 1px),
              linear-gradient(90deg, rgba(124,58,237,0.045) 1px, transparent 1px)
            `,
            backgroundSize: '36px 36px',
          }} />

          {/* ── Header ── */}
          <div style={{ padding: '28px 28px 22px', position: 'relative', zIndex: 1 }}>

            <div style={{ display: 'flex', alignItems: 'center', gap: 20, marginBottom: 22 }}>

              {/* Neural pulse icon */}
              <div style={{ position: 'relative', width: 58, height: 58, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {[0, 1].map(i => (
                  <div key={i} style={{
                    position: 'absolute', inset: 0, borderRadius: '50%',
                    border: `1px solid rgba(${i === 0 ? '124,58,237' : '6,182,212'},0.5)`,
                    animation: `nd-pulse-ring 2.6s ease-out infinite ${i * 1.3}s`,
                  }} />
                ))}
                <div style={{
                  position: 'absolute', inset: 5, borderRadius: '50%',
                  background: 'linear-gradient(135deg, rgba(124,58,237,0.18), rgba(6,182,212,0.14))',
                  border: '1px solid rgba(124,58,237,0.35)',
                  boxShadow: '0 0 24px rgba(124,58,237,0.28), inset 0 0 14px rgba(124,58,237,0.1)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  animation: 'nd-float 4s ease-in-out infinite',
                }}>
                  <span className="material-icons" style={{
                    fontSize: 22,
                    background: allLive ? '#00b386' : 'linear-gradient(135deg,#a78bfa,#67e8f9)',
                    WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
                    backgroundClip: 'text',
                  }}>{allLive ? 'verified' : 'hub'}</span>
                </div>
              </div>

              {/* Title block */}
              <div>
                <div style={{ fontSize: 10.5, letterSpacing: 3.5, color: 'rgba(167,139,250,0.65)', marginBottom: 5, fontWeight: 700 }}>
                  NEURADEX AI
                </div>
                <div style={{
                  fontSize: 19, fontWeight: 800, letterSpacing: 0.4, lineHeight: 1.15,
                  background: allLive ? '#00b386' : 'linear-gradient(120deg,#e2d9f3 0%,#a5f3fc 100%)',
                  WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text',
                }}>
                  {allLive ? 'All Systems Live' : 'Initializing Systems'}
                </div>
                <div style={{ fontSize: 11.5, color: 'rgba(148,163,184,0.6)', marginTop: 4, letterSpacing: 0.4 }}>
                  {allLive
                    ? 'NeuradeX is ready — closing automatically'
                    : `${okCount} of ${svcs.length} services operational`}
                </div>
              </div>
            </div>

            {/* Progress bar */}
            <div style={{ height: 3, borderRadius: 2, background: 'rgba(255,255,255,0.07)', overflow: 'hidden' }}>
              <div style={{
                height: '100%', borderRadius: 2,
                width: `${pct}%`,
                background: allLive
                  ? '#00b386'
                  : 'linear-gradient(90deg, #7c3aed 0%, #06b6d4 50%, #a78bfa 100%)',
                backgroundSize: '200% 100%',
                animation: !allLive ? 'nd-shimmer-bar 2s linear infinite' : 'none',
                transition: 'width 0.5s cubic-bezier(0.4,0,0.2,1)',
                boxShadow: allLive ? '0 0 10px rgba(0,179,134,0.7)' : '0 0 8px rgba(124,58,237,0.55)',
              }} />
            </div>
          </div>

          {/* Gradient divider */}
          <div style={{ height: 1, background: 'linear-gradient(90deg,transparent,rgba(124,58,237,0.35),rgba(6,182,212,0.35),transparent)' }} />

          {/* ── Service rows ── */}
          <div style={{ maxHeight: '50vh', overflow: 'auto', padding: '6px 0' }}>
            {svcs.map((svc, i) => (
              <div key={svc.name} style={{
                display: 'flex', alignItems: 'center', gap: 14,
                padding: '9px 28px',
                borderBottom: i < svcs.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                animation: 'nd-row-slide 0.35s ease both',
                animationDelay: `${i * 35}ms`,
                transition: 'background 0.15s',
                cursor: 'default',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >
                {/* Status dot with ping ring */}
                <div style={{ position: 'relative', width: 20, height: 20, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  {svc.status === 'ok' && (
                    <div style={{
                      position: 'absolute', inset: 0, borderRadius: '50%',
                      background: 'rgba(0,179,134,0.25)',
                      animation: 'nd-dot-ping 2s ease-out infinite',
                    }} />
                  )}
                  <div style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: svc.status === 'ok' ? '#00b386'
                      : svc.status === 'error' ? '#f59e0b'
                      : 'rgba(148,163,184,0.35)',
                    boxShadow: svc.status === 'ok' ? '0 0 8px rgba(0,179,134,0.9)'
                      : svc.status === 'error' ? '0 0 7px rgba(245,158,11,0.7)'
                      : 'none',
                    animation: svc.status === 'checking' ? 'nd-dot-blink 1.4s ease-in-out infinite' : 'none',
                    transition: 'all 0.3s ease',
                  }} />
                </div>

                {/* Service icon */}
                <span className="material-icons" style={{
                  fontSize: 14,
                  color: svc.status === 'ok' ? 'rgba(0,179,134,0.65)'
                    : svc.status === 'error' ? 'rgba(245,158,11,0.65)'
                    : 'rgba(148,163,184,0.3)',
                  transition: 'color 0.3s',
                }}>{svc.icon}</span>

                {/* Name */}
                <span style={{
                  flex: 1, fontSize: 13, fontWeight: 500, letterSpacing: 0.2,
                  color: svc.status === 'ok' ? 'rgba(226,232,240,0.9)'
                    : svc.status === 'error' ? 'rgba(226,232,240,0.6)'
                    : 'rgba(148,163,184,0.45)',
                  transition: 'color 0.3s',
                }}>{svc.name}</span>

                {/* Status badge */}
                <span style={{
                  fontSize: 9.5, fontWeight: 700, letterSpacing: 1.4,
                  padding: '3px 8px', borderRadius: 4,
                  background: svc.status === 'ok' ? 'rgba(0,179,134,0.13)'
                    : svc.status === 'error' ? 'rgba(245,158,11,0.1)'
                    : 'rgba(148,163,184,0.07)',
                  color: svc.status === 'ok' ? '#00b386'
                    : svc.status === 'error' ? '#f59e0b'
                    : 'rgba(148,163,184,0.45)',
                  border: `1px solid ${svc.status === 'ok' ? 'rgba(0,179,134,0.28)'
                    : svc.status === 'error' ? 'rgba(245,158,11,0.22)'
                    : 'rgba(148,163,184,0.1)'}`,
                  transition: 'all 0.3s ease',
                }}>
                  {svc.status === 'ok' ? 'LIVE' : svc.status === 'error' ? 'WAITING' : 'INIT'}
                </span>
              </div>
            ))}
          </div>

          {/* Footer */}
          {!allLive && (
            <div style={{
              padding: '11px 28px', position: 'relative', zIndex: 1,
              borderTop: '1px solid rgba(255,255,255,0.04)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <span style={{ fontSize: 10.5, color: 'rgba(148,163,184,0.35)', letterSpacing: 0.5 }}>
                Closes automatically when all systems are online
              </span>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                {[0, 1, 2].map(i => (
                  <div key={i} style={{
                    width: 4, height: 4, borderRadius: '50%',
                    background: 'rgba(124,58,237,0.55)',
                    animation: `nd-dot-blink 1.4s ease-in-out infinite ${i * 0.22}s`,
                  }} />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ── Performance + market-regime hero strip ────────────────────────────────────

const REGIME_STYLE: Record<string, { color: string; label: string; icon: string }> = {
  bullish: { color: '#22c55e', label: 'Risk-On · Bullish',  icon: 'trending_up' },
  bearish: { color: '#ef4444', label: 'Risk-Off · Bearish', icon: 'trending_down' },
  neutral: { color: '#f59e0b', label: 'Neutral',            icon: 'trending_flat' },
};

const PerformanceRegimeStrip: React.FC = () => {
  const [pm, setPm] = useState<any>(null);
  const [regime, setRegime] = useState<string>('neutral');

  useEffect(() => {
    apiService.getPortfolioMetrics().then((r: any) => { if (r && !r.error) setPm(r); }).catch(() => {});
    apiService.aiWatchlist().then((r: any) => { const d = r?.data; if (d?.marketRegime) setRegime(d.marketRegime); }).catch(() => {});
  }, []);

  const rg = REGIME_STYLE[regime] ?? REGIME_STYLE.neutral;
  const tiles = pm && pm.totalTrades > 0 ? [
    { label: 'Win Rate',     value: `${(pm.winRate * 100).toFixed(0)}%`,            good: pm.winRate >= 0.5 },
    { label: 'Sharpe',       value: pm.sharpeRatio?.toFixed(2),                      good: pm.sharpeRatio >= 1 },
    { label: 'Max DD',       value: `${pm.maxDrawdownPct?.toFixed(1)}%`,             good: pm.maxDrawdownPct < 15 },
    { label: 'Total Return', value: `${pm.totalReturnPct >= 0 ? '+' : ''}${pm.totalReturnPct?.toFixed(1)}%`, good: pm.totalReturnPct >= 0 },
  ] : [];

  return (
    <div className="nd-card" style={{ padding: '12px 16px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
      {/* Market regime chip */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingRight: 16, borderRight: tiles.length ? '1px solid var(--nd-border)' : 'none' }}>
        <span className="material-icons" style={{ fontSize: 20, color: rg.color }}>{rg.icon}</span>
        <div>
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.6 }}>Market Regime</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: rg.color }}>{rg.label}</div>
        </div>
      </div>
      {tiles.length > 0 ? tiles.map(t => (
        <div key={t.label}>
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.6 }}>{t.label}</div>
          <div style={{ fontSize: 16, fontWeight: 700, color: t.good ? 'var(--nd-green)' : 'var(--nd-red)' }}>{t.value}</div>
        </div>
      )) : (
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Live performance appears here once trades are recorded.</div>
      )}
      {pm && pm.totalTrades > 0 && (
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--nd-text-3)' }}>{pm.totalTrades} closed trades</span>
      )}
    </div>
  );
};

// ── Live auto-trading sessions (open positions + intraday P&L) ─────────────────

const LiveSessionsPanel: React.FC = () => {
  const [sessions, setSessions] = useState<any[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await apiService.sessionList();
      const all: any[] = (r as any).data ?? [];
      setSessions(all.filter(s => s.status === 'running'));
    } catch { /* keep last */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  const stop = async (id: string) => {
    setBusy(id);
    try { await apiService.sessionStop(id); await load(); } catch { /* ignore */ } finally { setBusy(null); }
  };

  if (!sessions.length) return null;
  const totalPnl = sessions.reduce((s, x) => s + (x.pnl ?? 0), 0);

  return (
    <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-green)' }}>monitoring</span>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Live Auto-Trading</div>
        <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{sessions.length} running</span>
        <span style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 700, color: totalPnl >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
          {totalPnl >= 0 ? '+' : ''}{inr(totalPnl)}
        </span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: 540, borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: 'var(--nd-text-3)', textAlign: 'left' }}>
              {['Symbol', 'Mode', 'Position', 'Hold cap', 'P&L', 'Trades', ''].map(h => (
                <th key={h} style={{ padding: '6px 10px', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sessions.map(s => (
              <tr key={s.id} style={{ borderTop: '1px solid var(--nd-border)' }}>
                <td style={{ padding: '7px 10px', fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.symbol}</td>
                <td style={{ padding: '7px 10px' }}>
                  <span style={{ fontSize: 10, fontWeight: 600, padding: '2px 6px', borderRadius: 4, background: s.mode === 'paper' ? 'rgba(245,158,11,0.15)' : 'rgba(59,130,246,0.15)', color: s.mode === 'paper' ? '#f59e0b' : '#3b82f6' }}>{(s.mode || '').toUpperCase()}</span>
                </td>
                <td style={{ padding: '7px 10px' }}>
                  <span style={{ fontWeight: 600, color: s.position === 'LONG' ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>{s.position ?? 'NONE'}</span>
                </td>
                <td style={{ padding: '7px 10px', color: 'var(--nd-text-2)' }}>{s.maxHoldMinutes ? `${s.maxHoldMinutes}m` : '—'}</td>
                <td style={{ padding: '7px 10px', fontWeight: 600, color: (s.pnl ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                  {(s.pnl ?? 0) >= 0 ? '+' : ''}{inr(s.pnl ?? 0)} <span style={{ color: 'var(--nd-text-3)', fontWeight: 400 }}>({(s.pnlPct ?? 0).toFixed(2)}%)</span>
                </td>
                <td style={{ padding: '7px 10px', color: 'var(--nd-text-3)' }}>{s.trades ?? 0}</td>
                <td style={{ padding: '7px 10px', textAlign: 'right' }}>
                  <button onClick={() => stop(s.id)} disabled={busy === s.id}
                    style={{ padding: '3px 10px', borderRadius: 6, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: 'var(--nd-red)', cursor: 'pointer', fontSize: 11, fontWeight: 600 }}>
                    {busy === s.id ? '…' : 'Stop'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// ── Dashboard Page ────────────────────────────────────────────────────────────

const Dashboard: React.FC = () => {
  const [activeTab, setActiveTab]     = useState<TabId>('watchlist');
  const [accuracyStats, setAccuracyStats] = useState<any>(null);
  const [selectedCard, setSelectedCard] = useState<string | null>(null);
  const [showStartup, setShowStartup] = useState(false);

  useEffect(() => {
    apiService.getAccuracyStats().then(r => { if (r.data) setAccuracyStats(r.data); }).catch(() => {});

    // Show startup modal once per browser session, only if at least one service isn't up yet.
    if (sessionStorage.getItem('neuradex-startup-done')) return;
    Promise.allSettled([
      apiService.healthCheck(),
      apiService.getServicesHealth(),
    ]).then(results => {
      const backendOk = results[0].status === 'fulfilled';
      const svcsOk = results[1].status === 'fulfilled' &&
        ((results[1].value as any).data ?? []).every((s: any) => s.status === 'ok');
      if (!backendOk || !svcsOk) setShowStartup(true);
      else sessionStorage.setItem('neuradex-startup-done', '1');
    }).catch(() => setShowStartup(true));
  }, []);

  const STAT_CARDS = accuracyStats ? [
    { id: 'accuracy', label: 'Model Accuracy', value: `${(accuracyStats.accuracyRate * 100).toFixed(1)}%`, icon: 'psychology',    color: 'var(--nd-green)',  bg: 'var(--nd-green-50)' },
    { id: 'win',      label: 'Win Rate',       value: `${(accuracyStats.winRate * 100).toFixed(1)}%`,        icon: 'emoji_events', color: 'var(--nd-green)',  bg: 'var(--nd-green-50)' },
    { id: 'return',   label: 'Avg Return',     value: `${accuracyStats.averageReturn?.toFixed(2)}%`,          icon: 'trending_up',  color: 'var(--nd-blue)',   bg: '#e3f2fd'            },
    { id: 'sharpe',   label: 'Sharpe Ratio',   value: accuracyStats.sharpeRatio?.toFixed(2),                  icon: 'analytics',    color: 'var(--nd-purple)', bg: '#f5f3ff'            },
  ] : [];

  return (
    <div>
      {showStartup && (
        <SystemStartupModal onClose={() => {
          setShowStartup(false);
          sessionStorage.setItem('neuradex-startup-done', '1');
        }} />
      )}

      {/* Page heading */}
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">Market Overview</h1>
        <p className="nd-page-sub">Real-time NSE · BSE stock data with AI-generated predictions</p>
      </div>

      {/* Live performance + current market regime */}
      <PerformanceRegimeStrip />

      {/* Currently-running auto-trading sessions with open positions + P&L */}
      <LiveSessionsPanel />

      {/* Accuracy stat cards — click any to see the evidence */}
      {STAT_CARDS.length > 0 && (
        <div className="nd-grid-4" style={{ gap: 12, marginBottom: 20 }}>
          {STAT_CARDS.map(s => (
            <div key={s.label} className="nd-card" onClick={() => setSelectedCard(s.id)}
              style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px', cursor: 'pointer', transition: 'box-shadow 0.15s' }}
              onMouseEnter={e => (e.currentTarget.style.boxShadow = 'var(--nd-shadow-md)')}
              onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}>
              <span className="material-icons" title="See how this is calculated"
                style={{ position: 'absolute', top: 8, right: 8, fontSize: 16, color: 'var(--nd-text-3)' }}>info</span>
              <div className="nd-icon-chip" style={{ background: s.bg }}>
                <span className="material-icons" style={{ color: s.color }}>{s.icon}</span>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p className="nd-label">{s.label}</p>
                <p style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{s.value}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedCard && accuracyStats && (
        <MetricModal cardId={selectedCard} stats={accuracyStats} onClose={() => setSelectedCard(null)} />
      )}

      {/* Self-running autopilot + the system's learning curve */}
      <AutopilotBanner />
      <TradeGateCard />

      {/* Two-up: the system's learning (curve + pattern model) | AI scan accuracy */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: 20, marginBottom: 20, alignItems: 'start' }}>
        {/* Unified "system learning" card: the equity/win-rate curve on top, the
            dedicated pattern-recognition model below the divider. */}
        <div className="nd-card" style={{ padding: 0, position: 'relative', display: 'flex', flexDirection: 'column' }}>
          <LearningCurveCard embedded />
          <div style={{ height: 1, background: 'var(--nd-border)', margin: '12px 18px 0' }} />
          <PatternModelCard embedded />
        </div>
        <ScanAccuracyCard />
      </div>

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
                    AI
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Tab content */}
        <div style={{ padding: '16px 20px 20px' }}>
          {activeTab === 'watchlist' && <AiWatchlistTab />}
          {activeTab === 'directory' && <DirectoryTab />}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
