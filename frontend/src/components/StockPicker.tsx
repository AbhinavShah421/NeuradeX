import React, { useEffect, useRef, useState } from 'react';
import apiService from '../services/api';

interface StockItem { symbol: string; name: string; sector?: string; exchange?: string; }
interface Props {
  value: string;
  onChange: (symbol: string, name?: string) => void;
  placeholder?: string;
}

/** Searchable stock dropdown — searches the full NSE/BSE directory by symbol or name. */
const StockPicker: React.FC<Props> = ({ value, onChange, placeholder = 'Search symbol or company…' }) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<StockItem[]>([]);
  const [loading, setLoading] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const debRef = useRef<ReturnType<typeof setTimeout>>();

  // Close on outside click
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

  const pick = (s: StockItem) => {
    onChange(s.symbol, s.name);
    setQuery('');
    setOpen(false);
  };

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%' }}>
      <input
        className="nd-input"
        value={open ? query : value}
        placeholder={open ? placeholder : value || placeholder}
        onFocus={openAndLoad}
        onChange={e => { setQuery(e.target.value); setOpen(true); fetchList(e.target.value); }}
        style={{ width: '100%', boxSizing: 'border-box' }}
      />
      {open && (
        <div
          style={{
            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50, marginTop: 4,
            background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10,
            boxShadow: '0 12px 32px #00000040', maxHeight: 280, overflowY: 'auto',
          }}
        >
          {loading && <div style={{ padding: 12, fontSize: 12, color: 'var(--nd-text-3)' }}>Searching…</div>}
          {!loading && items.length === 0 && (
            <div style={{ padding: 12, fontSize: 12, color: 'var(--nd-text-3)' }}>No matches</div>
          )}
          {!loading && items.map(s => (
            <button
              key={`${s.symbol}-${s.exchange ?? ''}`}
              onClick={() => pick(s)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
                width: '100%', textAlign: 'left', padding: '9px 12px', background: 'none', border: 'none',
                borderBottom: '1px solid var(--nd-border)', cursor: 'pointer',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-bg)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'none')}
            >
              <span style={{ minWidth: 0 }}>
                <span style={{ fontWeight: 700, color: 'var(--nd-text-1)', fontSize: 13 }}>{s.symbol}</span>
                <span style={{ display: 'block', fontSize: 11, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {s.name}
                </span>
              </span>
              {s.exchange && <span style={{ fontSize: 10, color: 'var(--nd-text-3)', flexShrink: 0 }}>{s.exchange}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default StockPicker;
