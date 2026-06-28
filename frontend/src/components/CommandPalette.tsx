import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '../stores/appStore';

/**
 * Terminal-style command palette (Ctrl/Cmd+K) — quick navigation to any page,
 * any symbol's chart, or quick actions. Mounted once globally in Layout.
 */
interface Cmd {
  id: string;
  label: string;
  hint?: string;
  icon: string;
  run: () => void;
  keywords?: string;
}

const CommandPalette: React.FC = () => {
  const navigate = useNavigate();
  const { theme, setTheme } = useAppStore();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // ── Global hotkey ──────────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setOpen(o => !o);
      } else if (e.key === 'Escape') {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (open) { setQuery(''); setActive(0); setTimeout(() => inputRef.current?.focus(), 30); }
  }, [open]);

  const go = (path: string) => { navigate(path); setOpen(false); };

  const commands = useMemo<Cmd[]>(() => [
    { id: 'dash',   label: 'Dashboard',          icon: 'dashboard',        run: () => go('/'),                       keywords: 'home overview market' },
    { id: 'live',   label: 'AI Engine — Live Analysis', icon: 'psychology', run: () => go('/ai-engine'),             keywords: 'ensemble analyze' },
    { id: 'agents', label: 'AI Agents',          icon: 'smart_toy',        run: () => go('/ai-engine/agents'),       keywords: 'llm ollama' },
    { id: 'memory', label: 'Agents & Memory',    icon: 'memory',           run: () => go('/ai-engine/memory'),       keywords: 'pattern weights gbm dataset learning' },
    { id: 'bt',     label: 'Backtesting',        icon: 'history_edu',      run: () => go('/ai-engine/backtest'),     keywords: 'simulate historical' },
    { id: 'paper',  label: 'Paper Trading',      icon: 'receipt_long',     run: () => go('/ai-engine/paper-trading'),keywords: 'practice' },
    { id: 'live2',  label: 'Live Trading',       icon: 'bolt',             run: () => go('/ai-engine/live-trading'), keywords: 'real groww mis' },
    { id: 'orders', label: 'Orders',             icon: 'list_alt',         run: () => go('/orders'),                 keywords: 'trades executions' },
    { id: 'pf',     label: 'Portfolio',          icon: 'account_balance_wallet', run: () => go('/portfolio'),        keywords: 'holdings risk' },
    { id: 'pred',   label: 'Predictions',        icon: 'insights',         run: () => go('/predictions'),            keywords: 'forecast' },
    { id: 'funds',  label: 'Mutual Funds',       icon: 'savings',          run: () => go('/mutual-funds'),           keywords: 'mf sip' },
    { id: 'models', label: 'Model Registry',     icon: 'model_training',   run: () => go('/models'),                 keywords: 'mlflow' },
    { id: 'set',    label: 'Settings',           icon: 'settings',         run: () => go('/settings'),               keywords: 'config broker groww creds' },
    { id: 'theme',  label: `Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`, icon: theme === 'dark' ? 'light_mode' : 'dark_mode', run: () => { setTheme(theme === 'dark' ? 'light' : 'dark'); setOpen(false); }, keywords: 'dark light appearance' },
  ], [theme]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo<Cmd[]>(() => {
    const q = query.trim().toLowerCase();
    const list = !q
      ? commands
      : commands.filter(c => (c.label + ' ' + (c.keywords || '')).toLowerCase().includes(q));
    // If the query looks like a ticker, offer "Open <SYMBOL>".
    const sym = query.trim().toUpperCase();
    if (/^[A-Z][A-Z0-9&-]{1,14}$/.test(sym)) {
      list.unshift({ id: 'open-sym', label: `Open ${sym} chart`, hint: 'stock', icon: 'candlestick_chart', run: () => go(`/stocks/${sym}`) });
    }
    return list;
  }, [query, commands]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setActive(0); }, [query]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); filtered[active]?.run(); }
  };

  if (!open) return null;

  return (
    <div onClick={() => setOpen(false)} style={{
      position: 'fixed', inset: 0, zIndex: 20000, background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: '12vh',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: 'min(560px, 92vw)', background: 'var(--nd-surface)',
        border: '1px solid var(--nd-border)', borderRadius: 12,
        boxShadow: '0 20px 60px rgba(0,0,0,0.5)', overflow: 'hidden',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px', borderBottom: '1px solid var(--nd-border)' }}>
          <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-3)' }}>search</span>
          <input
            ref={inputRef} value={query} onChange={e => setQuery(e.target.value)} onKeyDown={onKeyDown}
            placeholder="Jump to a page, type a symbol, or run an action…"
            style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', color: 'var(--nd-text-1)', fontSize: 14 }}
          />
          <kbd style={{ fontSize: 10, color: 'var(--nd-text-3)', border: '1px solid var(--nd-border)', borderRadius: 5, padding: '2px 6px' }}>ESC</kbd>
        </div>
        <div style={{ maxHeight: '46vh', overflowY: 'auto', padding: 6 }}>
          {filtered.length === 0 && (
            <div style={{ padding: '18px 14px', color: 'var(--nd-text-3)', fontSize: 13 }}>No matches.</div>
          )}
          {filtered.map((c, i) => (
            <div key={c.id} onMouseEnter={() => setActive(i)} onClick={() => c.run()} style={{
              display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', borderRadius: 8, cursor: 'pointer',
              background: i === active ? 'var(--nd-green)' : 'transparent',
              color: i === active ? '#fff' : 'var(--nd-text-1)',
            }}>
              <span className="material-icons" style={{ fontSize: 17, color: i === active ? '#fff' : 'var(--nd-text-3)' }}>{c.icon}</span>
              <span style={{ flex: 1, fontSize: 13, fontWeight: 500 }}>{c.label}</span>
              {c.hint && <span style={{ fontSize: 10, color: i === active ? 'rgba(255,255,255,0.8)' : 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{c.hint}</span>}
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 14, padding: '8px 14px', borderTop: '1px solid var(--nd-border)', fontSize: 10.5, color: 'var(--nd-text-3)' }}>
          <span>↑↓ navigate</span><span>↵ open</span><span>Ctrl/⌘K toggle</span>
        </div>
      </div>
    </div>
  );
};

export default CommandPalette;
