import React, { useEffect, useRef, useState } from 'react';
import apiService from '../services/api';

interface StockItem { symbol: string; name: string; sector?: string; exchange?: string; }
interface Props {
  /** Currently-selected symbols (uppercase). */
  selected: string[];
  /** Called with the full next selection whenever a checkbox is toggled. */
  onChange: (symbols: string[]) => void;
  placeholder?: string;
}

/**
 * Searchable MULTI-select stock dropdown — every option carries a checkbox and
 * the menu stays open while you tick several symbols. (StockPicker is the
 * single-select variant used elsewhere; this one is for batch selection.)
 */
const MultiStockPicker: React.FC<Props> = ({ selected, onChange, placeholder = 'Search symbol or company…' }) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<StockItem[]>([]);
  const [loading, setLoading] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const debRef = useRef<ReturnType<typeof setTimeout>>();

  const sel = new Set(selected.map(s => s.toUpperCase()));

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const fetchList = (q: string) => {
    clearTimeout(debRef.current);
    debRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await apiService.getDirectoryList({ q, limit: 40 });
        setItems((r?.data ?? []) as StockItem[]);
      } catch {
        setItems([]);
      } finally {
        setLoading(false);
      }
    }, 220);
  };

  const openAndLoad = () => {
    setOpen(true);
    if (items.length === 0) fetchList(query);
  };

  const toggle = (sym: string) => {
    const u = sym.toUpperCase();
    if (sel.has(u)) onChange(selected.filter(s => s.toUpperCase() !== u));
    else onChange([...selected, u]);
  };

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%' }}>
      <input
        className="nd-input"
        value={query}
        placeholder={selected.length ? `${selected.length} selected — search to add more…` : placeholder}
        onFocus={openAndLoad}
        onChange={e => { setQuery(e.target.value); setOpen(true); fetchList(e.target.value); }}
        style={{ width: '100%', boxSizing: 'border-box' }}
      />
      {open && (
        <div
          style={{
            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50, marginTop: 4,
            background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10,
            boxShadow: '0 12px 32px #00000040', maxHeight: 320, overflowY: 'auto',
          }}
        >
          {loading && <div style={{ padding: 12, fontSize: 12, color: 'var(--nd-text-3)' }}>Searching…</div>}
          {!loading && items.length === 0 && (
            <div style={{ padding: 12, fontSize: 12, color: 'var(--nd-text-3)' }}>No matches</div>
          )}
          {!loading && items.map(s => {
            const checked = sel.has(s.symbol.toUpperCase());
            return (
              <button
                key={`${s.symbol}-${s.exchange ?? ''}`}
                onClick={() => toggle(s.symbol)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  width: '100%', textAlign: 'left', padding: '9px 12px',
                  background: checked ? 'var(--nd-bg)' : 'none', border: 'none',
                  borderBottom: '1px solid var(--nd-border)', cursor: 'pointer',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-bg)')}
                onMouseLeave={e => (e.currentTarget.style.background = checked ? 'var(--nd-bg)' : 'none')}
              >
                {/* checkbox */}
                <span style={{
                  width: 18, height: 18, flexShrink: 0, borderRadius: 5,
                  border: `1.5px solid ${checked ? 'var(--nd-green)' : 'var(--nd-border)'}`,
                  background: checked ? 'var(--nd-green)' : 'transparent',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  {checked && <span className="material-icons" style={{ fontSize: 14, color: '#fff' }}>check</span>}
                </span>
                <span style={{ minWidth: 0, flex: 1 }}>
                  <span style={{ fontWeight: 700, color: 'var(--nd-text-1)', fontSize: 13 }}>{s.symbol}</span>
                  <span style={{ display: 'block', fontSize: 11, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {s.name}
                  </span>
                </span>
                {s.exchange && <span style={{ fontSize: 10, color: 'var(--nd-text-3)', flexShrink: 0 }}>{s.exchange}</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default MultiStockPicker;
