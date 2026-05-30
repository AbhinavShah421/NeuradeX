import React, { useState } from 'react';

function extractApiError(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const msg: string = (detail[0] as { msg?: string })?.msg || fallback;
    return msg.replace(/^Value error,\s*/i, '');
  }
  return fallback;
}
import NeuradeXLogo from '../components/NeuradeXLogo';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';
import { useAppStore } from '../stores/appStore';
import apiService from '../services/api';

const Login: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { setAuth, setProfile } = useAuthStore();
  const { theme, setTheme } = useAppStore();
  const isDark = theme === 'dark';

  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const from = (location.state as { from?: { pathname: string } })?.from?.pathname || '/';

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!identifier.trim() || !password) {
      setError('Email/phone and password are required');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const res = await apiService.login({ identifier: identifier.trim(), password });
      if (res.status === 'success' && res.data) {
        setAuth(res.data.token, res.data.broker, res.data.expires_at, res.data.user_id, res.data.email);
        apiService.getProfile().then(p => {
          if (p.status === 'success' && p.data) setProfile(p.data);
        }).catch(() => {});
        navigate(from, { replace: true });
      } else {
        setError(res.message || 'Login failed');
      }
    } catch (err: unknown) {
      setError(extractApiError(err, 'Invalid credentials — check your email/phone and password'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={isDark ? 'dark-mode' : ''} style={{ minHeight: '100vh', background: 'var(--nd-bg)', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <header style={{ borderBottom: '1px solid var(--nd-border)', background: 'var(--nd-bg)', padding: '0 32px', height: 58, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <NeuradeXLogo size={32} />
          <span style={{ fontWeight: 700, fontSize: 18, color: 'var(--nd-text-1)', letterSpacing: '-0.3px' }}>NeuradeX</span>
        </div>
        <button
          onClick={() => setTheme(isDark ? 'light' : 'dark')}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-2)', display: 'flex', alignItems: 'center' }}
        >
          <span className="material-icons" style={{ fontSize: 20 }}>{isDark ? 'light_mode' : 'dark_mode'}</span>
        </button>
      </header>

      <main style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '40px 16px' }}>
        <div style={{ width: '100%', maxWidth: 400 }}>

          {/* Title */}
          <div style={{ textAlign: 'center', marginBottom: 32 }}>
            <div style={{ margin: '0 auto 16px', width: 56, height: 56 }}>
              <NeuradeXLogo size={56} />
            </div>
            <h1 style={{ fontSize: 26, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 6 }}>Welcome back</h1>
            <p style={{ color: 'var(--nd-text-2)', fontSize: 14 }}>Sign in to your NeuradeX account</p>
          </div>

          {/* Form card */}
          <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 14, padding: '28px 24px' }}>
            <form onSubmit={handleLogin}>
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--nd-text-2)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Email or Phone
                </label>
                <input
                  className="nd-input"
                  type="text"
                  placeholder="you@example.com or +91 98765 43210"
                  value={identifier}
                  onChange={e => setIdentifier(e.target.value)}
                  autoComplete="username"
                  style={{ width: '100%' }}
                />
              </div>

              <div style={{ marginBottom: 20 }}>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--nd-text-2)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Password
                </label>
                <div style={{ position: 'relative' }}>
                  <input
                    className="nd-input"
                    type={showPassword ? 'text' : 'password'}
                    placeholder="Your password"
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    autoComplete="current-password"
                    style={{ width: '100%', paddingRight: 42 }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(s => !s)}
                    style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-2)', display: 'flex', alignItems: 'center' }}
                  >
                    <span className="material-icons" style={{ fontSize: 18 }}>{showPassword ? 'visibility_off' : 'visibility'}</span>
                  </button>
                </div>
              </div>

              {error && (
                <div style={{ background: 'var(--nd-red-50)', border: '1px solid #fca5a5', borderRadius: 8, padding: '10px 14px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-red)' }}>error_outline</span>
                  <span style={{ fontSize: 13, color: 'var(--nd-red)' }}>{error}</span>
                </div>
              )}

              <button
                type="submit"
                disabled={loading}
                style={{
                  width: '100%', height: 44, background: loading ? 'var(--nd-text-3)' : 'var(--nd-green)',
                  color: '#fff', border: 'none', borderRadius: 10, fontWeight: 600, fontSize: 15,
                  cursor: loading ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center',
                  justifyContent: 'center', gap: 8, transition: 'background 0.15s',
                }}
              >
                {loading ? (
                  <span style={{ width: 18, height: 18, border: '2px solid rgba(255,255,255,0.3)', borderTopColor: '#fff', borderRadius: '50%', display: 'inline-block', animation: 'nd-spin 0.7s linear infinite' }} />
                ) : (
                  <><span className="material-icons" style={{ fontSize: 18 }}>login</span>Sign In</>
                )}
              </button>
            </form>
          </div>

          {/* Sign up link */}
          <p style={{ textAlign: 'center', marginTop: 20, fontSize: 13, color: 'var(--nd-text-2)' }}>
            Don't have an account?{' '}
            <Link to="/signup" style={{ color: 'var(--nd-green)', fontWeight: 600, textDecoration: 'none' }}>
              Create one
            </Link>
          </p>

          <p style={{ textAlign: 'center', marginTop: 10, fontSize: 12, color: 'var(--nd-text-3)' }}>
            For educational purposes only · NeuradeX © 2024
          </p>
        </div>
      </main>
    </div>
  );
};

export default Login;
