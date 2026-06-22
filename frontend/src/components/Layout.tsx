import React, { useEffect, useRef, useState } from 'react';
import NeuradeXLogo from './NeuradeXLogo';
import GrowwStatusBadge from './GrowwStatusBadge';
import FloatingSystemStatus from './FloatingSystemStatus';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAppStore } from '../stores/appStore';
import { useAuthStore } from '../stores/authStore';
import { useScanStore } from '../stores/scanStore';
import socketService from '../services/socket';

interface LayoutProps {
  children: React.ReactNode;
}

const INDICES = [
  { name: 'NIFTY',      value: '23,643', change: -46.10,  pct: '-0.19' },
  { name: 'SENSEX',     value: '75,237', change: -160.73, pct: '-0.21' },
  { name: 'BANKNIFTY',  value: '53,710', change: -418.60, pct: '-0.77' },
  { name: 'MIDCPNIFTY', value: '14,168', change: -96.65,  pct: '-0.68' },
  { name: 'FINNIFTY',   value: '25,343', change: +18.40,  pct: '+0.07' },
  { name: 'INDIAVIX',   value: '13.82',  change: -0.35,   pct: '-2.47' },
];

// Main nav items (left of AI Engine)
const NAV_LEFT = [
  { to: '/',             label: 'Dashboard' },
  { to: '/predictions',  label: 'Predictions' },
  { to: '/portfolio',    label: 'Portfolio' },
  { to: '/mutual-funds', label: 'Funds' },
];

// AI Engine sub-menu items
const AI_ENGINE_ITEMS = [
  { to: '/ai-engine',               label: 'Live Analysis',  icon: 'psychology' },
  { to: '/ai-engine/agents',        label: 'AI Agents',      icon: 'smart_toy' },
  { to: '/ai-engine/models-control', label: 'AI Models',      icon: 'model_training' },
  { to: '/ai-engine/backtest',      label: 'Backtesting',    icon: 'history_edu' },
  { to: '/ai-engine/paper-trading', label: 'Paper Trading',  icon: 'receipt_long' },
  { to: '/ai-engine/memory',        label: 'Pattern Memory', icon: 'memory' },
];

// Main nav items (right of AI Engine)
const NAV_RIGHT = [
  { to: '/models', label: 'Models' },
  { to: '/orders', label: 'Orders' },
];

const BROKER_COLORS: Record<string, string> = {
  groww:    '#00b386',
  zerodha:  '#387ed1',
  angelone: '#e74c3c',
  upstox:   '#7c3aed',
};

const Layout: React.FC<LayoutProps> = ({ children }) => {
  const { theme, setTheme } = useAppStore();
  const { broker, profile, clearAuth } = useAuthStore();
  const [, setIsConnected] = useState(false);
  const [userDropdownOpen, setUserDropdownOpen] = useState(false);
  const [aiDropdownOpen, setAiDropdownOpen]     = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen]     = useState(false);
  const userDropdownRef = useRef<HTMLDivElement>(null);
  const aiDropdownRef   = useRef<HTMLDivElement>(null);
  const location  = useLocation();
  const navigate  = useNavigate();
  const isDark = theme === 'dark';

  // Keep the theme on <html> so the persisted dark mode covers the whole page
  // (and matches the pre-render script in index.html that prevents a flash).
  useEffect(() => {
    document.documentElement.classList.toggle('dark-mode', isDark);
  }, [isDark]);

  // Centralized AI-scan status poll — runs app-wide so every page shares the same
  // scanning/rescan state (faster cadence while a sweep is live).
  const scanning = useScanStore(s => s.scanning);
  const fetchScanStatus = useScanStore(s => s.fetchStatus);
  useEffect(() => {
    fetchScanStatus();
    const t = setInterval(fetchScanStatus, scanning ? 5000 : 15000);
    return () => clearInterval(t);
  }, [scanning, fetchScanStatus]);

  useEffect(() => {
    const init = async () => {
      try { await socketService.connect(); setIsConnected(true); }
      catch { console.error('WebSocket connection failed'); }
    };
    init();
    return () => { socketService.disconnect(); };
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (userDropdownRef.current && !userDropdownRef.current.contains(e.target as Node)) setUserDropdownOpen(false);
      if (aiDropdownRef.current  && !aiDropdownRef.current.contains(e.target as Node))   setAiDropdownOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Close dropdowns and mobile menu on route change
  useEffect(() => { setAiDropdownOpen(false); setMobileMenuOpen(false); }, [location.pathname]);

  const isActive = (to: string, exact = false) =>
    exact ? location.pathname === to : (to === '/' ? location.pathname === '/' : location.pathname.startsWith(to));

  const isAiEngineActive = location.pathname.startsWith('/ai-engine');

  const handleLogout = () => {
    clearAuth();
    navigate('/login', { replace: true });
  };

  const brokerLabel = broker ? broker.charAt(0).toUpperCase() + broker.slice(1) : '';
  const brokerColor = broker ? BROKER_COLORS[broker] ?? '#767676' : '#767676';

  return (
    <div className={isDark ? 'dark-mode' : ''} style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--nd-bg)', color: 'var(--nd-text-1)' }}>
      <header className="nd-header">
        <div className="nd-header-inner">
          <Link to="/" className="nd-logo">
            <NeuradeXLogo size={32} />
            <span className="nd-logo-text">NeuradeX</span>
          </Link>

          <nav className="nd-nav">
            {/* Left links */}
            {NAV_LEFT.map(n => (
              <Link key={n.to} to={n.to} className={`nd-nav-link${isActive(n.to) ? ' active' : ''}`}>
                {n.label}
              </Link>
            ))}

            {/* AI Engine dropdown */}
            <div ref={aiDropdownRef} style={{ position: 'relative' }}>
              <button
                onClick={() => setAiDropdownOpen(o => !o)}
                className={`nd-nav-link${isAiEngineActive ? ' active' : ''}`}
                style={{
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: 0,
                  font: 'inherit',
                  color: 'inherit',
                }}
              >
                AI Engine
                <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)', transition: 'transform 0.15s', transform: aiDropdownOpen ? 'rotate(180deg)' : 'none' }}>
                  expand_more
                </span>
              </button>

              {aiDropdownOpen && (
                <div style={{
                  position: 'absolute',
                  top: 'calc(100% + 10px)',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  width: 220,
                  background: 'var(--nd-bg)',
                  border: '1px solid var(--nd-border)',
                  borderRadius: 12,
                  boxShadow: 'var(--nd-shadow-md)',
                  zIndex: 300,
                  overflow: 'hidden',
                  padding: 6,
                }}>
                  <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 1, padding: '4px 10px 6px' }}>
                    AI Engine
                  </div>
                  {AI_ENGINE_ITEMS.map(item => (
                    <Link
                      key={item.to}
                      to={item.to}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        padding: '9px 12px',
                        borderRadius: 8,
                        textDecoration: 'none',
                        fontSize: 13,
                        fontWeight: 500,
                        transition: 'background 0.12s',
                        background: isActive(item.to, item.to === '/ai-engine') ? 'var(--nd-surface)' : 'transparent',
                        color: isActive(item.to, item.to === '/ai-engine') ? 'var(--nd-accent)' : 'var(--nd-text-1)',
                        borderLeft: isActive(item.to, item.to === '/ai-engine') ? '2px solid var(--nd-accent)' : '2px solid transparent',
                      }}
                      onMouseEnter={e => { if (!isActive(item.to, item.to === '/ai-engine')) (e.currentTarget as HTMLElement).style.background = 'var(--nd-surface)'; }}
                      onMouseLeave={e => { if (!isActive(item.to, item.to === '/ai-engine')) (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
                    >
                      <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>{item.icon}</span>
                      {item.label}
                    </Link>
                  ))}
                </div>
              )}
            </div>

            {/* Right links */}
            {NAV_RIGHT.map(n => (
              <Link key={n.to} to={n.to} className={`nd-nav-link${isActive(n.to) ? ' active' : ''}`}>
                {n.label}
              </Link>
            ))}

            {/* Docs — always opens in a new tab */}
            <a href="/neuradex/dev/docs/" target="_blank" rel="noopener noreferrer"
               className="nd-nav-link" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              Docs
              <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)' }}>open_in_new</span>
            </a>
          </nav>

          <div className="nd-header-right">
            {/* Groww API token status — hidden on mobile (lives in hamburger menu) */}
            <span className="nd-hide-mobile"><GrowwStatusBadge /></span>

            {/* Theme toggle — hidden on mobile (lives in hamburger menu) */}
            <button className="nd-theme-btn nd-hide-mobile" onClick={() => setTheme(isDark ? 'light' : 'dark')}>
              <span className="material-icons">{isDark ? 'light_mode' : 'dark_mode'}</span>
            </button>

            {/* Hamburger — mobile only */}
            <button className="nd-hamburger" onClick={() => setMobileMenuOpen(o => !o)} aria-label="Menu">
              <span className="material-icons">{mobileMenuOpen ? 'close' : 'menu'}</span>
            </button>

            {/* User avatar + dropdown — hidden on mobile (lives in hamburger menu) */}
            {broker && (
              <div ref={userDropdownRef} className="nd-hide-mobile" style={{ position: 'relative' }}>
                <button
                  onClick={() => setUserDropdownOpen(o => !o)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 24, padding: '4px 12px 4px 4px', cursor: 'pointer', transition: 'box-shadow 0.15s' }}
                  onMouseEnter={e => (e.currentTarget.style.boxShadow = 'var(--nd-shadow-md)')}
                  onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}
                >
                  <div style={{ width: 28, height: 28, borderRadius: '50%', background: brokerColor, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 12, color: '#fff', flexShrink: 0 }}>
                    {profile?.initials || brokerLabel.charAt(0)}
                  </div>
                  <div style={{ textAlign: 'left', lineHeight: 1.2 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--nd-text-1)', whiteSpace: 'nowrap', maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {profile?.name || brokerLabel}
                    </div>
                    {profile?.accountId && (
                      <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{profile.accountId}</div>
                    )}
                  </div>
                  <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)', marginLeft: 2 }}>
                    {userDropdownOpen ? 'expand_less' : 'expand_more'}
                  </span>
                </button>

                {userDropdownOpen && (
                  <div style={{ position: 'absolute', top: 'calc(100% + 8px)', right: 0, width: 240, background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 12, boxShadow: 'var(--nd-shadow-md)', zIndex: 200, overflow: 'hidden' }}>
                    <div style={{ padding: '16px', borderBottom: '1px solid var(--nd-border)', display: 'flex', gap: 12, alignItems: 'center' }}>
                      <div style={{ width: 40, height: 40, borderRadius: '50%', background: brokerColor, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 16, color: '#fff', flexShrink: 0 }}>
                        {profile?.initials || brokerLabel.charAt(0)}
                      </div>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--nd-text-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {profile?.name || 'Groww User'}
                        </div>
                        {profile?.email && (
                          <div style={{ fontSize: 11, color: 'var(--nd-text-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{profile.email}</div>
                        )}
                        {profile?.accountId && (
                          <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 2 }}>ID: {profile.accountId}</div>
                        )}
                      </div>
                    </div>
                    <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ width: 24, height: 24, borderRadius: '50%', background: brokerColor, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800, fontSize: 11, color: '#fff' }}>
                        {brokerLabel.charAt(0)}
                      </div>
                      <div>
                        <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--nd-text-1)' }}>{brokerLabel}</div>
                        <div style={{ fontSize: 10, color: 'var(--nd-green)' }}>● Connected</div>
                      </div>
                    </div>
                    <div style={{ padding: '6px' }}>
                      <Link
                        to="/settings"
                        onClick={() => setUserDropdownOpen(false)}
                        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', borderRadius: 8, textDecoration: 'none', color: 'var(--nd-text-1)', fontSize: 13, fontWeight: 500, transition: 'background 0.12s' }}
                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-bg)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                      >
                        <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>settings</span>
                        Settings
                      </Link>
                      <button
                        onClick={handleLogout}
                        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', borderRadius: 8, border: 'none', background: 'none', cursor: 'pointer', color: 'var(--nd-red)', fontSize: 13, fontWeight: 500, transition: 'background 0.12s' }}
                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-red-50)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                      >
                        <span className="material-icons" style={{ fontSize: 16 }}>logout</span>
                        Disconnect Broker
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* ── Mobile full-screen menu ── */}
        {mobileMenuOpen && (
          <div className="nd-mobile-menu">
            <div className="nd-mobile-menu-head">
              <Link to="/" className="nd-logo" onClick={() => setMobileMenuOpen(false)}>
                <NeuradeXLogo size={28} />
                <span style={{ fontSize: 17, fontWeight: 700 }}>NeuradeX</span>
              </Link>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto' }}>
                {/* Groww badge */}
                <GrowwStatusBadge />
                {/* Theme toggle */}
                <button className="nd-theme-btn" onClick={() => setTheme(isDark ? 'light' : 'dark')}>
                  <span className="material-icons">{isDark ? 'light_mode' : 'dark_mode'}</span>
                </button>
                {/* Close */}
                <button className="nd-theme-btn" onClick={() => setMobileMenuOpen(false)}>
                  <span className="material-icons">close</span>
                </button>
              </div>
            </div>
            <div className="nd-mobile-menu-body">
              {NAV_LEFT.map(n => (
                <Link key={n.to} to={n.to} className={`nd-mobile-nav-link${isActive(n.to) ? ' active' : ''}`}>
                  <span className="material-icons" style={{ fontSize: 20, color: 'var(--nd-text-3)' }}>
                    {n.to === '/' ? 'dashboard' : n.to === '/predictions' ? 'psychology' : 'account_balance_wallet'}
                  </span>
                  {n.label}
                </Link>
              ))}
              <div className="nd-mobile-nav-section">AI Engine</div>
              {AI_ENGINE_ITEMS.map(item => (
                <Link key={item.to} to={item.to} className={`nd-mobile-nav-link${isActive(item.to, item.to === '/ai-engine') ? ' active' : ''}`}
                  style={{ paddingLeft: 24 }}>
                  <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-3)' }}>{item.icon}</span>
                  {item.label}
                </Link>
              ))}
              {NAV_RIGHT.map(n => (
                <Link key={n.to} to={n.to} className={`nd-mobile-nav-link${isActive(n.to) ? ' active' : ''}`}>
                  <span className="material-icons" style={{ fontSize: 20, color: 'var(--nd-text-3)' }}>
                    {n.to === '/models' ? 'model_training' : 'receipt_long'}
                  </span>
                  {n.label}
                </Link>
              ))}
              {/* Docs — always opens in a new tab */}
              <a href="/neuradex/dev/docs/" target="_blank" rel="noopener noreferrer"
                 className="nd-mobile-nav-link" onClick={() => setMobileMenuOpen(false)}>
                <span className="material-icons" style={{ fontSize: 20, color: 'var(--nd-text-3)' }}>menu_book</span>
                Docs
                <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)', marginLeft: 'auto' }}>open_in_new</span>
              </a>
            </div>
            {broker && (
              <div className="nd-mobile-menu-footer">
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                  <div style={{ width: 36, height: 36, borderRadius: '50%', background: brokerColor, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 14, color: '#fff' }}>
                    {profile?.initials || brokerLabel.charAt(0)}
                  </div>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{profile?.name || brokerLabel}</div>
                    <div style={{ fontSize: 11, color: 'var(--nd-green)' }}>● Connected</div>
                  </div>
                </div>
                <Link to="/settings" onClick={() => setMobileMenuOpen(false)} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 10, textDecoration: 'none', background: 'var(--nd-surface)', color: 'var(--nd-text-1)', fontSize: 14, fontWeight: 500, marginBottom: 8, boxSizing: 'border-box' }}>
                  <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-3)' }}>settings</span>
                  Settings
                </Link>
                <button onClick={handleLogout} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 10, border: 'none', background: 'var(--nd-red-50)', cursor: 'pointer', color: 'var(--nd-red)', fontSize: 14, fontWeight: 500 }}>
                  <span className="material-icons" style={{ fontSize: 18 }}>logout</span>
                  Disconnect Broker
                </button>
              </div>
            )}
          </div>
        )}

        <div className="nd-ticker">
          <div className="nd-ticker-inner">
            {INDICES.map(idx => (
              <div key={idx.name} className="nd-ticker-item">
                <span className="nd-ticker-name">{idx.name}</span>
                <span className="nd-ticker-val">{idx.value}</span>
                <span className={idx.change >= 0 ? 'nd-ticker-up' : 'nd-ticker-dn'}>
                  {idx.change >= 0 ? '+' : ''}{idx.change.toFixed(2)} ({idx.pct}%)
                </span>
              </div>
            ))}
          </div>
        </div>
      </header>

      <main className="nd-main">{children}</main>

      <FloatingSystemStatus />

      <footer className="nd-footer">
        NeuradeX © 2024 · AI-Powered Market Intelligence · For educational purposes only
      </footer>
    </div>
  );
};

export default Layout;
