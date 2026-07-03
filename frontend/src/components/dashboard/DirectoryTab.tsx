import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import apiService from '../../services/api';
import { inr } from '../../utils/format';

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

      {/* Pagination — up to 9 buttons (Prev + 7 page numbers + Next) in a rigid
          row with no wrap/scroll used to push ~30-40px past a 390px viewport,
          the exact width of the page's horizontal-scroll bug. flexWrap lets the
          button group drop to its own line; overflowX is a second safety net
          for the narrowest phones where even that line doesn't fit. */}
      {pages > 1 && (
        <div style={{ padding: '12px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4, flexWrap: 'wrap', gap: 8 }}>
          <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
            Page {page} of {pages} · {total} stocks
          </span>
          <div style={{ display: 'flex', gap: 4, overflowX: 'auto', scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch', maxWidth: '100%' }}>
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
              style={{ padding: '4px 12px', fontSize: 12, borderRadius: 6, cursor: page === 1 ? 'default' : 'pointer', border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: page === 1 ? 'var(--nd-text-3)' : 'var(--nd-text-1)', opacity: page === 1 ? 0.5 : 1, flexShrink: 0, whiteSpace: 'nowrap' }}>
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
                  style={{ width: 30, height: 28, fontSize: 12, borderRadius: 6, cursor: 'pointer', border: '1px solid var(--nd-border)', background: p === page ? 'var(--nd-accent)' : 'var(--nd-surface)', color: p === page ? '#fff' : 'var(--nd-text-2)', fontWeight: p === page ? 700 : 400, flexShrink: 0 }}>
                  {p}
                </button>
              );
            })}
            <button onClick={() => setPage(p => Math.min(pages, p + 1))} disabled={page === pages}
              style={{ padding: '4px 12px', fontSize: 12, borderRadius: 6, cursor: page === pages ? 'default' : 'pointer', border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: page === pages ? 'var(--nd-text-3)' : 'var(--nd-text-1)', opacity: page === pages ? 0.5 : 1, flexShrink: 0, whiteSpace: 'nowrap' }}>
              Next →
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default DirectoryTab;
