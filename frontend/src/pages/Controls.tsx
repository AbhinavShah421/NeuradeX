/**
 * Trading Controls — every gate and ensemble knob, editable at runtime.
 *
 * These decide whether a trade happens, so the page is built to make a bad
 * change hard and an informed change easy:
 *
 *   • Bounds are enforced server-side (services/controls.py). The input hints
 *     at the range, but the API is what rejects 7.8 for a score_min of 78 —
 *     a typo there does not tighten the gate, it admits everything.
 *   • Anything differing from the shipped default is marked, counted in the
 *     header, and individually resettable. You can always see how far the
 *     system has drifted from what ships.
 *   • Where a knob has already been MEASURED, the result sits next to the
 *     input. Several of these are documented dead ends; a page that invites
 *     you to re-tune them without saying so is worse than no page.
 *
 * Response keys arrive camelCased by the app-wide axios interceptor.
 */
import React, { useCallback, useEffect, useState } from 'react';
import apiService from '../services/api';

const card: React.CSSProperties = {
  background: 'var(--nd-surface)', border: '1px solid var(--nd-border)',
  borderRadius: 12, padding: '14px 16px', marginBottom: 14,
};

const Controls: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, any>>({});

  const load = useCallback(async () => {
    try { setData(await apiService.getControls()); }
    catch (e: any) { setErr(e?.message || 'Could not load controls'); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const apply = async (id: string, value: any) => {
    setBusy(id); setErr(null);
    try {
      setData(await apiService.setControl(id, value));
      setDraft(d => { const n = { ...d }; delete n[id]; return n; });
    } catch (e: any) {
      setErr(`${id}: ${e?.response?.data?.detail || e?.message || 'rejected'}`);
    } finally { setBusy(null); }
  };

  const reset = async (id: string) => {
    setBusy(id); setErr(null);
    try { setData(await apiService.resetControl(id)); }
    catch (e: any) { setErr(e?.message || 'reset failed'); }
    finally { setBusy(null); }
  };

  if (!data) {
    return <div style={{ padding: 24, color: 'var(--nd-text-3)', fontSize: 13 }}>
      {err ?? 'Loading controls…'}
    </div>;
  }

  const controls: any[] = data.controls ?? [];
  const groups = controls.reduce((m: Record<string, any[]>, c) => {
    (m[c.group] = m[c.group] ?? []).push(c); return m;
  }, {});

  return (
    <div style={{ padding: '18px 20px', maxWidth: 980, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
        <h2 style={{ margin: 0, fontSize: 19, fontWeight: 700, color: 'var(--nd-text-1)' }}>Trading Controls</h2>
        {data.overrideCount > 0 ? (
          <>
            <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 5, background: '#f59e0b1f', color: '#f59e0b', border: '1px solid #f59e0b55' }}>
              {data.overrideCount} override{data.overrideCount === 1 ? '' : 's'} active
            </span>
            <button className="nd-btn" onClick={() => reset('all')} disabled={busy === 'all'}
              style={{ fontSize: 11, padding: '3px 10px' }}>Reset all to shipped defaults</button>
          </>
        ) : (
          <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>running shipped defaults</span>
        )}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', lineHeight: 1.6, marginBottom: 16 }}>
        {data.note}
      </div>

      {err && (
        <div style={{ ...card, borderColor: 'var(--nd-red)66', background: 'var(--nd-red)0f', color: 'var(--nd-red)', fontSize: 12 }}>
          {err}
        </div>
      )}

      {Object.entries(groups).map(([group, items]) => (
        <div key={group} style={card}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 10 }}>{group}</div>
          {(items as any[]).map(c => {
            const pending = draft[c.id];
            const shown = pending !== undefined ? pending : c.value;
            const dirty = pending !== undefined && String(pending) !== String(c.value);
            return (
              <div key={c.id} style={{ borderTop: '1px solid var(--nd-border)', padding: '10px 0' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12.5, color: 'var(--nd-text-1)', fontWeight: 500, minWidth: 210 }}>
                    {c.label}
                  </span>

                  {c.type === 'boolean' ? (
                    <input type="checkbox" checked={!!shown}
                      onChange={e => apply(c.id, e.target.checked)} disabled={busy === c.id} />
                  ) : c.type === 'enum' ? (
                    <select value={String(shown)} onChange={e => apply(c.id, e.target.value)}
                      disabled={busy === c.id}
                      style={{ background: 'var(--nd-bg)', color: 'var(--nd-text-1)', border: '1px solid var(--nd-border)', borderRadius: 6, padding: '3px 8px', fontSize: 12 }}>
                      {(c.options as string[]).map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  ) : (
                    <>
                      <input type="number" value={shown} min={c.min} max={c.max} step={c.step}
                        onChange={e => setDraft(d => ({ ...d, [c.id]: e.target.value }))}
                        onKeyDown={e => { if (e.key === 'Enter') apply(c.id, shown); }}
                        disabled={busy === c.id}
                        style={{ width: 92, background: 'var(--nd-bg)', color: 'var(--nd-text-1)', border: `1px solid ${dirty ? 'var(--nd-accent)' : 'var(--nd-border)'}`, borderRadius: 6, padding: '3px 8px', fontSize: 12 }} />
                      <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{c.min}–{c.max}</span>
                      {dirty && (
                        <button className="nd-btn" onClick={() => apply(c.id, shown)} disabled={busy === c.id}
                          style={{ fontSize: 10.5, padding: '2px 9px' }}>Apply</button>
                      )}
                    </>
                  )}

                  {c.overridden && (
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto' }}>
                      <span style={{ fontSize: 10, color: '#f59e0b' }}>ships {String(c.default)}</span>
                      <button className="nd-btn" onClick={() => reset(c.id)} disabled={busy === c.id}
                        style={{ fontSize: 10, padding: '1px 8px' }}>reset</button>
                    </span>
                  )}
                </div>

                {c.help && (
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)', lineHeight: 1.5, marginTop: 4 }}>{c.help}</div>
                )}
                {/* Measured result, shown where the change is made. */}
                {c.evidence && (
                  <div style={{ fontSize: 11, color: 'var(--nd-text-2)', lineHeight: 1.5, marginTop: 5, paddingLeft: 9, borderLeft: '2px solid var(--nd-accent)' }}>
                    <strong style={{ color: 'var(--nd-accent)' }}>Measured: </strong>{c.evidence}
                  </div>
                )}
                {c.danger && (
                  <div style={{ fontSize: 11, color: 'var(--nd-text-2)', lineHeight: 1.5, marginTop: 5, paddingLeft: 9, borderLeft: '2px solid var(--nd-red)' }}>
                    <strong style={{ color: 'var(--nd-red)' }}>Careful: </strong>{c.danger}
                  </div>
                )}
                <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 4, opacity: 0.8 }}>
                  read in {c.readIn}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
};

export default Controls;
