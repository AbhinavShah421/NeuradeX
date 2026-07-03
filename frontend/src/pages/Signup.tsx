import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';
import { useAppStore } from '../stores/appStore';
import { BrokerInfo, BrokerType } from '../types';
import apiService from '../services/api';
import NeuradeXLogo from '../components/NeuradeXLogo';

function extractApiError(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const msg: string = (detail[0] as { msg?: string })?.msg || fallback;
    return msg.replace(/^Value error,\s*/i, '');
  }
  return fallback;
}

const BROKERS: BrokerInfo[] = [
  { id: 'groww',    name: 'Groww',     logo: 'G', color: '#00b386', available: true,  tagline: 'NSE · BSE · F&O' },
  { id: 'zerodha',  name: 'Zerodha',   logo: 'Z', color: '#387ed1', available: false, tagline: 'Coming Soon' },
  { id: 'angelone', name: 'Angel One', logo: 'A', color: '#e74c3c', available: false, tagline: 'Coming Soon' },
  { id: 'upstox',   name: 'Upstox',    logo: 'U', color: '#7c3aed', available: false, tagline: 'Coming Soon' },
];

type Step = 1 | 2 | 3;

const STEP_LABELS = ['Personal Info', 'Verify OTP', 'Connect Broker'];

const Signup: React.FC = () => {
  const navigate = useNavigate();
  const { setAuth, setProfile } = useAuthStore();
  const { theme, setTheme } = useAppStore();
  const isDark = theme === 'dark';

  const [step, setStep] = useState<Step>(1);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // Step 1 fields
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  // Step 2
  const [otp, setOtp] = useState('');
  const [resendCooldown, setResendCooldown] = useState(0);

  // Step 3
  const [selectedBroker, setSelectedBroker] = useState<BrokerType>('groww');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);

  const startResendCooldown = () => {
    setResendCooldown(60);
    const interval = setInterval(() => {
      setResendCooldown(prev => {
        if (prev <= 1) { clearInterval(interval); return 0; }
        return prev - 1;
      });
    }, 1000);
  };

  // ── Step 1 submit ──────────────────────────────────────────────────────────
  const handleSendOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!firstName.trim() || !lastName.trim()) { setError('First and last name are required'); return; }
    if (!email.trim()) { setError('Email is required'); return; }
    if (!phone.trim()) { setError('Phone number is required'); return; }
    if (password.length < 8) { setError('Password must be at least 8 characters'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match'); return; }

    setLoading(true);
    try {
      const res = await apiService.signupSendOtp({
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        email: email.trim(),
        phone: phone.trim(),
        password,
        confirm_password: confirmPassword,
      });
      if (res.status === 'success') {
        setStep(2);
        startResendCooldown();
      } else {
        setError(res.message || 'Failed to send OTP');
      }
    } catch (err: unknown) {
      setError(extractApiError(err, 'Failed to send verification code'));
    } finally {
      setLoading(false);
    }
  };

  // ── Step 2 submit ──────────────────────────────────────────────────────────
  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (otp.trim().length !== 6) { setError('Enter the 6-digit code'); return; }

    setLoading(true);
    try {
      const res = await apiService.signupVerifyOtp({ email, otp: otp.trim() });
      if (res.status === 'success') {
        setStep(3);
      } else {
        setError(res.message || 'Invalid code');
      }
    } catch (err: unknown) {
      setError(extractApiError(err, 'Verification failed'));
    } finally {
      setLoading(false);
    }
  };

  const handleResendOtp = async () => {
    if (resendCooldown > 0) return;
    setError('');
    setLoading(true);
    try {
      await apiService.signupSendOtp({
        first_name: firstName,
        last_name: lastName,
        email,
        phone,
        password,
        confirm_password: confirmPassword,
      });
      startResendCooldown();
    } catch {
      setError('Failed to resend code');
    } finally {
      setLoading(false);
    }
  };

  // ── Step 3 submit ──────────────────────────────────────────────────────────
  const handleComplete = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!apiKey.trim() || !apiSecret.trim()) { setError('API key and secret are required'); return; }

    setLoading(true);
    try {
      const res = await apiService.signupComplete({
        email,
        broker: selectedBroker,
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
      });
      if (res.status === 'success' && res.data) {
        setAuth(res.data.token, res.data.broker, res.data.expires_at, res.data.user_id, res.data.email);
        apiService.getProfile().then(p => {
          if (p.status === 'success' && p.data) setProfile(p.data);
        }).catch(() => {});
        navigate('/', { replace: true });
      } else {
        setError(res.message || 'Signup failed');
      }
    } catch (err: unknown) {
      setError(extractApiError(err, 'Failed to complete signup'));
    } finally {
      setLoading(false);
    }
  };

  const broker = BROKERS.find(b => b.id === selectedBroker)!;

  // ── Shared styles ──────────────────────────────────────────────────────────
  const inputStyle: React.CSSProperties = {
    // 16px, not 14px: iOS Safari auto-zooms the page on focusing any input under 16px.
    width: '100%', height: 42, padding: '0 12px', border: '1px solid var(--nd-border)',
    borderRadius: 8, background: 'var(--nd-bg)', color: 'var(--nd-text-1)', fontSize: 16,
    outline: 'none', boxSizing: 'border-box',
  };
  const labelStyle: React.CSSProperties = {
    display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--nd-text-2)',
    marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px',
  };

  return (
    <div className={isDark ? 'dark-mode' : ''} style={{ minHeight: '100vh', background: 'var(--nd-bg)', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <header style={{ borderBottom: '1px solid var(--nd-border)', background: 'var(--nd-bg)', padding: '0 32px', height: 58, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <NeuradeXLogo size={32} />
          <span style={{ fontWeight: 700, fontSize: 18, color: 'var(--nd-text-1)', letterSpacing: '-0.3px' }}>NeuradeX</span>
        </div>
        <button onClick={() => setTheme(isDark ? 'light' : 'dark')} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-2)', display: 'flex', alignItems: 'center' }}>
          <span className="material-icons" style={{ fontSize: 20 }}>{isDark ? 'light_mode' : 'dark_mode'}</span>
        </button>
      </header>

      <main style={{ flex: 1, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '40px 16px' }}>
        <div style={{ width: '100%', maxWidth: 480 }}>

          {/* Title */}
          <div style={{ textAlign: 'center', marginBottom: 28 }}>
            <h1 style={{ fontSize: 26, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 6 }}>Create Your Account</h1>
            <p style={{ color: 'var(--nd-text-2)', fontSize: 14 }}>AI-powered market intelligence — free to get started</p>
          </div>

          {/* Step indicator */}
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 28, gap: 0 }}>
            {STEP_LABELS.map((label, i) => {
              const n = (i + 1) as Step;
              const done = step > n;
              const active = step === n;
              return (
                <React.Fragment key={n}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1 }}>
                    <div style={{
                      width: 32, height: 32, borderRadius: '50%',
                      background: done ? 'var(--nd-green)' : active ? 'var(--nd-green)' : 'var(--nd-border)',
                      color: done || active ? '#fff' : 'var(--nd-text-3)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontWeight: 700, fontSize: 13, marginBottom: 4,
                    }}>
                      {done ? <span className="material-icons" style={{ fontSize: 16 }}>check</span> : n}
                    </div>
                    <span style={{ fontSize: 11, color: active ? 'var(--nd-green)' : 'var(--nd-text-3)', fontWeight: active ? 600 : 400 }}>{label}</span>
                  </div>
                  {i < STEP_LABELS.length - 1 && (
                    <div style={{ flex: 2, height: 2, background: step > n ? 'var(--nd-green)' : 'var(--nd-border)', marginBottom: 20 }} />
                  )}
                </React.Fragment>
              );
            })}
          </div>

          {/* ── Step 1: Personal Info ── */}
          {step === 1 && (
            <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 14, padding: '28px 24px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 22 }}>
                <span className="material-icons" style={{ fontSize: 22, color: 'var(--nd-green)' }}>person</span>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--nd-text-1)' }}>Personal Information</div>
                  <div style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>We'll send a verification code to your email and phone</div>
                </div>
              </div>

              <form onSubmit={handleSendOtp}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 14 }}>
                  <div>
                    <label style={labelStyle}>First Name</label>
                    <input style={inputStyle} placeholder="John" value={firstName} onChange={e => setFirstName(e.target.value)} autoComplete="given-name" />
                  </div>
                  <div>
                    <label style={labelStyle}>Last Name</label>
                    <input style={inputStyle} placeholder="Doe" value={lastName} onChange={e => setLastName(e.target.value)} autoComplete="family-name" />
                  </div>
                </div>

                <div style={{ marginBottom: 14 }}>
                  <label style={labelStyle}>Email Address</label>
                  <input style={inputStyle} type="email" placeholder="you@example.com" value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" />
                </div>

                <div style={{ marginBottom: 14 }}>
                  <label style={labelStyle}>Phone Number</label>
                  <input style={inputStyle} type="tel" placeholder="+91 98765 43210" value={phone} onChange={e => setPhone(e.target.value)} autoComplete="tel" name="phone" />
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
                  <div>
                    <label style={labelStyle}>Password</label>
                    <div style={{ position: 'relative' }}>
                      <input style={{ ...inputStyle, paddingRight: 36 }} type={showPassword ? 'text' : 'password'} placeholder="Min 8 characters" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" />
                      <button type="button" onClick={() => setShowPassword(s => !s)} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-2)' }}>
                        <span className="material-icons" style={{ fontSize: 16 }}>{showPassword ? 'visibility_off' : 'visibility'}</span>
                      </button>
                    </div>
                  </div>
                  <div>
                    <label style={labelStyle}>Confirm Password</label>
                    <div style={{ position: 'relative' }}>
                      <input style={{ ...inputStyle, paddingRight: 36, borderColor: confirmPassword && confirmPassword !== password ? 'var(--nd-red)' : undefined }} type={showConfirm ? 'text' : 'password'} placeholder="Repeat password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} autoComplete="new-password" />
                      <button type="button" onClick={() => setShowConfirm(s => !s)} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-2)' }}>
                        <span className="material-icons" style={{ fontSize: 16 }}>{showConfirm ? 'visibility_off' : 'visibility'}</span>
                      </button>
                    </div>
                  </div>
                </div>

                {error && <ErrorBox message={error} />}

                <button type="submit" disabled={loading} style={submitBtnStyle(loading, '#00b386')}>
                  {loading ? <Spinner /> : <><span className="material-icons" style={{ fontSize: 18 }}>send</span>Send Verification Code</>}
                </button>
              </form>
            </div>
          )}

          {/* ── Step 2: OTP Verification ── */}
          {step === 2 && (
            <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 14, padding: '28px 24px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 22 }}>
                <span className="material-icons" style={{ fontSize: 22, color: 'var(--nd-green)' }}>verified</span>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--nd-text-1)' }}>Verify Your Identity</div>
                  <div style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>Code sent to {email} and your WhatsApp</div>
                </div>
              </div>

              <form onSubmit={handleVerifyOtp}>
                <div style={{ marginBottom: 20 }}>
                  <label style={labelStyle}>6-Digit Verification Code</label>
                  <input
                    style={{ ...inputStyle, fontSize: 24, fontWeight: 700, letterSpacing: 8, textAlign: 'center' }}
                    type="text"
                    inputMode="numeric"
                    maxLength={6}
                    placeholder="------"
                    value={otp}
                    onChange={e => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    autoFocus
                  />
                </div>

                {error && <ErrorBox message={error} />}

                <button type="submit" disabled={loading} style={submitBtnStyle(loading, '#00b386')}>
                  {loading ? <Spinner /> : <><span className="material-icons" style={{ fontSize: 18 }}>check_circle</span>Verify Code</>}
                </button>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 14 }}>
                  <button type="button" onClick={() => { setStep(1); setOtp(''); setError(''); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-2)', fontSize: 13 }}>
                    ← Back
                  </button>
                  <button type="button" onClick={handleResendOtp} disabled={resendCooldown > 0 || loading} style={{ background: 'none', border: 'none', cursor: resendCooldown > 0 ? 'default' : 'pointer', color: resendCooldown > 0 ? 'var(--nd-text-3)' : 'var(--nd-green)', fontSize: 13, fontWeight: 500 }}>
                    {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Resend Code'}
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* ── Step 3: Broker Connection ── */}
          {step === 3 && (
            <div>
              {/* Broker grid */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
                {BROKERS.map(b => (
                  <button
                    key={b.id}
                    onClick={() => b.available && setSelectedBroker(b.id)}
                    disabled={!b.available}
                    style={{
                      background: 'var(--nd-bg)', border: `2px solid ${selectedBroker === b.id && b.available ? b.color : 'var(--nd-border)'}`,
                      borderRadius: 12, padding: '14px 12px', cursor: b.available ? 'pointer' : 'not-allowed',
                      opacity: b.available ? 1 : 0.45, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8,
                      transition: 'border-color 0.15s', boxShadow: selectedBroker === b.id && b.available ? `0 0 0 3px ${b.color}22` : 'none',
                      position: 'relative',
                    }}
                  >
                    <div style={{ width: 40, height: 40, borderRadius: '50%', background: b.color, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800, fontSize: 18, color: '#fff' }}>{b.logo}</div>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--nd-text-1)' }}>{b.name}</div>
                      <div style={{ fontSize: 11, color: b.available ? 'var(--nd-text-2)' : 'var(--nd-text-3)', marginTop: 2 }}>{b.tagline}</div>
                    </div>
                    {selectedBroker === b.id && b.available && <span className="material-icons" style={{ position: 'absolute', top: 8, right: 8, fontSize: 16, color: b.color }}>check_circle</span>}
                    {!b.available && <span style={{ position: 'absolute', top: 8, right: 8, fontSize: 10, background: 'var(--nd-surface)', color: 'var(--nd-text-3)', borderRadius: 4, padding: '2px 5px', border: '1px solid var(--nd-border)' }}>SOON</span>}
                  </button>
                ))}
              </div>

              <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 14, padding: '28px 24px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 22 }}>
                  <div style={{ width: 30, height: 30, borderRadius: 8, background: broker.color, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800, fontSize: 15, color: '#fff' }}>{broker.logo}</div>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--nd-text-1)' }}>Connect {broker.name}</div>
                    <div style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>Enter your API credentials</div>
                  </div>
                </div>

                <form onSubmit={handleComplete}>
                  <div style={{ marginBottom: 14 }}>
                    <label style={labelStyle}>API Key</label>
                    <input style={inputStyle} type="text" placeholder="Paste your Groww API key" value={apiKey} onChange={e => setApiKey(e.target.value)} autoComplete="off" />
                  </div>

                  <div style={{ marginBottom: 20 }}>
                    <label style={labelStyle}>API Secret</label>
                    <div style={{ position: 'relative' }}>
                      <input style={{ ...inputStyle, paddingRight: 42 }} type={showSecret ? 'text' : 'password'} placeholder="Paste your API secret" value={apiSecret} onChange={e => setApiSecret(e.target.value)} autoComplete="off" />
                      <button type="button" onClick={() => setShowSecret(s => !s)} style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-2)' }}>
                        <span className="material-icons" style={{ fontSize: 18 }}>{showSecret ? 'visibility_off' : 'visibility'}</span>
                      </button>
                    </div>
                  </div>

                  {error && <ErrorBox message={error} />}

                  <button type="submit" disabled={loading} style={submitBtnStyle(loading, broker.color)}>
                    {loading ? <Spinner /> : <><span className="material-icons" style={{ fontSize: 18 }}>link</span>Complete Registration</>}
                  </button>
                </form>
              </div>
            </div>
          )}

          {/* Login link */}
          <p style={{ textAlign: 'center', marginTop: 20, fontSize: 13, color: 'var(--nd-text-2)' }}>
            Already have an account?{' '}
            <Link to="/login" style={{ color: 'var(--nd-green)', fontWeight: 600, textDecoration: 'none' }}>Sign in</Link>
          </p>
          <p style={{ textAlign: 'center', marginTop: 10, fontSize: 12, color: 'var(--nd-text-3)' }}>
            For educational purposes only · NeuradeX © 2024
          </p>
        </div>
      </main>
    </div>
  );
};

// ── Shared sub-components ──────────────────────────────────────────────────────

const ErrorBox: React.FC<{ message: string }> = ({ message }) => (
  <div style={{ background: 'var(--nd-red-50)', border: '1px solid #fca5a5', borderRadius: 8, padding: '10px 14px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
    <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-red)' }}>error_outline</span>
    <span style={{ fontSize: 13, color: 'var(--nd-red)' }}>{message}</span>
  </div>
);

const Spinner: React.FC = () => (
  <span style={{ width: 18, height: 18, border: '2px solid rgba(255,255,255,0.3)', borderTopColor: '#fff', borderRadius: '50%', display: 'inline-block', animation: 'nd-spin 0.7s linear infinite' }} />
);

const submitBtnStyle = (loading: boolean, color: string): React.CSSProperties => ({
  width: '100%', height: 44, background: loading ? 'var(--nd-text-3)' : color, color: '#fff',
  border: 'none', borderRadius: 10, fontWeight: 600, fontSize: 15, cursor: loading ? 'not-allowed' : 'pointer',
  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, transition: 'background 0.15s',
});

export default Signup;
