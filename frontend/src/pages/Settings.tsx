import React, { useCallback, useEffect, useState } from 'react';
import apiService from '../services/api';

interface ProviderRow {
  name: string;
  requiresKey: boolean;
  available: boolean;
  enabled: boolean;
  hasKey: boolean | null;
}

const card: React.CSSProperties = {
  background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 20,
};

const PROVIDER_META: Record<string, { label: string; note: string }> = {
  groww:        { label: 'Groww',         note: 'Your broker — best for historical data (needs API key + live-data subscription for real-time).' },
  yahoo:        { label: 'Yahoo Finance', note: "Free, no key. Serves today's intraday (~1–2 min delay) and long daily history." },
  alphavantage: { label: 'Alpha Vantage', note: 'Free API key (rate-limited). BSE prices used as an NSE fallback.' },
};

const Settings: React.FC = () => {
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.getProviderSettings();
      const d = (res as any).data;
      setRows(d?.providers ?? []);
    } catch {
      setMsg('Could not load settings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= rows.length) return;
    const next = [...rows];
    [next[i], next[j]] = [next[j], next[i]];
    setRows(next);
  };
  const toggle = (i: number) => {
    const next = [...rows];
    next[i] = { ...next[i], enabled: !next[i].enabled };
    setRows(next);
  };

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const order = rows.map(r => r.name);
      const disabled = rows.filter(r => !r.enabled).map(r => r.name);
      const keysPayload: Record<string, string> = {};
      Object.entries(keys).forEach(([k, v]) => { if (v.trim()) keysPayload[k] = v.trim(); });
      const res = await apiService.updateProviderSettings({ order, disabled, keys: keysPayload });
      const d = (res as any).data;
      setRows(d?.providers ?? rows);
      setKeys({});
      setMsg('✓ Settings saved');
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ maxWidth: 760, margin: '0 auto', padding: '20px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <span className="material-icons" style={{ color: 'var(--nd-green)', fontSize: 22 }}>settings</span>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--nd-text-1)' }}>Settings</h1>
      </div>

      <div style={{ ...card }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 4 }}>Market Data Providers</div>
        <p style={{ margin: '0 0 16px', fontSize: 13, lineHeight: 1.6, color: 'var(--nd-text-2)' }}>
          The system fetches trading data from these sources <strong>in order</strong> — the first one that returns
          real data wins. Reorder by priority, enable/disable, and add API keys. If your top source is down or
          rate-limited, the next is used automatically.
        </p>

        {loading ? (
          <div style={{ fontSize: 13, color: 'var(--nd-text-3)' }}>Loading…</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {rows.map((r, i) => {
              const meta = PROVIDER_META[r.name] ?? { label: r.name, note: '' };
              return (
                <div key={r.name} style={{
                  border: '1px solid var(--nd-border)', borderRadius: 10, padding: 12,
                  background: 'var(--nd-bg)', opacity: r.enabled ? 1 : 0.6,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    {/* Reorder */}
                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                      <button onClick={() => move(i, -1)} disabled={i === 0} style={arrowBtn(i === 0)}>
                        <span className="material-icons" style={{ fontSize: 16 }}>keyboard_arrow_up</span>
                      </button>
                      <button onClick={() => move(i, 1)} disabled={i === rows.length - 1} style={arrowBtn(i === rows.length - 1)}>
                        <span className="material-icons" style={{ fontSize: 16 }}>keyboard_arrow_down</span>
                      </button>
                    </div>
                    {/* Priority badge */}
                    <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', width: 18 }}>#{i + 1}</span>
                    {/* Status dot */}
                    <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                      background: r.available ? 'var(--nd-green)' : 'var(--nd-red)',
                      boxShadow: r.available ? '0 0 6px var(--nd-green)' : 'none' }} />
                    {/* Name + note */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>
                        {meta.label}
                        <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--nd-text-3)', marginLeft: 8 }}>
                          {r.available ? 'available' : 'unavailable'}{r.requiresKey ? ' · needs key' : ''}
                        </span>
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 2 }}>{meta.note}</div>
                    </div>
                    {/* Enable toggle */}
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', flexShrink: 0 }}>
                      <input type="checkbox" checked={r.enabled} onChange={() => toggle(i)} />
                      <span style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>{r.enabled ? 'On' : 'Off'}</span>
                    </label>
                  </div>
                  {/* API key input */}
                  {r.requiresKey && (
                    <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                      <input
                        type="password"
                        placeholder={r.hasKey ? 'API key set — enter to replace' : 'Enter API key'}
                        value={keys[r.name] ?? ''}
                        onChange={e => setKeys(k => ({ ...k, [r.name]: e.target.value }))}
                        className="nd-input"
                        style={{ flex: 1, boxSizing: 'border-box' }}
                      />
                      {r.hasKey && <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-green)' }}>check_circle</span>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16 }}>
          <button onClick={save} disabled={saving || loading}
            style={{ background: 'var(--nd-green)', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 20px', fontSize: 14, fontWeight: 600, cursor: saving ? 'wait' : 'pointer' }}>
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
          {msg && <span style={{ fontSize: 13, color: msg.startsWith('✓') ? 'var(--nd-green)' : 'var(--nd-red)' }}>{msg}</span>}
        </div>
      </div>

      <div style={{ ...card, marginTop: 16 }}>
        <div style={{ fontSize: 13, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
          <strong style={{ color: 'var(--nd-text-2)' }}>Tip:</strong> Groww credentials are managed from the
          “Groww” badge in the top bar. Yahoo Finance needs no key and works out of the box — keep it enabled as a
          reliable fallback so the app keeps serving real data even when your broker is rate-limited.
        </div>
      </div>
    </div>
  );
};

const arrowBtn = (disabled: boolean): React.CSSProperties => ({
  background: 'none', border: 'none', cursor: disabled ? 'default' : 'pointer',
  color: disabled ? 'var(--nd-border)' : 'var(--nd-text-3)', padding: 0, lineHeight: 0,
});

export default Settings;
