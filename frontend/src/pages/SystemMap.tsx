/**
 * SystemMap — live end-to-end architecture monitor.
 *
 * Built for one job: find what is broken. Every outage this project has had
 * looked fine from the outside (a container "Up" while its loop had not run in
 * five weeks; a queue with zero consumers quietly dropping every message), so
 * the page leads with a ranked fault list and only then draws the topology.
 *
 * Resource discipline: the backend sweep is gated behind a session. This page
 * arms it on mount, heartbeats while it lives, and disarms on unmount / tab
 * close. The server TTL covers the case where the browser dies without warning.
 *
 * NOTE: the axios layer camelCases every response key, so the backend's
 * `probe_ok` / `log_severity` / `age_hours` arrive here as `probeOk` /
 * `logSeverity` / `ageHours`.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import apiService from '../services/api';

// ── palette ──────────────────────────────────────────────────────────────────
const C = {
  bg: '#070b14',
  panel: 'rgba(16,24,42,0.72)',
  panelSolid: '#0d1524',
  grid: 'rgba(56,189,248,0.07)',
  edge: 'rgba(120,150,190,0.28)',
  text: '#dbe6f5',
  dim: '#7d8ca6',
  ok: '#2dd4bf',
  warn: '#fbbf24',
  down: '#fb5c7d',
  idle: '#5b6b85',
  accent: '#38bdf8',
  // Incoming edges get their own hue so "what feeds this" and "what this feeds"
  // are separable at a glance rather than one undifferentiated highlight.
  flowIn: '#a78bfa',
};

const LAYER_LABEL: Record<string, string> = {
  edge: 'Ingress', ui: 'UI', api: 'Core API', agents: 'Agents',
  decision: 'Decision', execution: 'Execution', data: 'Data Stores', ml: 'ML / Obs',
};

const COL_W = 178;
const ROW_H = 84;
const NODE_W = 150;
const NODE_H = 58;
const PAD_X = 24;
const PAD_Y = 64;

type Node = any;
type Edge = any;

function nodeColor(n: Node): string {
  if (n.running === false) return C.down;
  if (n.probeOk === false) return C.down;
  if (n.health === 'unhealthy') return C.down;
  if (n.logSeverity === 'error') return C.warn;
  if (n.running == null) return C.idle;
  return C.ok;
}

function edgeColor(e: Edge): string {
  if (e.status === 'down') return C.down;
  if (e.status === 'degraded') return C.warn;
  if (e.status === 'unknown') return C.idle;
  return C.edge;
}

const SystemMap: React.FC = () => {
  const [active, setActive] = useState(false);
  const [snap, setSnap] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<Node | null>(null);
  const [logs, setLogs] = useState<string[] | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [tab, setTab] = useState<'code' | 'logs'>('code');
  const [view, setView] = useState<'map' | 'pages'>('map');
  const [pages, setPages] = useState<any>(null);
  const [pageName, setPageName] = useState<string | null>(null);
  const [pageDetail, setPageDetail] = useState<any>(null);
  const [openCall, setOpenCall] = useState<string | null>(null);
  const [reqs, setReqs] = useState<Record<string, any>>({});
  const [store, setStore] = useState<any>(null);
  const [pubFlag, setPubFlag] = useState<any>(null);
  const [storeBusy, setStoreBusy] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [onlyApi, setOnlyApi] = useState(false);
  const [q, setQ] = useState('');
  const [railOpen, setRailOpen] = useState(true);
  const [zoom, setZoom] = useState(1);
  const mapBoxRef = useRef<HTMLDivElement | null>(null);
  const pollRef = useRef<any>(null);
  const beatRef = useRef<any>(null);
  const aliveRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const d = await apiService.getMonitorSnapshot();
      if (!aliveRef.current) return;
      if (d?.active === false) { setActive(false); return; }
      setSnap(d);
      setErr(null);
    } catch (e: any) {
      if (aliveRef.current) setErr(e?.message || 'snapshot failed');
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  const start = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      await apiService.startMonitor();
      setActive(true);
      await refresh();
      pollRef.current = setInterval(refresh, 12000);
      beatRef.current = setInterval(() => { apiService.heartbeatMonitor().catch(() => {}); }, 30000);
    } catch (e: any) {
      setErr(e?.message || 'could not start monitoring');
      setLoading(false);
    }
  }, [refresh]);

  const stop = useCallback(async () => {
    clearInterval(pollRef.current); clearInterval(beatRef.current);
    pollRef.current = null; beatRef.current = null;
    setActive(false);
    try { await apiService.stopMonitor(); } catch { /* TTL will expire it anyway */ }
  }, []);

  // Arm on open, disarm on close. `sendBeacon` is used for the unload path
  // because fetch/XHR are cancelled once the document starts tearing down.
  useEffect(() => {
    aliveRef.current = true;
    start();
    const onUnload = () => {
      try {
        navigator.sendBeacon?.(
          apiService.monitorStopUrl(),
          new Blob([], { type: 'application/json' }),
        );
      } catch { /* best effort — the server-side TTL disarms it regardless */ }
    };
    window.addEventListener('beforeunload', onUnload);
    return () => {
      aliveRef.current = false;
      window.removeEventListener('beforeunload', onUnload);
      clearInterval(pollRef.current); clearInterval(beatRef.current);
      apiService.stopMonitor().catch(() => {});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openNode = useCallback(async (n: Node) => {
    setSelected(n); setLogs(null); setDetail(null); setTab('code');
    apiService.getMonitorComponent(n.id).then(setDetail).catch(() => setDetail(null));
    // In-process agents have no container of their own — show the logs of the
    // process they run inside, which is where their votes are logged.
    const logSource = n.container || n.host;
    if (!logSource) return;
    setLogsLoading(true);
    try {
      const d = await apiService.getMonitorLogs(logSource, 200);
      setLogs(d?.lines ?? []);
    } catch {
      setLogs(['— could not load logs —']);
    } finally {
      setLogsLoading(false);
    }
  }, []);

  // Page catalogue is derived by parsing the repo, so it is static between
  // deploys — fetch once, independent of the monitoring session.
  useEffect(() => {
    if (view === 'pages' && !pages) {
      apiService.getMonitorPages().then(setPages).catch(() => setPages({ pages: [] }));
    }
  }, [view, pages]);

  // Log-store figures are cheap and independent of the monitoring session.
  useEffect(() => {
    if (view === 'map' && !store) {
      apiService.getLogStore().then(setStore).catch(() => setStore(null));
    }
    if (view === 'map' && !pubFlag) {
      apiService.getPublishFlag().then(setPubFlag).catch(() => setPubFlag(null));
    }
  }, [view, store, pubFlag]);

  const runStoreAction = useCallback(async (label: string, fn: () => Promise<any>) => {
    setStoreBusy(label);
    try { await fn(); setStore(await apiService.getLogStore()); }
    catch { /* surfaced by the unchanged figures */ }
    finally { setStoreBusy(null); }
  }, []);

  const openPage = useCallback(async (name: string) => {
    setPageName(name); setPageDetail(null); setOpenCall(null);
    try { setPageDetail(await apiService.getMonitorPage(name)); }
    catch { setPageDetail({ error: 'could not load page detail' }); }
  }, []);

  // ── layout ────────────────────────────────────────────────────────────────
  const layout = useMemo(() => {
    if (!snap?.nodes) return null;
    const layers: string[] = snap.layers || [];
    const pos: Record<string, { x: number; y: number }> = {};
    layers.forEach((ly, i) => {
      const inLayer = snap.nodes.filter((n: Node) => n.layer === ly);
      inLayer.forEach((n: Node, j: number) => {
        pos[n.id] = { x: PAD_X + i * COL_W, y: PAD_Y + j * ROW_H };
      });
    });
    const maxRows = Math.max(...layers.map(ly => snap.nodes.filter((n: Node) => n.layer === ly).length), 1);
    return {
      pos,
      width: PAD_X * 2 + layers.length * COL_W,
      height: PAD_Y + maxRows * ROW_H + 20,
      layers,
    };
  }, [snap]);

  // Scale the diagram to the available width. Recomputed on demand rather than
  // watched, so collapsing the rail then hitting Fit uses the new width.
  const fitZoom = useCallback(() => {
    const box = mapBoxRef.current;
    if (!box || !layout?.width) return;
    const avail = box.clientWidth - 16;
    setZoom(Math.max(0.3, Math.min(1, avail / layout.width)));
  }, [layout?.width]);

  // Fit once the diagram first arrives, and again whenever the rail toggles —
  // the container width changes, so a previously-fitted scale is stale.
  useEffect(() => {
    if (view === 'map' && layout?.width) {
      const id = setTimeout(fitZoom, 60);   // after the grid reflows
      return () => clearTimeout(id);
    }
  }, [view, layout?.width, railOpen, fitZoom]);

  // Neighbours of the focused node, split by direction so the highlight can say
  // which way data flows rather than just "these lines are related".
  const focus = useMemo(() => {
    if (!hoverId || !snap?.edges) return null;
    const upstream = new Set<string>();
    const downstream = new Set<string>();
    for (const e of snap.edges) {
      if (e.to === hoverId) upstream.add(e.from);
      if (e.from === hoverId) downstream.add(e.to);
    }
    return { id: hoverId, upstream, downstream };
  }, [hoverId, snap?.edges]);

  const faults = snap?.faults ?? [];
  const loops = snap?.loops ?? [];
  const summary = snap?.summary ?? {};

  return (
    <div className="nd-root"
         style={{ background: C.bg, minHeight: '100vh', color: C.text, padding: '18px 20px 40px',
                  fontFamily: 'Inter, system-ui, sans-serif', overflowX: 'hidden' }}>
      <style>{`
        @keyframes ndFlow { to { stroke-dashoffset: -24; } }
        @keyframes ndPulse { 0%,100% { opacity:.35 } 50% { opacity:.9 } }
        @keyframes ndSweep { 0% { transform: translateX(-100%) } 100% { transform: translateX(100%) } }
        .nd-node { cursor: pointer; transition: filter .15s ease, transform .15s ease; outline: none; }
        .nd-node:hover { filter: brightness(1.35); }
        /* Keyboard focus needs a visible ring; CSS beats the presentation
           attribute, so this wins over the inline stroke-width. */
        .nd-node:focus-visible rect { stroke-width: 3; }
        .nd-flow { stroke-dasharray: 5 7; animation: ndFlow 1.1s linear infinite; }
        .nd-scan { position:absolute; inset:0; overflow:hidden; pointer-events:none; }
        .nd-scan::after { content:''; position:absolute; top:0; bottom:0; width:40%;
          background:linear-gradient(90deg,transparent,rgba(56,189,248,.05),transparent);
          animation: ndSweep 6s linear infinite; }
        @media (prefers-reduced-motion: reduce) {
          .nd-flow, .nd-scan::after { animation: none !important; }
        }

        /* Layout lives in real CSS, not inline styles, so it can respond to
           width. A fixed 360px rail pushed the fault list off-screen on a
           phone and made the whole body scroll sideways. min-width:0 on the
           grid children is what actually stops the wide SVG from forcing the
           page wider than the viewport. */
        .nd-grid-map   { display:grid; grid-template-columns:minmax(0,1fr) 360px; gap:16px; align-items:start; }
        /* Collapsed rail: the diagram takes the width back. */
        .nd-grid-map.nd-rail-closed { grid-template-columns:minmax(0,1fr) 42px; }
        .nd-spine { align-self:stretch; min-height:180px; }
        .nd-grid-pages { display:grid; grid-template-columns:280px minmax(0,1fr); gap:16px; align-items:start; }
        .nd-grid-map > *, .nd-grid-pages > * { min-width:0; }

        @media (max-width: 900px) {
          /* One column on a phone — the vertical spine is a desktop affordance,
             so collapse reverts to the normal stacked rail. */
          .nd-grid-map, .nd-grid-pages,
          .nd-grid-map.nd-rail-closed { grid-template-columns:minmax(0,1fr); }
          .nd-spine { display:none !important; }
          /* Beats the inline display:none, so collapsing on desktop can never
             leave a phone with no fault panel at all. */
          .nd-rail   { display:flex !important; }
          /* Faults first on a phone — the point of the page is what is broken. */
          .nd-rail { order:-1; }
          .nd-pagelist { max-height:none; }
        }

        @media (max-width: 620px) {
          .nd-root   { padding:12px 12px 32px !important; }
          .nd-title  { font-size:22px !important; }
          .nd-toggle button { padding:8px 11px !important; font-size:12px !important; }
          .nd-chips  { gap:6px !important; }
          .nd-chips > div { padding:5px 9px !important; }
          .nd-drawer { width:100vw !important; padding:14px !important; }
          .nd-klinks a { flex:1 1 100%; text-align:center; }
        }
      `}</style>

      {/* header */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'center', marginBottom: 16 }}>
        <div style={{ flex: '1 1 320px', minWidth: 0 }}>
          <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11, letterSpacing: '.18em',
                        color: C.accent, textTransform: 'uppercase' }}>
            Live architecture monitor
          </div>
          <h1 className="nd-title" style={{ margin: '4px 0 0', fontSize: 26, fontWeight: 700, letterSpacing: '-.02em' }}>
            NeuradeX System Map
          </h1>
        </div>

        <div className="nd-chips" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="nd-toggle" style={{ display: 'flex', border: `1px solid ${C.edge}`, borderRadius: 8, overflow: 'hidden' }}>
            {([['map', 'Architecture'], ['pages', 'Pages & Requests']] as const).map(([v, label]) => (
              <button key={v} onClick={() => setView(v)}
                style={{
                  background: view === v ? 'rgba(56,189,248,.16)' : 'transparent',
                  border: 'none', borderRight: v === 'map' ? `1px solid ${C.edge}` : 'none',
                  color: view === v ? C.accent : C.dim, padding: '9px 14px',
                  cursor: 'pointer', fontSize: 12.5, fontWeight: 600,
                }}>{label}</button>
            ))}
          </div>
          <Chip label="Nodes" value={`${summary.running ?? '–'}/${summary.nodes ?? '–'}`} color={C.ok} />
          <Chip label="Critical" value={summary.critical ?? '–'} color={summary.critical ? C.down : C.idle} />
          <Chip label="Warning" value={summary.warning ?? '–'} color={summary.warning ? C.warn : C.idle} />
          <button
            onClick={active ? stop : start}
            style={{
              background: active ? 'rgba(251,92,125,.12)' : 'rgba(45,212,191,.12)',
              border: `1px solid ${active ? C.down : C.ok}`, color: active ? C.down : C.ok,
              padding: '9px 16px', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: 13,
            }}
          >
            {active ? '■ Stop monitoring' : '▶ Start monitoring'}
          </button>
          <button
            onClick={() => { setLoading(true); refresh(); }}
            disabled={!active}
            style={{
              background: 'transparent', border: `1px solid ${C.edge}`,
              color: active ? C.text : C.idle, padding: '9px 14px', borderRadius: 8,
              cursor: active ? 'pointer' : 'not-allowed', fontSize: 13,
            }}
          >↻ Refresh</button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14,
                    fontSize: 12, color: C.dim, fontFamily: 'ui-monospace, monospace' }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: active ? C.ok : C.idle,
          boxShadow: active ? `0 0 10px ${C.ok}` : 'none',
          animation: active ? 'ndPulse 1.8s ease-in-out infinite' : 'none',
        }} />
        {active
          ? <>MONITORING · polls every 12s · sweep {snap?.tookMs ?? '–'}ms · {snap?.generatedAt?.slice(11, 19) ?? '—'}</>
          : <>IDLE · no sweep running · zero cost while stopped</>}
        {loading && <span style={{ color: C.accent }}>· refreshing…</span>}
        {err && <span style={{ color: C.down }}>· {err}</span>}
      </div>

      {view === 'map' && !active && !snap && (
        <div style={{ ...panel, padding: 40, textAlign: 'center', color: C.dim }}>
          Monitoring is stopped. Press <b style={{ color: C.ok }}>Start monitoring</b> to probe every
          component, verify each link end to end, and check the learning loops.
        </div>
      )}

      {/* ── Pages & Requests ── */}
      {view === 'pages' && (
        <div className="nd-grid-pages">
          <Panel title={`PAGES & COMPONENTS${pages ? ` (${pages.pages.length})` : ''}`}
                 key="pagelist">
            {!pages && <div style={{ color: C.dim, fontSize: 12 }}>parsing repo…</div>}
            {pages && (
              <>
                <div style={{ fontSize: 11, color: C.dim, marginBottom: 10, lineHeight: 1.5 }}>
                  {pages.totals.apiMethods} client methods · {pages.totals.backendRoutes} backend routes.
                  Derived by parsing the source, so it cannot drift.
                </div>
                <div style={{ position: 'relative', marginBottom: 8 }}>
                  <input
                    value={q}
                    onChange={e => setQ(e.target.value)}
                    placeholder="Filter: page, component, API name or path"
                    aria-label="Filter pages and components"
                    style={{
                      width: '100%', boxSizing: 'border-box',
                      background: 'rgba(255,255,255,.04)', border: `1px solid ${C.edge}`,
                      borderRadius: 7, padding: '7px 26px 7px 10px', color: C.text,
                      fontSize: 12, fontFamily: 'inherit', outline: 'none',
                    }}
                  />
                  {q && (
                    <button onClick={() => setQ('')} aria-label="Clear filter"
                      style={{
                        position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)',
                        background: 'transparent', border: 'none', color: C.dim,
                        cursor: 'pointer', fontSize: 13, lineHeight: 1, padding: 2,
                      }}>✕</button>
                  )}
                </div>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11,
                                color: C.dim, marginBottom: 8, cursor: 'pointer' }}>
                  <input type="checkbox" checked={onlyApi} onChange={e => setOnlyApi(e.target.checked)} />
                  only files that call the API
                </label>
                <div className="nd-pagelist" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
                  {(() => {
                    const term = q.trim().toLowerCase();
                    const shown = pages.pages
                      .filter((p: any) => !onlyApi || p.uniqueApis > 0)
                      .map((p: any) => {
                        if (!term) return { p, hits: [] as string[] };
                        // A match on the file itself needs no explanation; a
                        // match on one of its APIs does, so collect those to
                        // show under the entry.
                        const self =
                          p.name.toLowerCase().includes(term) ||
                          (p.folder || '').toLowerCase().includes(term) ||
                          (p.route || '').toLowerCase().includes(term);
                        const hits = (p.apis || [])
                          .filter((a: any) =>
                            a.method.toLowerCase().includes(term) ||
                            (a.path || '').toLowerCase().includes(term) ||
                            (a.verb || '').toLowerCase() === term)
                          .map((a: any) => `${a.verb ?? ''} ${a.path ?? a.method}`.trim());
                        return (self || hits.length) ? { p, hits } : null;
                      })
                      .filter(Boolean) as { p: any; hits: string[] }[];

                    if (shown.length === 0) {
                      return (
                        <div style={{ color: C.dim, fontSize: 12, padding: '10px 2px', lineHeight: 1.5 }}>
                          Nothing matches “{q}”. Try an API path like
                          <span style={{ color: C.accent }}> /api/orders</span>, a method name, or a page name.
                        </div>
                      );
                    }
                    return shown.map(({ p, hits }) => {
                      const sel = pageName === p.key;
                      const none = p.uniqueApis === 0;
                      return (
                        <div key={p.key} onClick={() => openPage(p.key)}
                          style={{
                            padding: '8px 10px', borderRadius: 6, cursor: 'pointer', marginBottom: 4,
                            background: sel ? 'rgba(56,189,248,.14)' : 'transparent',
                            border: `1px solid ${sel ? C.accent : 'transparent'}`,
                            opacity: none ? 0.55 : 1,
                          }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                            <span style={{ fontSize: 12.5, fontWeight: 600 }}>{p.name}</span>
                            <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10,
                                           color: none ? C.dim : C.accent }}>
                              {p.uniqueApis}
                            </span>
                          </div>
                          <div style={{ fontSize: 10, color: C.dim, fontFamily: 'ui-monospace, monospace',
                                        wordBreak: 'break-all' }}>
                            {p.folder ? p.folder.replace('frontend/src/', '') : p.kind}
                            {p.route ? ` · ${p.route}` : ''}
                            {p.onLoad.length > 0 ? ` · ${p.onLoad.length} on load` : ''}
                          </div>
                          {hits.slice(0, 3).map((h, i) => (
                            <div key={i} style={{
                              fontSize: 10, color: C.accent, fontFamily: 'ui-monospace, monospace',
                              marginTop: 3, wordBreak: 'break-all',
                            }}>↳ {h}</div>
                          ))}
                          {hits.length > 3 && (
                            <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>
                              +{hits.length - 3} more
                            </div>
                          )}
                        </div>
                      );
                    });
                  })()}
                </div>
              </>
            )}
          </Panel>

          <div style={{ ...panel, padding: 16, minHeight: 300 }}>
            {!pageName && (
              <div style={{ color: C.dim, fontSize: 13, padding: 20 }}>
                Pick a page to see every request it fires — what triggers it, and the full path
                from the call site through nginx to the backend handler.
              </div>
            )}
            {pageName && !pageDetail && <div style={{ color: C.dim }}>loading…</div>}
            {pageDetail && !pageDetail.error && (
              <>
                <h2 style={{ margin: '0 0 2px', fontSize: 19 }}>{pageDetail.name}</h2>
                <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11, color: C.dim, marginBottom: 4 }}>
                  {pageDetail.route ? `route ${pageDetail.route} · ` : ''}
                  {pageDetail.kind} · {pageDetail.calls.length} API calls
                </div>
                {pageDetail.calls.length === 0 && (
                  <div style={{ ...panel, padding: 14, marginTop: 12, fontSize: 12.5,
                                color: C.dim, lineHeight: 1.55 }}>
                    This file makes no API calls of its own. It is presentational —
                    its data arrives as props from a parent that does the fetching.
                    Open the page that renders it to see where the data comes from.
                  </div>
                )}
                <a href={pageDetail.github} target="_blank" rel="noopener noreferrer"
                   style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10.5, color: C.accent }}>
                  {pageDetail.file} ↗
                </a>

                <div style={{ marginTop: 16 }}>
                  {pageDetail.calls.map((c: any) => {
                    const open = openCall === c.method;
                    const t = c.trace;
                    return (
                      <div key={c.method} style={{
                        border: `1px solid ${open ? C.accent : C.edge}`, borderRadius: 8,
                        marginBottom: 8, overflow: 'hidden',
                      }}>
                        <div onClick={() => {
                            const next = open ? null : c.method;
                            setOpenCall(next);
                            // Fetch the real exchanges once, on first expand.
                            if (next && t.path && reqs[c.method] === undefined) {
                              setReqs(r => ({ ...r, [c.method]: { loading: true } }));
                              apiService.getMonitorRequests(t.path, 15)
                                .then(d => setReqs(r => ({ ...r, [c.method]: d })))
                                .catch(() => setReqs(r => ({ ...r, [c.method]: { error: true } })));
                            }
                          }}
                          style={{
                            padding: '10px 12px', cursor: 'pointer', display: 'flex',
                            gap: 10, alignItems: 'center', flexWrap: 'wrap',
                            background: open ? 'rgba(56,189,248,.07)' : 'transparent',
                          }}>
                          <span style={{
                            fontFamily: 'ui-monospace, monospace', fontSize: 9.5, color: C.ok,
                            border: `1px solid ${C.edge}`, padding: '1px 6px', borderRadius: 4,
                          }}>{t.verb ?? '—'}</span>
                          <span style={{ fontSize: 12.5, fontWeight: 650 }}>{c.method}</span>
                          <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10.5,
                                         color: C.dim, flex: 1, minWidth: 0, wordBreak: 'break-all' }}>
                            {t.path ?? ''}
                          </span>
                          <span style={{ fontSize: 10, color: C.accent }}>{open ? '▲' : '▼'}</span>
                        </div>

                        {open && (
                          <div style={{ padding: '4px 12px 12px', borderTop: `1px solid ${C.edge}` }}>
                            <SecHead>TRIGGERED BY</SecHead>
                            {c.sites.map((s: any, i: number) => (
                              <div key={i} style={{ marginBottom: 6, fontSize: 11.5 }}>
                                <span style={{ color: C.warn }}>{s.trigger}</span>{' '}
                                <RefLink r={s.ref} />
                              </div>
                            ))}

                            <SecHead>END-TO-END PATH</SecHead>
                            {t.chain.map((st: any, i: number) => (
                              <div key={i} style={{
                                display: 'flex', gap: 10, alignItems: 'baseline',
                                padding: '5px 0', borderBottom: i < t.chain.length - 1 ? `1px solid ${C.edge}` : 'none',
                                flexWrap: 'wrap',
                              }}>
                                <span style={{
                                  fontFamily: 'ui-monospace, monospace', fontSize: 9.5,
                                  color: C.accent, minWidth: 92,
                                }}>{st.stage}</span>
                                <span style={{ fontSize: 11.5, flex: 1, minWidth: 140 }}>{st.detail}</span>
                                {st.ref && <RefLink r={st.ref} />}
                              </div>
                            ))}

                            <TryIt verb={t.verb} path={t.path} />

                            <SecHead>ACTUAL REQUESTS (last 96h)</SecHead>
                            <Exchanges data={reqs[c.method]} />

                            {t.kibana && (
                              <div className="nd-klinks" style={{ marginTop: 12, display: 'flex' }}>
                                <KLink href={t.kibana} label="Explore in Kibana"
                                       hint={`Kibana Discover, filtered to ${t.path}`} />
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {view === 'map' && snap && (
        <div className={railOpen ? 'nd-grid-map' : 'nd-grid-map nd-rail-closed'}>
          {/* ── topology ── */}
          <div style={{ ...panel, padding: 0, position: 'relative', overflow: 'hidden' }}>
            <div className="nd-scan" />
            <div style={{ padding: '10px 12px', borderBottom: `1px solid ${C.edge}`,
                          display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
                          fontSize: 12, color: C.dim, fontFamily: 'ui-monospace, monospace' }}>
              <span style={{ flex: 1, minWidth: 120, display: 'flex', gap: 12,
                             alignItems: 'center', flexWrap: 'wrap' }}>
                {focus ? (
                  <>
                    <b style={{ color: C.text, fontWeight: 600 }}>
                      {snap.nodes.find((n: Node) => n.id === focus.id)?.label}
                    </b>
                    <span><i style={{ display: 'inline-block', width: 16, height: 2,
                                      background: C.flowIn, verticalAlign: 'middle',
                                      marginRight: 5 }} />
                      {focus.upstream.size} feeding in</span>
                    <span><i style={{ display: 'inline-block', width: 16, height: 2,
                                      background: C.accent, verticalAlign: 'middle',
                                      marginRight: 5 }} />
                      {focus.downstream.size} fed from it</span>
                  </>
                ) : 'END-TO-END FLOW · hover to trace a path, tap for details'}
              </span>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                <ZoomBtn onClick={() => setZoom(z => Math.max(0.3, +(z - 0.15).toFixed(2)))} label="−" title="Zoom out" />
                <span style={{ minWidth: 42, textAlign: 'center', color: C.accent }}>
                  {Math.round(zoom * 100)}%
                </span>
                <ZoomBtn onClick={() => setZoom(z => Math.min(2.5, +(z + 0.15).toFixed(2)))} label="+" title="Zoom in" />
                <ZoomBtn onClick={fitZoom} label="Fit" title="Scale the whole diagram to fit" wide />
                <ZoomBtn onClick={() => setZoom(1)} label="1:1" title="Actual size" wide />
              </div>
            </div>
            <div ref={mapBoxRef} style={{ overflowX: 'auto', overflowY: 'hidden', padding: 8 }}>
              <svg
                viewBox={`0 0 ${layout?.width ?? 100} ${layout?.height ?? 100}`}
                width={(layout?.width ?? 100) * zoom}
                height={(layout?.height ?? 100) * zoom}
                style={{ display: 'block' }}
                role="img"
                aria-label="System architecture flow"
              >
                <defs>
                  <pattern id="ndgrid" width="26" height="26" patternUnits="userSpaceOnUse">
                    <path d="M26 0 L0 0 0 26" fill="none" stroke={C.grid} strokeWidth="1" />
                  </pattern>
                </defs>
                <rect width="100%" height="100%" fill="url(#ndgrid)" />

                {/* layer headings */}
                {layout?.layers.map((ly, i) => (
                  <text key={ly} x={PAD_X + i * COL_W + NODE_W / 2} y={28}
                        textAnchor="middle" fill={C.dim}
                        style={{ fontSize: 10, letterSpacing: '.16em', textTransform: 'uppercase',
                                 fontFamily: 'ui-monospace, monospace' }}>
                    {LAYER_LABEL[ly] ?? ly}
                  </text>
                ))}

                {/* edges under nodes */}
                {snap.edges.map((e: Edge, i: number) => {
                  const a = layout?.pos[e.from], b = layout?.pos[e.to];
                  if (!a || !b) return null;
                  const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
                  const x2 = b.x,          y2 = b.y + NODE_H / 2;
                  const mx = (x1 + x2) / 2;
                  const isDown = e.status === 'down';

                  // With a node focused, its own edges are coloured by
                  // direction and everything else recedes, so a single path can
                  // be followed across 41 nodes.
                  const outgoing = focus && e.from === focus.id;
                  const incoming = focus && e.to === focus.id;
                  const related = outgoing || incoming;

                  let col = edgeColor(e);
                  let width = isDown ? 2 : 1.4;
                  let op = isDown ? 0.95 : 0.75;
                  if (focus) {
                    if (related) {
                      col = isDown ? C.down : outgoing ? C.accent : C.flowIn;
                      width = 2.4;
                      op = 1;
                    } else {
                      op = 0.08;
                    }
                  }
                  return (
                    <path
                      key={i}
                      d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
                      fill="none" stroke={col}
                      strokeWidth={width}
                      strokeDasharray={isDown ? '4 4' : undefined}
                      className={e.status === 'ok' ? 'nd-flow' : undefined}
                      opacity={op}
                      style={{ transition: 'opacity .12s ease, stroke-width .12s ease' }}
                    >
                      <title>{`${e.from} → ${e.to} (${e.kind}) — ${e.status}${e.detail ? ': ' + e.detail : ''}`}</title>
                    </path>
                  );
                })}

                {/* nodes */}
                {snap.nodes.map((n: Node) => {
                  const p = layout?.pos[n.id];
                  if (!p) return null;
                  const col = nodeColor(n);
                  const isSel = selected?.id === n.id;
                  const isFocus = focus?.id === n.id;
                  const isUp = focus?.upstream.has(n.id);
                  const isDown_ = focus?.downstream.has(n.id);
                  const nodeOp = !focus ? 1 : (isFocus || isUp || isDown_) ? 1 : 0.22;
                  // Ring the neighbours in the same hue as their edges.
                  const ring = isFocus ? C.accent : isUp ? C.flowIn : isDown_ ? C.accent : col;
                  return (
                    <g key={n.id} className="nd-node" onClick={() => openNode(n)}
                       tabIndex={0} role="button"
                       aria-label={`${n.label} — open details`}
                       onMouseEnter={() => setHoverId(n.id)}
                       onMouseLeave={() => setHoverId(h => (h === n.id ? null : h))}
                       onFocus={() => setHoverId(n.id)}
                       onBlur={() => setHoverId(h => (h === n.id ? null : h))}
                       onKeyDown={ev => {
                         if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openNode(n); }
                       }}
                       opacity={nodeOp}
                       style={{ transition: 'opacity .12s ease' }}
                       transform={`translate(${p.x},${p.y})`}>
                      {/* In-process agents get a dashed outline: they are code
                          inside the runner, not a container you can restart. */}
                      <rect width={NODE_W} height={NODE_H} rx="9"
                            fill={isSel ? 'rgba(56,189,248,.16)'
                                  : isFocus ? 'rgba(56,189,248,.10)' : 'rgba(13,21,36,.92)'}
                            stroke={ring}
                            strokeWidth={isSel || isFocus ? 2.2 : (isUp || isDown_) ? 1.8 : 1.2}
                            strokeDasharray={n.kind === 'inprocess' || n.kind === 'component' ? '5 3' : undefined} />
                      <rect width="3.5" height={NODE_H} rx="2" fill={col} />
                      <text x="14" y="23" fill={C.text} style={{ fontSize: 12.5, fontWeight: 600 }}>
                        {n.label.length > 17 ? n.label.slice(0, 16) + '…' : n.label}
                      </text>
                      <text x="14" y="41" fill={C.dim}
                            style={{ fontSize: 10, fontFamily: 'ui-monospace, monospace' }}>
                        {n.kind === 'component'
                          ? `in ${(n.host ?? '').replace('stock-prediction-', '') || 'process'}`
                          : n.kind === 'inprocess'
                          ? `w ${n.weight ?? '—'}${n.lift != null
                              ? ` · ${n.lift >= 0 ? '+' : ''}${(n.lift * 100).toFixed(1)}pp` : ''}`
                          : n.running === false ? 'STOPPED'
                          : n.cpuPct != null ? `${n.cpuPct}% · ${Math.round(n.memUsedMb ?? 0)}MB`
                          : n.running == null ? 'no container' : 'running'}
                      </text>
                      {n.kind === 'inprocess' && n.lift != null && (
                        <circle cx={NODE_W - 14} cy="41" r="3"
                                fill={n.lift > 0 ? C.ok : C.warn}>
                          <title>{`BUY hit ${(n.buyRate * 100).toFixed(1)}% vs base ${(n.baseRate * 100).toFixed(1)}%`}</title>
                        </circle>
                      )}
                      <circle cx={NODE_W - 14} cy="17" r="4" fill={col}>
                        <title>{n.probeDetail || n.statusText || ''}</title>
                      </circle>
                      {n.logSeverity === 'error' && (
                        <text x={NODE_W - 26} y="45" fill={C.warn} style={{ fontSize: 10 }}>!</text>
                      )}
                    </g>
                  );
                })}
              </svg>
            </div>
          </div>

          {/* ── right rail ── */}
          {/* Collapsed: a slim vertical spine that still shows the fault count,
              so hiding the panel never hides the fact that something is wrong. */}
          {!railOpen && (
            <div className="nd-rail nd-spine" onClick={() => setRailOpen(true)}
                 title="Show faults and learning loops"
                 style={{
                   ...panel, cursor: 'pointer', padding: '12px 0',
                   display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
                 }}>
              <span style={{ color: C.accent, fontSize: 14 }}>‹</span>
              {summary.critical > 0 && (
                <span style={{
                  background: C.down, color: '#0b0f18', borderRadius: 10,
                  padding: '1px 6px', fontSize: 10.5, fontWeight: 700,
                  fontFamily: 'ui-monospace, monospace',
                }}>{summary.critical}</span>
              )}
              {summary.warning > 0 && (
                <span style={{
                  border: `1px solid ${C.warn}`, color: C.warn, borderRadius: 10,
                  padding: '1px 6px', fontSize: 10.5, fontWeight: 700,
                  fontFamily: 'ui-monospace, monospace',
                }}>{summary.warning}</span>
              )}
              <span style={{
                writingMode: 'vertical-rl', textOrientation: 'mixed',
                fontFamily: 'ui-monospace, monospace', fontSize: 11,
                letterSpacing: '.16em', color: C.dim, textTransform: 'uppercase',
              }}>Faults &amp; loops</span>
            </div>
          )}

          <div className="nd-rail"
               style={{ display: railOpen ? 'flex' : 'none', flexDirection: 'column', gap: 14 }}>
            <button onClick={() => setRailOpen(false)}
              style={{
                alignSelf: 'flex-end', background: 'transparent', border: `1px solid ${C.edge}`,
                color: C.dim, borderRadius: 6, padding: '3px 9px', cursor: 'pointer',
                fontSize: 11, fontFamily: 'ui-monospace, monospace',
              }}>collapse ›</button>
            <Panel title={`FAULTS DETECTED (${faults.length})`}>
              {faults.length === 0 && (
                <div style={{ color: C.ok, fontSize: 13, padding: '6px 0' }}>
                  No faults detected — every component, link and learning loop is healthy.
                </div>
              )}
              {faults.map((f: any, i: number) => (
                <FaultCard key={i} f={f} />
              ))}
            </Panel>

            <Panel title="EXECUTION CHAIN">
              {!pubFlag && <div style={{ color: C.dim, fontSize: 12 }}>reading flag…</div>}
              {pubFlag && (
                <>
                  <div style={{ fontSize: 11, color: C.dim, lineHeight: 1.5, marginBottom: 10 }}>
                    backend ensemble → ensemble-engine (MLflow gate) → risk-engine → trade-executor
                  </div>
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{
                      fontFamily: 'ui-monospace, monospace', fontSize: 11, fontWeight: 700,
                      color: pubFlag.enabled ? C.ok : C.idle,
                    }}>
                      {pubFlag.enabled ? '● PUBLISHING' : '○ DISARMED'}
                    </span>
                    <button
                      onClick={() => {
                        const next = !pubFlag.enabled;
                        if (next && !window.confirm(
                          'Arm the execution chain? Decisions will reach '
                          + 'risk-engine and, if they clear its 0.60 gate, '
                          + 'trade-executor (paper mode). Confidence is '
                          + 'de-saturated but measured NON-PREDICTIVE '
                          + '(out-of-sample correlation ~0).')) return;
                        apiService.setPublishFlag(next).then(setPubFlag).catch(() => {});
                      }}
                      style={{
                        background: pubFlag.enabled ? 'rgba(251,92,125,.10)' : 'rgba(45,212,191,.12)',
                        border: `1px solid ${pubFlag.enabled ? C.down : C.ok}`,
                        color: pubFlag.enabled ? C.down : C.ok,
                        borderRadius: 7, padding: '6px 13px', fontSize: 11.5,
                        fontWeight: 600, cursor: 'pointer',
                      }}>
                      {pubFlag.enabled ? 'Disarm' : 'Arm'}
                    </button>
                  </div>
                </>
              )}
            </Panel>

            <Panel title="LOG STORE">
              {!store && <div style={{ color: C.dim, fontSize: 12 }}>reading index sizes…</div>}
              {store && (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
                    <KV k="Total" v={`${(store.totalBytes / 1048576).toFixed(0)} MB`} />
                    <KV k="Indices" v={store.totalIndices} />
                    <KV k="Documents" v={store.totalDocs.toLocaleString()} />
                    <KV k="Reclaimable" v={`${(store.expiringBytes / 1048576).toFixed(0)} MB`}
                        c={store.expiringBytes > 0 ? C.warn : undefined} />
                  </div>

                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8,
                                fontSize: 11.5, color: C.dim, flexWrap: 'wrap' }}>
                    <span>Keep</span>
                    <select
                      value={store.retentionDays}
                      onChange={e => runStoreAction('retention',
                        () => apiService.setLogRetention(Number(e.target.value)))}
                      aria-label="Log retention window"
                      style={{
                        background: C.panelSolid, color: C.text, border: `1px solid ${C.edge}`,
                        borderRadius: 6, padding: '4px 7px', fontSize: 11.5,
                        fontFamily: 'ui-monospace, monospace',
                      }}>
                      {[3, 7, 14, 30, 60, 90, 180].map(d => (
                        <option key={d} value={d}>{d} days</option>
                      ))}
                    </select>
                    <span>· pruned nightly 03:20 IST</span>
                  </div>

                  <div style={{ fontSize: 11, color: C.dim, marginBottom: 8, lineHeight: 1.5 }}>
                    {store.expiringCount > 0
                      ? `${store.expiringCount} indices older than ${store.cutoff} are due to be dropped.`
                      : `Nothing older than ${store.cutoff}.`}
                  </div>

                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button
                      disabled={!!storeBusy || store.expiringCount === 0}
                      onClick={() => runStoreAction('prune', () => apiService.pruneLogs(false))}
                      style={{
                        background: 'rgba(45,212,191,.10)', border: `1px solid ${C.ok}`,
                        color: store.expiringCount === 0 ? C.dim : C.ok,
                        borderRadius: 7, padding: '6px 11px', fontSize: 11.5, fontWeight: 600,
                        cursor: storeBusy || store.expiringCount === 0 ? 'not-allowed' : 'pointer',
                      }}>
                      {storeBusy === 'prune' ? 'pruning…' : `Prune now`}
                    </button>
                    <button
                      disabled={!!storeBusy}
                      onClick={() => {
                        // Irreversible, so it asks — and says exactly what survives.
                        if (!window.confirm(
                          `Delete ALL log indices except today's?\n\n` +
                          `${store.totalIndices} indices · ${(store.totalBytes / 1048576).toFixed(0)} MB\n\n` +
                          `This cannot be undone.`)) return;
                        runStoreAction('purge', () => apiService.purgeLogs(true));
                      }}
                      style={{
                        background: 'rgba(251,92,125,.08)', border: `1px solid ${C.down}`,
                        color: C.down, borderRadius: 7, padding: '6px 11px',
                        fontSize: 11.5, fontWeight: 600,
                        cursor: storeBusy ? 'not-allowed' : 'pointer',
                      }}>
                      {storeBusy === 'purge' ? 'clearing…' : 'Clear all logs'}
                    </button>
                  </div>
                </>
              )}
            </Panel>

            <Panel title="LEARNING LOOPS">
              {loops.map((l: any) => {
                const col = l.status === 'ok' ? C.ok : l.status === 'stale' ? C.warn : C.down;
                return (
                  <div key={l.id} style={{ padding: '8px 0', borderBottom: `1px solid ${C.edge}` }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600 }}>{l.label}</span>
                      <span style={{ fontSize: 10.5, color: col, fontWeight: 700,
                                     fontFamily: 'ui-monospace, monospace' }}>
                        {l.status.toUpperCase()}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: C.dim, marginTop: 2, fontFamily: 'ui-monospace, monospace' }}>
                      {l.ageHours == null ? 'never run' : `${l.ageHours}h ago`}
                      {' · limit '}{l.thresholdHours}h · {l.detail}
                    </div>
                  </div>
                );
              })}
            </Panel>
          </div>
        </div>
      )}

      {/* ── detail drawer ── */}
      {selected && (
        <div
          onClick={() => setSelected(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(3,6,12,.66)', zIndex: 60 }}
        >
          <div
            onClick={ev => ev.stopPropagation()}
            className="nd-drawer"
            style={{
              position: 'absolute', top: 0, right: 0, bottom: 0, width: 'min(680px, 96vw)',
              background: C.panelSolid, borderLeft: `1px solid ${C.edge}`,
              padding: 20, overflowY: 'auto',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <h2 style={{ margin: 0, fontSize: 20 }}>{selected.label}</h2>
              <button onClick={() => setSelected(null)}
                      style={{ background: 'transparent', border: `1px solid ${C.edge}`, color: C.text,
                               borderRadius: 6, padding: '5px 11px', cursor: 'pointer' }}>✕</button>
            </div>
            <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11.5, color: C.dim, marginBottom: 14 }}>
              {selected.container || 'no container'}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(130px,1fr))', gap: 8, marginBottom: 16 }}>
              {selected.kind === 'inprocess' ? (
                <>
                  <KV k="Runs in" v="session-runner" />
                  <KV k="State" v={selected.running ? 'in-process' : 'host down'}
                      c={selected.running ? C.ok : C.down} />
                  <KV k="Learned weight" v={selected.weight ?? '—'} />
                  <KV k="BUY hit rate" v={selected.buyRate != null ? `${(selected.buyRate * 100).toFixed(1)}%` : '—'} />
                  <KV k="Base rate" v={selected.baseRate != null ? `${(selected.baseRate * 100).toFixed(1)}%` : '—'} />
                  <KV k="Lift vs base"
                      v={selected.lift != null ? `${selected.lift >= 0 ? '+' : ''}${(selected.lift * 100).toFixed(1)}pp` : '—'}
                      c={selected.lift == null ? undefined : selected.lift > 0 ? C.ok : C.warn} />
                </>
              ) : (
                <>
                  <KV k="State" v={selected.state ?? '—'} c={selected.running === false ? C.down : C.ok} />
                  <KV k="Health" v={selected.health ?? '—'} />
                  <KV k="Probe" v={selected.probeOk == null ? '—' : selected.probeOk ? 'pass' : 'FAIL'}
                      c={selected.probeOk === false ? C.down : C.ok} />
                  <KV k="CPU" v={selected.cpuPct != null ? `${selected.cpuPct}%` : '—'} />
                  <KV k="Memory" v={selected.memUsedMb != null ? `${Math.round(selected.memUsedMb)} MB` : '—'} />
                  <KV k="Logs" v={selected.logSeverity ?? 'ok'}
                      c={selected.logSeverity === 'error' ? C.down : selected.logSeverity === 'warning' ? C.warn : C.ok} />
                </>
              )}
            </div>

            {selected.probeDetail && (
              <div style={{ fontSize: 12, color: C.dim, marginBottom: 14 }}>
                Probe: {selected.probeDetail}
              </div>
            )}

            {/* Kibana deep links — query already applied, opens in a new tab */}
            {detail?.kibana && (
              <>
                <SecHead>OPEN IN KIBANA {detail.kibanaReady ? '' : '(data view unresolved)'}</SecHead>
                <div className="nd-klinks" style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 16 }}>
                  <KLink href={detail.kibana.requests} label="Requests & responses"
                         hint="api_request / api_response / api_error with status + duration" />
                  <KLink href={detail.kibana.errors} label="Errors only" hint="level:ERROR, last 4h" />
                  <KLink href={detail.kibana.all} label="All logs" hint="last 1h" />
                  <KLink href={detail.kibana.slow} label="Slow (>1s)" hint="duration_ms > 1000" />
                </div>
              </>
            )}

            <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
              {(['code', 'logs'] as const).map(t => (
                <button key={t} onClick={() => setTab(t)}
                  style={{
                    background: tab === t ? 'rgba(56,189,248,.14)' : 'transparent',
                    border: `1px solid ${tab === t ? C.accent : C.edge}`,
                    color: tab === t ? C.accent : C.dim,
                    padding: '6px 14px', borderRadius: 7, cursor: 'pointer',
                    fontSize: 12, fontWeight: 600,
                  }}>
                  {t === 'code' ? 'Code & flow' : 'Container logs'}
                </button>
              ))}
            </div>

            {tab === 'code' && (
              <div>
                {!detail && <div style={{ color: C.dim, fontSize: 12.5 }}>loading component map…</div>}
                {detail?.docs && (
                  <>
                    <div style={{ fontSize: 13.5, lineHeight: 1.55, marginBottom: 6 }}>{detail.docs.role}</div>
                    <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11,
                                  color: C.dim, marginBottom: 14 }}>{detail.docs.language}</div>

                    {detail.docs.entry && (
                      <>
                        <SecHead>ENTRY POINT</SecHead>
                        <RefLink r={detail.docs.entry} />
                      </>
                    )}

                    {detail.docs.flow?.length > 0 && (
                      <>
                        <SecHead>WHAT RUNS, IN ORDER</SecHead>
                        <ol style={{ margin: '0 0 16px', padding: 0, listStyle: 'none' }}>
                          {detail.docs.flow.map((f: any, i: number) => (
                            <li key={i} style={{
                              position: 'relative', paddingLeft: 26, marginBottom: 11,
                              borderLeft: `1px solid ${C.edge}`, marginLeft: 7, paddingBottom: 4,
                            }}>
                              <span style={{
                                position: 'absolute', left: -7, top: 2, width: 14, height: 14,
                                borderRadius: '50%', background: C.panelSolid, border: `1px solid ${C.accent}`,
                                color: C.accent, fontSize: 8.5, lineHeight: '13px', textAlign: 'center',
                                fontFamily: 'ui-monospace, monospace',
                              }}>{i + 1}</span>
                              <div style={{ fontSize: 12.5, fontWeight: 650, marginBottom: 2 }}>{f.step}</div>
                              <div style={{ fontSize: 11.5, color: C.dim, lineHeight: 1.5, marginBottom: 4 }}>{f.why}</div>
                              <RefLink r={f.ref} />
                            </li>
                          ))}
                        </ol>
                      </>
                    )}

                    {detail.docs.endpoints?.length > 0 && (
                      <>
                        <SecHead>API ENDPOINTS</SecHead>
                        <div style={{ marginBottom: 16 }}>
                          {detail.docs.endpoints.map((e: any, i: number) => (
                            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline',
                                                  marginBottom: 5, flexWrap: 'wrap' }}>
                              <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10,
                                             color: C.ok, border: `1px solid ${C.edge}`,
                                             padding: '1px 6px', borderRadius: 4 }}>{e.method}</span>
                              <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11.5 }}>{e.path}</span>
                              <RefLink r={e.ref} />
                            </div>
                          ))}
                        </div>
                      </>
                    )}

                    {(detail.docs.consumes?.length > 0 || detail.docs.publishes?.length > 0) && (
                      <>
                        <SecHead>MESSAGING</SecHead>
                        <div style={{ marginBottom: 16, fontSize: 12 }}>
                          {detail.docs.consumes?.length > 0 && (
                            <div style={{ marginBottom: 4 }}>
                              <span style={{ color: C.dim }}>consumes </span>
                              {detail.docs.consumes.map((q: string) => <Tag key={q} t={q} />)}
                            </div>
                          )}
                          {detail.docs.publishes?.length > 0 && (
                            <div>
                              <span style={{ color: C.dim }}>publishes </span>
                              {detail.docs.publishes.map((q: string) => <Tag key={q} t={q} />)}
                            </div>
                          )}
                        </div>
                      </>
                    )}

                    {detail.docs.tables?.length > 0 && (
                      <>
                        <SecHead>DATABASE TABLES</SecHead>
                        <div style={{ marginBottom: 16 }}>
                          {detail.docs.tables.map((t: string) => <Tag key={t} t={t} />)}
                        </div>
                      </>
                    )}

                    {detail.docs.gotchas?.length > 0 && (
                      <>
                        <SecHead>GOTCHAS — THINGS THAT HAVE ACTUALLY BROKEN</SecHead>
                        {detail.docs.gotchas.map((g: string, i: number) => (
                          <div key={i} style={{
                            borderLeft: `3px solid ${C.warn}`, background: 'rgba(251,191,36,.05)',
                            padding: '8px 11px', borderRadius: 6, marginBottom: 7,
                            fontSize: 12, lineHeight: 1.5,
                          }}>{g}</div>
                        ))}
                      </>
                    )}
                  </>
                )}
              </div>
            )}

            {tab === 'logs' && (
              <pre style={{
                background: '#05080f', border: `1px solid ${C.edge}`, borderRadius: 8,
                padding: 12, maxHeight: '52vh', overflow: 'auto', fontSize: 11.5,
                lineHeight: 1.5, color: '#c3d0e4', whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0,
              }}>
                {logsLoading ? 'loading…' : (logs?.join('\n') || 'no logs available')}
              </pre>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

// ── small pieces ─────────────────────────────────────────────────────────────
const panel: React.CSSProperties = {
  background: C.panel, border: `1px solid ${C.edge}`, borderRadius: 12,
  backdropFilter: 'blur(6px)',
};

/** One fault, with what to do about it.
 *
 *  The message alone leaves the reader to work out the remedy every time, and
 *  it is the same short list each time — read that container's log, restart
 *  that service, re-run the loop that consumed its slot without doing work. So
 *  the backend ships `actions` alongside every fault and this renders them.
 *
 *  Criticals open expanded: something is down and the next step should be on
 *  screen. Warnings open collapsed but say how many steps they carry, because
 *  six warnings with four steps each is a wall of text that gets scrolled past
 *  — which is the failure mode this panel exists to avoid.
 */
const FaultCard: React.FC<{ f: any }> = ({ f }) => {
  const critical = f.severity === 'critical';
  const actions: string[] = f.actions ?? [];
  const [open, setOpen] = useState(critical);
  const hue = critical ? C.down : C.warn;

  return (
    <div style={{
      borderLeft: `3px solid ${hue}`,
      background: 'rgba(255,255,255,.02)', padding: '9px 11px',
      borderRadius: 6, marginBottom: 7,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 3 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: hue,
                       fontFamily: 'ui-monospace, monospace', letterSpacing: '.08em' }}>
          {f.severity.toUpperCase()}
        </span>
        <span style={{ fontSize: 10, color: C.dim, fontFamily: 'ui-monospace, monospace' }}>{f.kind}</span>
      </div>
      <div style={{ fontSize: 12.5, lineHeight: 1.45 }}>{f.message}</div>

      {actions.length > 0 && (
        <>
          <button
            onClick={() => setOpen(o => !o)}
            style={{
              marginTop: 7, background: 'transparent', border: 'none', padding: 0,
              color: hue, cursor: 'pointer', fontSize: 11,
              fontFamily: 'ui-monospace, monospace', letterSpacing: '.06em',
            }}>
            {open ? '▾' : '▸'} what to do ({actions.length})
          </button>
          {open && (
            <ol style={{
              margin: '7px 0 1px', paddingLeft: 18, display: 'flex',
              flexDirection: 'column', gap: 5,
            }}>
              {actions.map((a, j) => (
                <li key={j} style={{ fontSize: 11.5, lineHeight: 1.5, color: C.text }}>
                  {renderAction(a)}
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </div>
  );
};

/** Set the runnable part of an action in monospace so a command or an endpoint
 *  reads as something to copy rather than as prose. Anything that starts with a
 *  shell verb, or an HTTP verb and a path, qualifies; everything else is left
 *  alone rather than guessed at. */
function renderAction(text: string): React.ReactNode {
  const m = text.match(/((?:docker|POST|GET|npm|curl)\s+[^\n]*)$/);
  if (!m) return text;
  const head = text.slice(0, m.index);
  return (
    <>
      {head}
      <code style={{
        fontFamily: 'ui-monospace, monospace', fontSize: 11,
        background: 'rgba(56,189,248,.10)', color: C.accent,
        padding: '1px 5px', borderRadius: 4, wordBreak: 'break-all',
      }}>{m[1]}</code>
    </>
  );
}

const Panel: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div style={{ ...panel, padding: 14 }}>
    <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10.5, letterSpacing: '.14em',
                  color: C.accent, marginBottom: 10 }}>{title}</div>
    {children}
  </div>
);

/** Real request→response pairs, read from Elasticsearch by the backend.
 *  Shown in-page because Kibana needs its own app to boot plus several XHRs and
 *  a resolved data view — the parts that fail on a phone over a tunnel. */
const Exchanges: React.FC<{ data: any }> = ({ data }) => {
  if (data === undefined) return <div style={{ fontSize: 11.5, color: C.dim }}>—</div>;
  if (data?.loading) return <div style={{ fontSize: 11.5, color: C.dim }}>loading…</div>;
  if (data?.error) return <div style={{ fontSize: 11.5, color: C.down }}>could not read request logs</div>;
  const ex = data?.exchanges ?? [];
  if (ex.length === 0) {
    return (
      <div style={{ fontSize: 11.5, color: C.dim, lineHeight: 1.5 }}>
        No requests recorded in the last 96h. This endpoint simply has not been
        called — the logging pipeline itself is fine.
      </div>
    );
  }
  return (
    <div style={{ marginBottom: 6 }}>
      {ex.map((e: any, i: number) => {
        const bad = e.status && e.status >= 400;
        return (
          <div key={i} style={{
            border: `1px solid ${C.edge}`, borderLeft: `3px solid ${bad ? C.down : C.ok}`,
            borderRadius: 6, padding: '7px 9px', marginBottom: 5,
            fontFamily: 'ui-monospace, monospace', fontSize: 10.5,
          }}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
              <span style={{ color: C.dim }}>{(e.ts || '').slice(11, 19)}</span>
              <span style={{ color: C.accent }}>{e.method}</span>
              <span style={{ color: bad ? C.down : C.ok }}>{e.status ?? '—'}</span>
              <span style={{ color: C.dim }}>
                {e.durationMs != null ? `${Math.round(e.durationMs)}ms` : ''}
              </span>
              {!e.hasResponse && <span style={{ color: C.warn }}>no response logged</span>}
            </div>
            <div style={{ color: C.text, wordBreak: 'break-all', marginTop: 2 }}>{e.path}</div>
            {e.responseBody && (
              <div style={{ color: C.dim, marginTop: 4, wordBreak: 'break-all',
                            maxHeight: 90, overflow: 'auto' }}>
                {String(e.responseBody).slice(0, 400)}
              </div>
            )}
            {e.error && <div style={{ color: C.down, marginTop: 3 }}>{e.error}</div>}
          </div>
        );
      })}
    </div>
  );
};

/** Swagger-style runner for one endpoint.
 *
 *  Path params are derived from the `${...}` placeholders in the recorded path,
 *  so the inputs match the real signature instead of a hand-written list.
 *  Non-GET verbs confirm before firing — these are live endpoints against live
 *  data, and POST/DELETE here really does mutate the running system.
 */
const TryIt: React.FC<{ verb?: string; path?: string }> = ({ verb, path }) => {
  const params = useMemo(() => {
    if (!path) return [] as string[];
    return Array.from(path.matchAll(/\$\{([^}]+)\}/g)).map(m => m[1]);
  }, [path]);

  const initialQuery = useMemo(() => {
    const qs = (path || '').split('?')[1];
    if (!qs) return '';
    // Strip template placeholders so the box starts as editable key=value text.
    return qs.replace(/\$\{[^}]+\}/g, '');
  }, [path]);

  const [vals, setVals] = useState<Record<string, string>>({});
  const [query, setQuery] = useState(initialQuery);
  const [body, setBody] = useState('');
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [bodyErr, setBodyErr] = useState<string | null>(null);

  if (!verb || !path) return null;
  const mutating = verb !== 'GET';

  const resolvedPath = params.reduce(
    (acc, p) => acc.replace(`\${${p}}`, encodeURIComponent(vals[p] ?? '')),
    path.split('?')[0],
  );
  const missing = params.filter(p => !vals[p]);

  const send = async () => {
    setBodyErr(null);
    let parsed: any = undefined;
    if (mutating && body.trim()) {
      try { parsed = JSON.parse(body); }
      catch (e: any) { setBodyErr(`Body is not valid JSON: ${e.message}`); return; }
    }
    const qp: Record<string, string> = {};
    query.split('&').forEach(kv => {
      const [k, ...rest] = kv.split('=');
      if (k && k.trim()) qp[k.trim()] = rest.join('=');
    });
    if (mutating && !window.confirm(
      `${verb} ${resolvedPath}\n\nThis runs against the LIVE system and can change data.\n\nSend it?`)) return;

    setBusy(true);
    try { setRes(await apiService.executeRequest(verb, resolvedPath, { params: qp, data: parsed })); }
    finally { setBusy(false); }
  };

  const okStatus = res && res.status >= 200 && res.status < 300;

  return (
    <div style={{ marginTop: 10, border: `1px solid ${C.edge}`, borderRadius: 8, padding: 11 }}>
      <SecHead>TRY IT {mutating ? '· MUTATES LIVE DATA' : ''}</SecHead>

      {params.map(p => (
        <div key={p} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
          <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11,
                         color: C.warn, minWidth: 78 }}>{p}</span>
          <input value={vals[p] ?? ''} onChange={e => setVals(v => ({ ...v, [p]: e.target.value }))}
            placeholder={`value for ${p}`}
            style={{
              flex: 1, minWidth: 0, background: 'rgba(255,255,255,.04)',
              border: `1px solid ${C.edge}`, borderRadius: 6, padding: '5px 8px',
              color: C.text, fontSize: 11.5, fontFamily: 'ui-monospace, monospace',
            }} />
        </div>
      ))}

      <input value={query} onChange={e => setQuery(e.target.value)}
        placeholder="query string, e.g. limit=10&symbol=RELIANCE"
        style={{
          width: '100%', boxSizing: 'border-box', background: 'rgba(255,255,255,.04)',
          border: `1px solid ${C.edge}`, borderRadius: 6, padding: '5px 8px',
          color: C.text, fontSize: 11.5, fontFamily: 'ui-monospace, monospace', marginBottom: 6,
        }} />

      {mutating && (
        <textarea value={body} onChange={e => setBody(e.target.value)} rows={4}
          placeholder='request body (JSON), e.g. {"symbol": "RELIANCE"}'
          style={{
            width: '100%', boxSizing: 'border-box', background: 'rgba(255,255,255,.04)',
            border: `1px solid ${bodyErr ? C.down : C.edge}`, borderRadius: 6,
            padding: '6px 8px', color: C.text, fontSize: 11.5,
            fontFamily: 'ui-monospace, monospace', marginBottom: 6, resize: 'vertical',
          }} />
      )}
      {bodyErr && <div style={{ color: C.down, fontSize: 11, marginBottom: 6 }}>{bodyErr}</div>}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button onClick={send} disabled={busy || missing.length > 0}
          title={missing.length ? `Fill in: ${missing.join(', ')}` : `Send ${verb}`}
          style={{
            background: mutating ? 'rgba(251,92,125,.10)' : 'rgba(45,212,191,.12)',
            border: `1px solid ${mutating ? C.down : C.ok}`,
            color: missing.length ? C.dim : mutating ? C.down : C.ok,
            borderRadius: 7, padding: '6px 14px', fontSize: 12, fontWeight: 600,
            cursor: busy || missing.length ? 'not-allowed' : 'pointer',
          }}>
          {busy ? 'sending…' : `Send ${verb}`}
        </button>
        <code style={{ fontSize: 10.5, color: C.dim, wordBreak: 'break-all' }}>{resolvedPath}</code>
      </div>

      {res && (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: 'flex', gap: 10, fontFamily: 'ui-monospace, monospace',
                        fontSize: 11, marginBottom: 5, flexWrap: 'wrap' }}>
            <span style={{ color: okStatus ? C.ok : C.down, fontWeight: 700 }}>
              {res.status || 'ERR'} {res.statusText}
            </span>
            <span style={{ color: C.dim }}>{res.durationMs}ms</span>
            {res.error && <span style={{ color: C.down }}>{res.error}</span>}
          </div>
          <pre style={{
            background: '#05080f', border: `1px solid ${C.edge}`, borderRadius: 6,
            padding: 9, maxHeight: 300, overflow: 'auto', fontSize: 11,
            color: '#c3d0e4', margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          }}>
            {(() => {
              try { return JSON.stringify(res.data, null, 2).slice(0, 12000); }
              catch { return String(res.data).slice(0, 12000); }
            })()}
          </pre>
        </div>
      )}
    </div>
  );
};

const ZoomBtn: React.FC<{ onClick: () => void; label: string; title: string; wide?: boolean }> =
  ({ onClick, label, title, wide }) => (
  <button onClick={onClick} title={title} aria-label={title}
    style={{
      background: 'transparent', border: `1px solid ${C.edge}`, color: C.text,
      borderRadius: 6, cursor: 'pointer', fontSize: 11.5,
      fontFamily: 'ui-monospace, monospace',
      minWidth: wide ? 36 : 26, height: 26, lineHeight: '22px', padding: 0,
    }}>{label}</button>
);

const SecHead: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10, letterSpacing: '.13em',
                color: C.accent, margin: '4px 0 8px' }}>{children}</div>
);

/** A code reference. Line numbers are resolved server-side against the working
 *  tree on every request, so they never go stale; `r.stale` means the symbol
 *  could not be found any more, which is worth showing rather than hiding. */
const RefLink: React.FC<{ r: any }> = ({ r }) => (
  <a href={r.github} target="_blank" rel="noopener noreferrer"
     title={r.stale ? 'Symbol not found — the docs have drifted from the code' : 'Open on GitHub'}
     style={{
       display: 'inline-block', fontFamily: 'ui-monospace, monospace', fontSize: 10.5,
       color: r.stale ? C.warn : C.accent, textDecoration: 'none',
       border: `1px solid ${r.stale ? C.warn : C.edge}`, borderRadius: 5,
       padding: '2px 7px', marginBottom: 4, wordBreak: 'break-all',
     }}>
    {r.label}{r.stale ? ' ⚠' : ' ↗'}
  </a>
);

const KLink: React.FC<{ href: string; label: string; hint: string }> = ({ href, label, hint }) => (
  <a href={href} target="_blank" rel="noopener noreferrer" title={hint}
     style={{
       display: 'inline-block', fontSize: 11.5, fontWeight: 600, color: C.text,
       background: 'rgba(56,189,248,.08)', border: `1px solid ${C.accent}`,
       borderRadius: 7, padding: '6px 11px', textDecoration: 'none',
     }}>
    🔍 {label}
  </a>
);

const Tag: React.FC<{ t: string }> = ({ t }) => (
  <span style={{
    display: 'inline-block', fontFamily: 'ui-monospace, monospace', fontSize: 10.5,
    background: 'rgba(255,255,255,.04)', border: `1px solid ${C.edge}`,
    borderRadius: 5, padding: '2px 7px', margin: '0 5px 5px 0', color: C.text,
  }}>{t}</span>
);

const Chip: React.FC<{ label: string; value: any; color: string }> = ({ label, value, color }) => (
  <div style={{ border: `1px solid ${C.edge}`, borderRadius: 8, padding: '6px 11px', background: C.panel }}>
    <div style={{ fontSize: 9.5, color: C.dim, letterSpacing: '.12em',
                  fontFamily: 'ui-monospace, monospace' }}>{label.toUpperCase()}</div>
    <div style={{ fontSize: 16, fontWeight: 700, color, fontFamily: 'ui-monospace, monospace' }}>{value}</div>
  </div>
);

const KV: React.FC<{ k: string; v: any; c?: string }> = ({ k, v, c }) => (
  <div style={{ background: 'rgba(255,255,255,.03)', border: `1px solid ${C.edge}`,
                borderRadius: 7, padding: '7px 10px' }}>
    <div style={{ fontSize: 9.5, color: C.dim, letterSpacing: '.1em',
                  fontFamily: 'ui-monospace, monospace' }}>{k.toUpperCase()}</div>
    <div style={{ fontSize: 13, fontWeight: 600, color: c ?? C.text }}>{v}</div>
  </div>
);

export default SystemMap;
