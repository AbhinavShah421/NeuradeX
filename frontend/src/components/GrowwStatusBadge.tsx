import React, { useEffect, useRef, useState } from 'react';
import api from '../services/api';
import { useAppStore } from '../stores/appStore';
import { useAuthStore } from '../stores/authStore';

type KeyType = 'approval' | 'totp';

interface GrowwStatus {
  status: string;
  tokenExpiry: string | null;
  timeRemainingSeconds: number | null;
  failureCount: number;
  failureReason: string;
  lastAttempt: string | null;
  hasToken: boolean;
  keyType: KeyType;
  /** false for an approval key — it cannot refresh without a manual tap in the Groww app. */
  unattended: boolean;
}

const GrowwStatusBadge: React.FC = () => {
  const { theme } = useAppStore();
  const { isAuthenticated } = useAuthStore();
  const dark = theme === 'dark';

  const [gwStatus, setGwStatus] = useState<GrowwStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [showCredForm, setShowCredForm] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [keyType, setKeyType] = useState<KeyType>('approval');
  const [credSaving, setCredSaving] = useState(false);
  const [message, setMessage] = useState('');
  const popoverRef = useRef<HTMLDivElement>(null);
  const POPOVER_W = 300;
  // The popover is right:0-anchored (right edge = trigger's right edge), which
  // clips off the LEFT edge of the screen when the trigger sits less than
  // POPOVER_W+8 from the left — true for the mobile-menu-header placement on
  // any phone narrower than ~380px. Shift it to keep an 8px margin.
  const [popRight, setPopRight] = useState(0);
  useEffect(() => {
    if (!open || !popoverRef.current) return;
    const clamp = () => {
      const rect = popoverRef.current!.getBoundingClientRect();
      setPopRight(Math.min(0, rect.right - (POPOVER_W + 8)));
    };
    clamp();
    window.addEventListener('resize', clamp);
    return () => window.removeEventListener('resize', clamp);
  }, [open]);

  const load = async () => {
    if (!isAuthenticated) return;
    try {
      const res = await api.getGrowwStatus();
      if (res.status === 'success' && res.data) setGwStatus(res.data);
    } catch {}
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, [isAuthenticated]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
        setShowCredForm(false);
        setMessage('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  if (!isAuthenticated || !gwStatus) return null;

  const isOk = gwStatus.status === 'ok';
  const isFailed = gwStatus.status === 'failed';
  const dotColor = isOk ? '#22c55e' : isFailed ? '#ef4444' : '#eab308';
  const label = isOk ? 'Groww Live' : isFailed ? 'Groww Disconnected' : 'Groww Unknown';

  const handleRefresh = async () => {
    setRefreshing(true);
    setMessage('');
    try {
      const res = await api.refreshGrowwToken();
      if (res.data?.success) {
        setMessage('Token refreshed successfully');
        await load();
      } else {
        setMessage(res.data?.error || 'Refresh failed');
      }
    } catch (e: any) {
      setMessage(e?.response?.data?.detail || 'Refresh failed');
    } finally {
      setRefreshing(false);
    }
  };

  const handleCredSave = async () => {
    if (!apiKey.trim() || !apiSecret.trim()) return;
    setCredSaving(true);
    setMessage('');
    try {
      const res = await api.updateGrowwCredentials(apiKey.trim(), apiSecret.trim(), keyType);
      setMessage(
        res.status === 'success'
          ? 'Credentials updated and token refreshed'
          : keyType === 'approval'
            ? 'Saved — now approve the session in the Groww app'
            : 'Saved, but the token refresh failed — check the reason above'
      );
      await load();
      setShowCredForm(false);
      setApiKey('');
      setApiSecret('');
    } catch (e: any) {
      setMessage(e?.response?.data?.detail || 'Failed to update credentials');
    } finally {
      setCredSaving(false);
    }
  };

  const formatTime = (secs: number | null) => {
    if (secs === null) return '—';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  };

  return (
    <div ref={popoverRef} style={{ position: 'relative', display: 'inline-block' }}>
      {/* Badge trigger */}
      <button
        onClick={() => { setOpen(o => !o); setMessage(''); setShowCredForm(false); }}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          background: dark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.06)',
          border: `1px solid ${dark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)'}`,
          borderRadius: 20, padding: '4px 10px', cursor: 'pointer',
          fontSize: 12, color: dark ? '#cbd5e1' : '#374151',
        }}
      >
        <span style={{
          width: 8, height: 8, borderRadius: '50%', background: dotColor,
          boxShadow: `0 0 6px ${dotColor}`,
          animation: isOk ? 'nd-pulse 2s infinite' : 'none',
          display: 'inline-block', flexShrink: 0,
        }} />
        {label}
      </button>

      {/* Popover */}
      {open && (
        <div style={{
          position: 'absolute', top: '110%', right: popRight, zIndex: 1000,
          width: POPOVER_W,
          background: dark ? '#1e2837' : '#ffffff',
          border: `1px solid ${dark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)'}`,
          borderRadius: 12, padding: 16,
          boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
        }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 12, color: dark ? '#e2e8f0' : '#1e293b' }}>
            Groww API Status
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
            <Row label="Status" dark={dark}>
              <span style={{ color: dotColor, fontWeight: 600, textTransform: 'capitalize' }}>
                {gwStatus.status}
              </span>
            </Row>
            <Row label="Token valid" dark={dark}>
              {gwStatus.hasToken ? (isOk ? `${formatTime(gwStatus.timeRemainingSeconds)} remaining` : 'Expired') : 'None'}
            </Row>
            <Row label="Key type" dark={dark}>
              <span style={{ color: gwStatus.unattended ? '#22c55e' : '#eab308', fontWeight: 600 }}>
                {gwStatus.keyType === 'totp' ? 'TOTP · auto' : 'Approval · manual'}
              </span>
            </Row>
            {gwStatus.tokenExpiry && (
              <Row label="Expires" dark={dark}>
                {new Date(gwStatus.tokenExpiry).toLocaleString()}
              </Row>
            )}
            {gwStatus.failureCount > 0 && (
              <Row label="Failures" dark={dark}>
                <span style={{ color: '#ef4444' }}>{gwStatus.failureCount}</span>
              </Row>
            )}
            {gwStatus.failureReason && (
              <div style={{ fontSize: 11, color: '#ef4444', wordBreak: 'break-word', marginTop: 2 }}>
                {gwStatus.failureReason.slice(0, 160)}
              </div>
            )}
            {!gwStatus.unattended && (
              <div style={{
                fontSize: 10, lineHeight: 1.45, marginTop: 2, padding: '6px 8px', borderRadius: 6,
                background: dark ? 'rgba(234,179,8,0.12)' : '#fef9c3',
                color: dark ? '#eab308' : '#854d0e',
              }}>
                An approval key needs a manual tap in the Groww app every day. Switch to a
                TOTP key under Update Keys to refresh unattended.
              </div>
            )}
          </div>

          {message && (
            <div style={{
              fontSize: 11, padding: '6px 8px', borderRadius: 6, marginBottom: 10,
              background: message.includes('success') || message.includes('refreshed')
                ? (dark ? 'rgba(34,197,94,0.15)' : '#dcfce7')
                : (dark ? 'rgba(239,68,68,0.15)' : '#fee2e2'),
              color: message.includes('success') || message.includes('refreshed') ? '#22c55e' : '#ef4444',
            }}>
              {message}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginBottom: showCredForm ? 12 : 0 }}>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              style={{
                flex: 1, padding: '7px 0', borderRadius: 8, border: 'none',
                background: 'var(--nd-primary)', color: '#000',
                fontWeight: 600, fontSize: 12, cursor: refreshing ? 'not-allowed' : 'pointer',
                opacity: refreshing ? 0.7 : 1,
              }}
            >
              {refreshing ? 'Refreshing…' : 'Refresh Token'}
            </button>
            <button
              onClick={() => {
                // Start from whatever is configured, so reopening the form and
                // saving cannot quietly downgrade a TOTP key back to approval.
                if (!showCredForm) setKeyType(gwStatus.keyType ?? 'approval');
                setShowCredForm(f => !f);
              }}
              style={{
                flex: 1, padding: '7px 0', borderRadius: 8,
                border: `1px solid ${dark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.15)'}`,
                background: 'transparent', color: dark ? '#94a3b8' : '#64748b',
                fontWeight: 600, fontSize: 12, cursor: 'pointer',
              }}
            >
              Update Keys
            </button>
          </div>

          {showCredForm && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
              {/* The two key types take a different second field and behave very
                  differently day to day, so make the choice explicit up front. */}
              <div style={{ display: 'flex', gap: 6 }}>
                {(['approval', 'totp'] as KeyType[]).map(kt => {
                  const active = keyType === kt;
                  return (
                    <button
                      key={kt}
                      onClick={() => setKeyType(kt)}
                      style={{
                        flex: 1, padding: '6px 0', borderRadius: 8, fontSize: 11, fontWeight: 600,
                        cursor: 'pointer',
                        border: `1px solid ${active ? 'var(--nd-primary)' : (dark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.15)')}`,
                        background: active ? 'var(--nd-primary)' : 'transparent',
                        color: active ? '#000' : (dark ? '#94a3b8' : '#64748b'),
                      }}
                    >
                      {kt === 'totp' ? 'TOTP' : 'Approval'}
                    </button>
                  );
                })}
              </div>
              <div style={{ fontSize: 10, lineHeight: 1.45, color: dark ? '#64748b' : '#9ca3af' }}>
                {keyType === 'totp'
                  ? 'Refreshes on its own, including the pre-market warm-up at 08:45 IST.'
                  : 'Needs a manual session approval in the Groww app every day.'}
              </div>
              <input
                placeholder="API Key (JWT)"
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                style={{
                  padding: '7px 10px', borderRadius: 8, fontSize: 11,
                  background: dark ? 'rgba(255,255,255,0.07)' : '#f8fafc',
                  border: `1px solid ${dark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)'}`,
                  color: dark ? '#e2e8f0' : '#1e293b', outline: 'none',
                }}
              />
              <input
                placeholder={keyType === 'totp' ? 'TOTP seed (base32)' : 'API Secret'}
                value={apiSecret}
                onChange={e => setApiSecret(e.target.value)}
                type="password"
                style={{
                  padding: '7px 10px', borderRadius: 8, fontSize: 11,
                  background: dark ? 'rgba(255,255,255,0.07)' : '#f8fafc',
                  border: `1px solid ${dark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)'}`,
                  color: dark ? '#e2e8f0' : '#1e293b', outline: 'none',
                }}
              />
              <button
                onClick={handleCredSave}
                disabled={credSaving || !apiKey.trim() || !apiSecret.trim()}
                style={{
                  padding: '7px 0', borderRadius: 8, border: 'none',
                  background: 'var(--nd-primary)', color: '#000',
                  fontWeight: 600, fontSize: 12,
                  cursor: credSaving ? 'not-allowed' : 'pointer',
                  opacity: credSaving || !apiKey.trim() || !apiSecret.trim() ? 0.6 : 1,
                }}
              >
                {credSaving ? 'Saving…' : 'Save & Refresh'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const Row: React.FC<{ label: string; dark: boolean; children: React.ReactNode }> = ({ label, dark, children }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
    <span style={{ color: dark ? '#64748b' : '#9ca3af' }}>{label}</span>
    <span style={{ color: dark ? '#cbd5e1' : '#374151', textAlign: 'right' }}>{children}</span>
  </div>
);

export default GrowwStatusBadge;
