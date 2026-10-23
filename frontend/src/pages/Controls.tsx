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
 * The controls are mixing-desk faders rather than number boxes, because the
 * question you actually ask here is "how far along its range is this, and how
 * far from what ships" — and a bare "0.68" answers neither. The lit track
 * answers the first; the tick under the cap answers the second.
 *
 * Response keys arrive camelCased by the app-wide axios interceptor.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import apiService from '../services/api';
import AutopilotBanner from '../components/controls/AutopilotBanner';
import DeliveryAutopilotCard from '../components/controls/DeliveryAutopilotCard';

const card: React.CSSProperties = {
  background: 'var(--nd-surface)', border: '1px solid var(--nd-border)',
  borderRadius: 12, padding: '14px 16px', marginBottom: 14,
};

/** Where `v` sits between min and max, as a percentage for the CSS fill. */
function pct(v: any, min: number, max: number): number {
  const n = Number(v);
  if (!Number.isFinite(n) || !Number.isFinite(min) || !Number.isFinite(max) || max <= min) return 0;
  return Math.min(100, Math.max(0, ((n - min) / (max - min)) * 100));
}

/** Trailing zeros make a fader readout twitch as you drag. Match the step. */
function fmt(v: any, step: number): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? '');
  const decimals = step && step < 1 ? String(step).split('.')[1]?.length ?? 2 : 0;
  return n.toFixed(decimals);
}

interface FaderProps {
  c: any;
  shown: any;
  dirty: boolean;
  busy: boolean;
  onDrag: (v: string) => void;
  onCommit: (v: any) => void;
}

/**
 * One numeric channel.
 *
 * The drag itself is local state only — `onCommit` fires on pointer release,
 * on Enter, and on blur, never on movement. Writing per `input` event would
 * send a live gate change for every pixel of a drag, at a session that is
 * reading these values on the next candle.
 */
const Fader: React.FC<FaderProps> = ({ c, shown, dirty, busy, onDrag, onCommit }) => {
  const min = Number(c.min), max = Number(c.max), step = Number(c.step) || 1;
  // The value at the moment the drag started, so a release that changed
  // nothing does not fire a pointless write.
  const dragFrom = useRef<string | null>(null);

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: '1 1 320px' }}>
      <div className="nd-fader-wrap"
           style={{ ['--pct' as any]: `${pct(shown, min, max)}%`,
                    ['--defpct' as any]: `${pct(c.default, min, max)}%` }}>
        <div className="nd-fader-track" />
        {/* Only worth drawing when it differs from where the cap already is. */}
        {c.overridden && <div className="nd-fader-default" title={`ships ${c.default}`} />}
        <input
          type="range" className="nd-fader"
          min={min} max={max} step={step}
          value={Number(shown)}
          disabled={busy}
          aria-label={c.label}
          onPointerDown={() => { dragFrom.current = String(shown); }}
          onChange={e => onDrag(e.target.value)}
          onPointerUp={() => {
            if (dragFrom.current !== null && dragFrom.current !== String(shown)) onCommit(shown);
            dragFrom.current = null;
          }}
          onKeyUp={e => { if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'].includes(e.key)) onCommit(shown); }}
        />
      </div>

      <input
        type="number" className="nd-readout"
        value={shown}
        min={min} max={max} step={step}
        disabled={busy}
        aria-label={`${c.label} value`}
        onChange={e => onDrag(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') onCommit(shown); }}
        onBlur={() => { if (dirty) onCommit(shown); }}
      />

      <span style={{ fontSize: 9.5, color: 'var(--nd-text-3)', fontFamily: 'ui-monospace, monospace', whiteSpace: 'nowrap' }}>
        {fmt(min, step)}–{fmt(max, step)}
      </span>

      {/* Release and Enter both commit, so this is a fallback rather than the
          main path — but a drag whose pointerup lands outside the window
          leaves the row amber, and without this there would be nothing to
          press to finish it. */}
      {dirty && (
        <button className="nd-btn" onClick={() => onCommit(shown)} disabled={busy}
          style={{ fontSize: 10, padding: '2px 9px', flex: '0 0 auto' }}>apply</button>
      )}
    </div>
  );
};

const Controls: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, any>>({});
  // Which gate preset the tabs are showing. Null until the payload arrives, so
  // it can open on the preset that is actually LIVE rather than on whichever
  // one happens to sort first — the tuning you came to do is almost always for
  // the gate the runner is reading.
  const [gateTab, setGateTab] = useState<string | null>(null);
  // The gate presets carry a one-line description of what each one demands.
  // That used to live on the dashboard card; it is the context you want when
  // deciding whether to make a different preset live.
  const [gateInfo, setGateInfo] = useState<any>(null);

  const load = useCallback(async () => {
    try { setData(await apiService.getControls()); }
    catch (e: any) { setErr(e?.message || 'Could not load controls'); }
    try { const r: any = await apiService.getTradeGate(); setGateInfo(r?.data ?? r); }
    catch { /* descriptions are a nicety; the faders work without them */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  /**
   * Make a preset the live gate.
   *
   * Deliberately NOT what clicking a tab does. Browsing the three presets has
   * to stay free — you should be able to read what Strict demands without
   * changing what the runner trades on the next candle. So selection and
   * activation are separate actions, and this one confirms first.
   */
  const activateGate = async (mode: string, label: string) => {
    if (!window.confirm(
      `Make the ${label} gate live?\n\n`
      + 'Every paper, backtest and autopilot entry will be judged by this '
      + 'preset from the next candle.')) return;
    setBusy('gate'); setErr(null);
    try {
      await apiService.setTradeGate(mode);
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail || e?.message || 'could not change the gate');
    } finally { setBusy(null); }
  };

  const apply = async (id: string, value: any) => {
    setBusy(id); setErr(null);
    try {
      setData(await apiService.setControl(id, value));
      setDraft(d => { const n = { ...d }; delete n[id]; return n; });
    } catch (e: any) {
      setErr(`${id}: ${e?.response?.data?.detail || e?.message || 'rejected'}`);
      // The server rejected it, so the fader must snap back to the value that
      // is actually live rather than sitting on a number nothing is using.
      setDraft(d => { const n = { ...d }; delete n[id]; return n; });
    } finally { setBusy(null); }
  };

  const reset = async (id: string) => {
    setBusy(id); setErr(null);
    try { setData(await apiService.resetControl(id)); setDraft({}); }
    catch (e: any) { setErr(e?.message || 'reset failed'); }
    finally { setBusy(null); }
  };

  if (!data) {
    return <div style={{ padding: 24, color: 'var(--nd-text-3)', fontSize: 13 }}>
      {err ?? 'Loading controls…'}
    </div>;
  }

  const controls: any[] = data.controls ?? [];
  const activeGate: string | null = data.activeGate ?? null;

  // The three gate presets become one tabbed bank; everything else stays a
  // plain bank. Stacking all three made the page three times longer than it
  // needed to be and, worse, made the live one look no different from the two
  // that are only sitting there.
  const gateControls = controls.filter(c => c.gateMode);
  const gateModes = Array.from(new Set(gateControls.map(c => c.gateMode)));
  const shownGate = (gateTab && gateModes.includes(gateTab)) ? gateTab
                  : (activeGate && gateModes.includes(activeGate)) ? activeGate
                  : gateModes[0];
  const gateLabel = (m: string) =>
    (gateControls.find(c => c.gateMode === m)?.group ?? m).replace(/^Entry gate — /, '');
  const gateDesc = (m: string) =>
    (gateInfo?.options ?? []).find((o: any) => o.id === m)?.desc ?? '';

  const groups = controls.filter(c => !c.gateMode).reduce((m: Record<string, any[]>, c) => {
    (m[c.group] = m[c.group] ?? []).push(c); return m;
  }, {});

  /**
   * One control row. Shared by the tabbed entry-gate bank and the plain
   * banks below it, so a gate knob and an ensemble knob cannot drift into
   * looking or behaving differently.
   */
  const renderRow = (c: any) => {
          const pending = draft[c.id];
          const shown = pending !== undefined ? pending : c.value;
          const dirty = pending !== undefined && String(pending) !== String(c.value);
          // A knob carrying a documented hazard lights red before you read
          // the warning underneath it.
          const rowClass = `nd-fader-row${c.danger ? ' is-danger' : ''}${dirty ? ' is-dirty' : ''}`;

          return (
            <div key={c.id} className={rowClass}
                 style={{ borderTop: '1px solid var(--nd-border)', padding: '12px 0' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12.5, color: 'var(--nd-text-1)', fontWeight: 500, minWidth: 200, flex: '0 0 auto' }}>
                  {c.label}
                </span>

                {c.type === 'boolean' ? (
                  <button
                    className="nd-switch" role="switch" aria-checked={!!shown}
                    aria-label={c.label} disabled={busy === c.id}
                    onClick={() => apply(c.id, !shown)} />
                ) : c.type === 'enum' ? (
                  <div className="nd-seg" role="group" aria-label={c.label}>
                    {(c.options as string[]).map(o => (
                      <button key={o} aria-pressed={String(shown) === o}
                        disabled={busy === c.id}
                        onClick={() => apply(c.id, o)}>{o}</button>
                    ))}
                  </div>
                ) : (
                  <Fader
                    c={c} shown={shown} dirty={dirty} busy={busy === c.id}
                    onDrag={v => setDraft(d => ({ ...d, [c.id]: v }))}
                    onCommit={v => apply(c.id, v)}
                  />
                )}

                {c.overridden && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto' }}>
                    <span style={{ fontSize: 10, color: '#f59e0b', fontFamily: 'ui-monospace, monospace' }}>
                      ships {String(c.default)}
                    </span>
                    <button className="nd-btn" onClick={() => reset(c.id)} disabled={busy === c.id}
                      style={{ fontSize: 10, padding: '1px 8px' }}>reset</button>
                  </span>
                )}
              </div>

              {c.help && (
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)', lineHeight: 1.5, marginTop: 6 }}>{c.help}</div>
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
              <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 4, opacity: 0.8, fontFamily: 'ui-monospace, monospace' }}>
                read in {c.readIn}
              </div>
            </div>
    );
  };

  return (
    <div className="nd-console" style={{ padding: '18px 20px', maxWidth: 980, margin: '0 auto' }}>
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
        {data.note} Faders apply when you let go.
      </div>

      {err && (
        <div style={{ ...card, borderColor: 'var(--nd-red)66', background: 'var(--nd-red)0f', color: 'var(--nd-red)', fontSize: 12 }}>
          {err}
        </div>
      )}

      {/* ── Entry gate: three presets, one tab strip ───────────────────── */}
      {gateModes.length > 0 && (
        <div className="nd-bank" style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-2)',
                           fontFamily: 'ui-monospace, monospace', letterSpacing: '.12em', textTransform: 'uppercase' }}>
              Entry gate
            </span>
            <div className="nd-tabs" role="tablist" aria-label="Entry gate preset">
              {gateModes.map(m => (
                <button key={m} role="tab"
                  aria-selected={m === shownGate}
                  className={m === activeGate ? 'is-live' : undefined}
                  onClick={() => setGateTab(m)}>
                  {/* The lamp says LIVE, the tab selection says "what I am
                      looking at". They are different questions and the page
                      has to answer both at once — you can inspect Strict while
                      Gentle is the one trading. */}
                  {m === activeGate && <i className="nd-lamp" aria-hidden="true" />}
                  {gateLabel(m)}
                </button>
              ))}
            </div>
            {activeGate ? (
              shownGate === activeGate ? (
                <span className="nd-live-note is-live">live — the runner reads this gate</span>
              ) : (
                <>
                  <span className="nd-live-note">
                    viewing {gateLabel(shownGate)} · <strong>{gateLabel(activeGate)}</strong> is live
                  </span>
                  <button className="nd-make-live" disabled={busy === 'gate'}
                    onClick={() => activateGate(shownGate, gateLabel(shownGate))}>
                    make {gateLabel(shownGate)} live
                  </button>
                </>
              )
            ) : (
              <span className="nd-live-note">active gate unknown</span>
            )}
          </div>

          {/* What this preset actually demands, from the gate presets endpoint. */}
          {gateDesc(shownGate) && (
            <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', lineHeight: 1.55,
                          marginBottom: 4, paddingBottom: 10 }}>
              {gateDesc(shownGate)}
            </div>
          )}
          {gateControls.filter(c => c.gateMode === shownGate).map(c => renderRow(c))}
        </div>
      )}

      {Object.entries(groups).map(([group, items]) => (
        <div key={group} className="nd-bank" style={card}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-2)', marginBottom: 12,
                        fontFamily: 'ui-monospace, monospace', letterSpacing: '.12em', textTransform: 'uppercase' }}>
            {group}
          </div>
          {(items as any[]).map(c => renderRow(c))}
        </div>
      ))}

      {/* ── The self-running loops ──────────────────────────────────────────
          Moved off the dashboard 2026-09-09, and kept BELOW the knobs: the
          gates and weights above decide what a trade has to look like, these
          decide whether anything runs at all. You tune first and arm second,
          so they read in that order. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '22px 0 10px' }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-2)',
                       fontFamily: 'ui-monospace, monospace', letterSpacing: '.12em', textTransform: 'uppercase' }}>
          Automation
        </span>
        <span style={{ flex: 1, height: 1, background: 'var(--nd-border)' }} />
      </div>
      <AutopilotBanner />
      <DeliveryAutopilotCard />
    </div>
  );
};

export default Controls;
