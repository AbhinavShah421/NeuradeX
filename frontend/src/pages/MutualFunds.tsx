import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../services/api';

type Tab = 'holdings' | 'optimize' | 'screener' | 'all';

const pct = (v: any) => (v === null || v === undefined) ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
const pctColor = (v: any) => v === null || v === undefined ? 'var(--nd-text-3)' : v >= 0 ? 'var(--nd-green)' : 'var(--nd-red)';
const inr = (v: number) => Number(v || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });

const ReturnCells: React.FC<{ f: any }> = ({ f }) => (
  <>
    {['1m', '3m', '6m', '1y', '3y'].map(k => (
      <td key={k} style={{ textAlign: 'right', padding: '8px 10px', whiteSpace: 'nowrap', fontSize: 12.5, color: pctColor(f[k]), fontWeight: 600 }}>{pct(f[k])}</td>
    ))}
  </>
);

const MutualFunds: React.FC = () => {
  const [tab, setTab] = useState<Tab>('holdings');

  // search + add
  const [q, setQ] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [units, setUnits] = useState('');
  const [invested, setInvested] = useState('');
  const [picked, setPicked] = useState<any>(null);

  // holdings + scan
  const [holdings, setHoldings] = useState<any>(null);
  const [scan, setScan] = useState<any>(null);
  const [loadingScan, setLoadingScan] = useState(false);

  // optimizer
  const [opt, setOpt] = useState<any>(null);
  const [optRisk, setOptRisk] = useState('moderate');
  const [optLoading, setOptLoading] = useState(false);
  const runOptimize = useCallback(async (r: string) => {
    setOptLoading(true);
    try { setOpt((await apiService.mfOptimize(r) as any).data); } catch {} finally { setOptLoading(false); }
  }, []);
  useEffect(() => { if (tab === 'optimize' && !opt) runOptimize(optRisk); /* eslint-disable-next-line */ }, [tab]);

  // all funds (browse)
  const [allData, setAllData] = useState<any>(null);
  const [allQ, setAllQ] = useState('');
  const [allLoading, setAllLoading] = useState(false);
  const [allSort, setAllSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'name', dir: 'asc' });
  const loadAll = useCallback(async (q: string, page: number) => {
    setAllLoading(true);
    try { setAllData((await apiService.mfAll(q, page, 25) as any).data); } catch {} finally { setAllLoading(false); }
  }, []);
  useEffect(() => { if (tab === 'all' && !allData) loadAll('', 1); /* eslint-disable-next-line */ }, [tab]);
  useEffect(() => {
    if (tab !== 'all') return;
    const t = setTimeout(() => { loadAll(allQ, 1); }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line
  }, [allQ]);

  // screener
  const [cats, setCats] = useState<string[]>([]);
  const [cat, setCat] = useState('Flexi Cap');
  const [sortBy, setSortBy] = useState<'return' | 'risk'>('return');
  const [screen, setScreen] = useState<any>(null);
  const [loadingScreen, setLoadingScreen] = useState(false);

  const loadHoldings = useCallback(async () => {
    try { setHoldings((await apiService.mfHoldings() as any).data); } catch {}
  }, []);
  useEffect(() => { loadHoldings(); apiService.mfCategories().then((r: any) => setCats(r.data ?? [])).catch(() => {}); }, [loadHoldings]);

  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return; }
    const t = setTimeout(async () => {
      try { setResults((await apiService.mfSearch(q) as any).data ?? []); } catch {}
    }, 350);
    return () => clearTimeout(t);
  }, [q]);

  const addFund = async () => {
    if (!picked) return;
    try {
      await apiService.mfAddHolding({ schemeCode: picked.schemeCode, units: units ? +units : undefined, invested: invested ? +invested : undefined });
      setPicked(null); setQ(''); setResults([]); setUnits(''); setInvested(''); setScan(null);
      loadHoldings();
    } catch {}
  };
  const removeFund = async (code: number) => { try { await apiService.mfRemoveHolding(code); setScan(null); loadHoldings(); } catch {} };

  const runScan = async () => {
    setLoadingScan(true);
    try { setScan((await apiService.mfScan() as any).data); } catch {} finally { setLoadingScan(false); }
  };
  const runScreener = useCallback(async (c: string, s: 'return' | 'risk') => {
    setLoadingScreen(true);
    try { setScreen((await apiService.mfScreener(c, 20, s) as any).data); } catch {} finally { setLoadingScreen(false); }
  }, []);
  useEffect(() => { if (tab === 'screener') runScreener(cat, sortBy); /* eslint-disable-next-line */ }, [tab]);

  const funds = holdings?.funds ?? [];

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">Mutual Funds</h1>
        <p className="nd-page-sub">Real NAV &amp; returns (AMFI) · AI performance scan &amp; replacement suggestions</p>
      </div>

      <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px', marginBottom: 14 }}>
        Groww's API doesn't expose mutual-fund holdings, so add the funds you hold below — all NAV/return data is live from AMFI.
      </div>

      <div className="nd-card" style={{ padding: 0 }}>
        {/* Scrolls horizontally instead of overflowing the whole page — 4 tabs
            don't fit a 390px screen at this padding/font-size. */}
        <div style={{
          display: 'flex', borderBottom: '1px solid var(--nd-border)', padding: '0 16px',
          overflowX: 'auto', scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch',
        }}>
          {([['holdings', 'My Funds'], ['optimize', 'Optimize'], ['screener', 'Screener'], ['all', 'All Funds']] as [Tab, string][]).map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} style={{
              padding: '12px 16px', border: 'none', background: 'none', cursor: 'pointer', fontSize: 13,
              fontWeight: tab === id ? 700 : 500, color: tab === id ? 'var(--nd-green)' : 'var(--nd-text-2)',
              borderBottom: tab === id ? '2px solid var(--nd-green)' : '2px solid transparent', marginBottom: -1,
              whiteSpace: 'nowrap', flexShrink: 0,
            }}>{label}</button>
          ))}
        </div>

        {tab === 'holdings' && (
          <div style={{ padding: '16px 20px' }}>
            {/* Add a fund */}
            <div style={{ marginBottom: 16, position: 'relative' }}>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <input className="nd-input" style={{ flex: '1 1 280px' }} placeholder="Search a fund (e.g. Parag Parikh Flexi Cap)…"
                  value={picked ? picked.name : q} onChange={e => { setPicked(null); setQ(e.target.value); }} />
                <input className="nd-input" style={{ width: 110 }} placeholder="Units" value={units} onChange={e => setUnits(e.target.value.replace(/[^0-9.]/g, ''))} />
                <input className="nd-input" style={{ width: 130 }} placeholder="Invested ₹" value={invested} onChange={e => setInvested(e.target.value.replace(/[^0-9]/g, ''))} />
                <button onClick={addFund} disabled={!picked} style={{ padding: '9px 16px', borderRadius: 8, border: 'none', background: picked ? 'var(--nd-green)' : 'var(--nd-border)', color: '#fff', fontWeight: 700, fontSize: 12.5, cursor: picked ? 'pointer' : 'default' }}>Add fund</button>
              </div>
              {results.length > 0 && !picked && (
                <div style={{ position: 'absolute', zIndex: 20, left: 0, right: 0, top: '100%', marginTop: 4, background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, maxHeight: 240, overflow: 'auto', boxShadow: 'var(--nd-shadow-md)' }}>
                  {results.map(r => (
                    <div key={r.schemeCode} onClick={() => { setPicked(r); setResults([]); }}
                      style={{ padding: '8px 12px', fontSize: 12.5, cursor: 'pointer', borderBottom: '1px solid var(--nd-border)' }}>{r.name}</div>
                  ))}
                </div>
              )}
            </div>

            {/* Summary + scan */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 14 }}>
              {holdings?.totalCurrent != null && (
                <>
                  <div className="nd-card" style={{ padding: '10px 14px' }}><div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Current value</div><div style={{ fontSize: 15, fontWeight: 700 }}>₹{inr(holdings.totalCurrent)}</div></div>
                  {holdings.totalGain != null && <div className="nd-card" style={{ padding: '10px 14px' }}><div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Total gain</div><div style={{ fontSize: 15, fontWeight: 700, color: pctColor(holdings.totalGain) }}>₹{inr(holdings.totalGain)}</div></div>}
                </>
              )}
              <button onClick={runScan} disabled={loadingScan || !funds.length} style={{ marginLeft: 'auto', padding: '9px 16px', borderRadius: 8, border: '1px solid var(--nd-purple)', background: loadingScan ? 'transparent' : 'var(--nd-purple)', color: loadingScan ? 'var(--nd-purple)' : '#fff', fontWeight: 700, fontSize: 12.5, cursor: funds.length ? 'pointer' : 'default' }}>
                {loadingScan ? 'Scanning…' : '✨ AI scan & replace'}
              </button>
            </div>

            {!funds.length ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>No funds yet — search above and add the funds you hold.</div>
            ) : (
              <div style={{ overflowX: 'auto', width: '100%' }}>
                <table style={{ minWidth: 720, width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                  <thead><tr style={{ color: 'var(--nd-text-3)', fontSize: 11 }}>
                    {[['Fund', 'left'], ['NAV', 'right'], ['Value', 'right'], ['1M', 'right'], ['3M', 'right'], ['6M', 'right'], ['1Y', 'right'], ['3Y', 'right'], ['', 'right']].map(([h, a], i) => (
                      <th key={i} style={{ textAlign: a as any, padding: '6px 10px', whiteSpace: 'nowrap', fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {funds.map((f: any) => (
                      <tr key={f.schemeCode} style={{ borderTop: '1px solid var(--nd-border)' }}>
                        <td style={{ padding: '8px 10px', maxWidth: 260 }}>
                          <div style={{ fontWeight: 600, color: 'var(--nd-text-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={f.name}>{f.name}</div>
                          <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.category} · {f.fundHouse}</div>
                        </td>
                        <td style={{ textAlign: 'right', padding: '8px 10px', whiteSpace: 'nowrap' }}>₹{f.nav}</td>
                        <td style={{ textAlign: 'right', padding: '8px 10px', whiteSpace: 'nowrap' }}>{f.currentValue ? `₹${inr(f.currentValue)}` : '—'}</td>
                        <ReturnCells f={f} />
                        <td style={{ textAlign: 'right', padding: '8px 10px' }}>
                          {/* padding + minHeight/minWidth turn a bare glyph into a real ~34px tap target */}
                          <button onClick={() => removeFund(f.schemeCode)} title="Remove fund"
                            style={{ background: 'none', border: 'none', color: 'var(--nd-red)', cursor: 'pointer', fontSize: 18, padding: 8, minWidth: 34, minHeight: 34, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>×</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* AI scan results */}
            {scan && (
              <div style={{ marginTop: 18 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 8 }}>
                  AI verdict · {scan.replace} replace, {scan.count - scan.replace} keep/review
                </div>
                {scan.results.map((r: any) => {
                  const col = r.verdict === 'REPLACE' ? '#ef4444' : r.verdict === 'REVIEW' ? '#f59e0b' : '#22c55e';
                  return (
                    <div key={r.fund.schemeCode} style={{ border: '1px solid var(--nd-border)', borderLeft: `3px solid ${col}`, borderRadius: 8, padding: '10px 12px', marginBottom: 8 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--nd-text-1)' }}>{r.fund.name}</span>
                        <span style={{ fontSize: 10.5, fontWeight: 700, color: col }}>{r.verdict} · 1Y {pct(r.fund['1y'])} · risk-adj {r.riskAdjusted ?? '—'} (cat med {r.categoryMedianRa ?? '—'})</span>
                      </div>
                      {r.suggestion && (
                        <div style={{ marginTop: 6, fontSize: 11.5, color: 'var(--nd-text-2)', background: 'var(--nd-surface)', borderRadius: 6, padding: '7px 9px' }}>
                          ↪ Switch to <strong style={{ color: 'var(--nd-green)' }}>{r.suggestion.name}</strong> — {r.suggestion.reason}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {tab === 'optimize' && (
          <div style={{ padding: '16px 20px' }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
              <span style={{ fontSize: 12.5, color: 'var(--nd-text-2)' }}>Risk profile:</span>
              {['conservative', 'moderate', 'aggressive'].map(r => (
                <button key={r} onClick={() => { setOptRisk(r); runOptimize(r); }} style={{
                  padding: '5px 11px', fontSize: 12, borderRadius: 7, cursor: 'pointer', textTransform: 'capitalize',
                  border: `1px solid ${optRisk === r ? 'var(--nd-green)' : 'var(--nd-border)'}`,
                  background: optRisk === r ? 'var(--nd-green)' : 'transparent', color: optRisk === r ? '#fff' : 'var(--nd-text-2)',
                }}>{r}</button>
              ))}
            </div>
            {optLoading ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Optimising your MF portfolio…</div>
            ) : !opt ? null : opt.note ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>{opt.note}</div>
            ) : (
              <>
                <div style={{ fontSize: 12.5, color: 'var(--nd-text-2)', background: 'var(--nd-surface)', borderRadius: 8, padding: '10px 12px', marginBottom: 14 }}>
                  🤖 {opt.aiSummary || opt.summary}
                </div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
                  {[['Keep', opt.keep, '#22c55e'], ['Replace', opt.replace, '#ef4444'], ['Consolidate', opt.consolidate, '#f59e0b'], ['Avg risk-adj', opt.avgRiskAdjusted, 'var(--nd-text-1)']].map(([l, v, c], i) => (
                    <div key={i} className="nd-card" style={{ padding: '10px 14px' }}><div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{l as string}</div><div style={{ fontSize: 16, fontWeight: 700, color: c as string }}>{v as any}</div></div>
                  ))}
                </div>

                <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Asset allocation vs {opt.risk} target</div>
                  {opt.allocation.map((a: any) => {
                    const col = a.status === 'overweight' ? '#ef4444' : a.status === 'underweight' ? '#f59e0b' : '#22c55e';
                    return (
                      <div key={a.asset} style={{ marginBottom: 10 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 3 }}>
                          <span style={{ fontWeight: 600 }}>{a.asset}</span>
                          <span style={{ color: 'var(--nd-text-2)' }}>now {a.currentPct}% · target {a.targetPct}% <span style={{ color: col, fontWeight: 700 }}>{a.status}</span></span>
                        </div>
                        <div style={{ position: 'relative', height: 8, background: 'var(--nd-border)', borderRadius: 4 }}>
                          <div style={{ position: 'absolute', height: 8, borderRadius: 4, width: `${Math.min(100, a.currentPct)}%`, background: col, opacity: 0.85 }} />
                          <div style={{ position: 'absolute', height: 8, width: 2, background: 'var(--nd-text-1)', left: `${Math.min(100, a.targetPct)}%` }} title={`target ${a.targetPct}%`} />
                        </div>
                      </div>
                    );
                  })}
                  {(opt.notes ?? []).map((n: string, i: number) => <div key={i} style={{ fontSize: 11.5, color: 'var(--nd-text-3)', marginTop: 4 }}>• {n}</div>)}
                </div>

                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Action plan</div>
                {opt.actions.map((a: any, i: number) => {
                  const col = a.verdict === 'REPLACE' ? '#ef4444' : a.verdict === 'CONSOLIDATE' ? '#f59e0b' : '#22c55e';
                  return (
                    <div key={i} style={{ border: '1px solid var(--nd-border)', borderLeft: `3px solid ${col}`, borderRadius: 8, padding: '10px 12px', marginBottom: 8 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--nd-text-1)' }}>{a.fund.name}</span>
                        <span style={{ fontSize: 10.5, fontWeight: 700, color: col }}>{a.verdict} · {a.fund.category} · risk-adj {a.fund.riskAdjusted ?? '—'}</span>
                      </div>
                      <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginTop: 4 }}>{a.reason}</div>
                      {a.suggestion && <div style={{ marginTop: 5, fontSize: 11.5, color: 'var(--nd-text-2)', background: 'var(--nd-surface)', borderRadius: 6, padding: '6px 9px' }}>↪ Switch to <strong style={{ color: 'var(--nd-green)' }}>{a.suggestion.name}</strong> (1Y {a.suggestion['1y']}%, risk-adj {a.suggestion.riskAdjusted})</div>}
                    </div>
                  );
                })}
              </>
            )}
          </div>
        )}

        {tab === 'screener' && (
          <div style={{ padding: '16px 20px' }}>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
              {cats.map(c => (
                <button key={c} onClick={() => { setCat(c); runScreener(c, sortBy); }} style={{
                  padding: '5px 11px', fontSize: 12, borderRadius: 7, cursor: 'pointer',
                  border: `1px solid ${cat === c ? 'var(--nd-green)' : 'var(--nd-border)'}`,
                  background: cat === c ? 'var(--nd-green)' : 'transparent', color: cat === c ? '#fff' : 'var(--nd-text-2)',
                }}>{c}</button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 14, fontSize: 12 }}>
              <span style={{ color: 'var(--nd-text-3)' }}>Rank by:</span>
              {([['return', '1Y Return'], ['risk', 'Risk-adjusted']] as [typeof sortBy, string][]).map(([s, label]) => (
                <button key={s} onClick={() => { setSortBy(s); runScreener(cat, s); }} style={{
                  padding: '4px 10px', fontSize: 11.5, borderRadius: 6, cursor: 'pointer',
                  border: `1px solid ${sortBy === s ? 'var(--nd-blue)' : 'var(--nd-border)'}`,
                  background: sortBy === s ? 'var(--nd-blue)' : 'transparent', color: sortBy === s ? '#fff' : 'var(--nd-text-2)',
                }}>{label}</button>
              ))}
            </div>
            {loadingScreen ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Scanning {cat} funds…</div>
            ) : !screen?.funds?.length ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>No funds found for this category.</div>
            ) : (
              <div style={{ overflowX: 'auto', width: '100%' }}>
                <table style={{ minWidth: 760, width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                  <thead><tr style={{ color: 'var(--nd-text-3)', fontSize: 11 }}>
                    {[['#', 'left'], ['Fund', 'left'], ['NAV', 'right'], ['1M', 'right'], ['3M', 'right'], ['6M', 'right'], ['1Y', 'right'], ['3Y', 'right'], ['Vol', 'right'], ['Risk-adj', 'right']].map(([h, a]) => (
                      <th key={h as string} style={{ textAlign: a as any, padding: '6px 10px', whiteSpace: 'nowrap', fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {screen.funds.map((f: any) => (
                      <tr key={f.schemeCode} style={{ borderTop: '1px solid var(--nd-border)', background: f.aiPick ? 'rgba(34,197,94,0.12)' : 'transparent', boxShadow: f.aiPick ? 'inset 3px 0 0 #22c55e' : 'none' }}>
                        <td style={{ padding: '8px 10px', color: 'var(--nd-text-3)', whiteSpace: 'nowrap' }}>{f.rank}{f.aiPick ? ' ⭐' : ''}</td>
                        <td style={{ padding: '8px 10px', maxWidth: 260 }}>
                          <div style={{ fontWeight: 600, color: 'var(--nd-text-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={f.name}>{f.name}</div>
                          <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.fundHouse}</div>
                        </td>
                        <td style={{ textAlign: 'right', padding: '8px 10px', whiteSpace: 'nowrap' }}>₹{f.nav}</td>
                        <ReturnCells f={f} />
                        <td style={{ textAlign: 'right', padding: '8px 10px', whiteSpace: 'nowrap', color: 'var(--nd-text-2)' }}>{f.vol != null ? `${f.vol.toFixed(0)}%` : '—'}</td>
                        <td style={{ textAlign: 'right', padding: '8px 10px', whiteSpace: 'nowrap', fontWeight: 700, color: pctColor(f.riskAdjusted) }}>{f.riskAdjusted != null ? f.riskAdjusted.toFixed(2) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginTop: 8 }}>Ranked by 1-year return · ⭐ = AI top-pick · returns ≥1Y are annualised (CAGR).</div>
              </div>
            )}
          </div>
        )}

        {tab === 'all' && (() => {
          const cols: [string, string][] = [
            ['name', 'Fund'], ['nav', 'NAV'], ['1m', '1M'], ['3m', '3M'], ['6m', '6M'],
            ['1y', '1Y'], ['3y', '3Y'], ['vol', 'Vol'], ['riskAdjusted', 'Risk-adj'],
          ];
          const funds: any[] = (allData?.funds ?? []).slice();
          const { key, dir } = allSort;
          funds.sort((a, b) => {
            const va = a[key], vb = b[key];
            if (va == null && vb == null) return 0;
            if (va == null) return 1; if (vb == null) return -1;     // nulls last
            const cmp = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
            return dir === 'asc' ? cmp : -cmp;
          });
          const toggle = (k: string) => setAllSort(s => s.key === k ? { key: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: k, dir: k === 'name' ? 'asc' : 'desc' });
          const pages = allData?.pages ?? 1; const cur = allData?.page ?? 1;
          const go = (p: number) => { loadAll(allQ, p); };
          const pageNums = Array.from({ length: pages }, (_, i) => i + 1).filter(p => p === 1 || p === pages || Math.abs(p - cur) <= 2);
          return (
            <div style={{ padding: '16px 20px' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                <input className="nd-input" style={{ flex: '1 1 280px' }} placeholder="Search all mutual funds…" value={allQ} onChange={e => setAllQ(e.target.value)} />
                <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>{allData ? `${allData.total.toLocaleString('en-IN')} funds · click any column to sort` : ''}</span>
              </div>
              {allLoading ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Loading funds…</div>
              ) : !funds.length ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>No funds match your search.</div>
              ) : (
                <>
                  <div style={{ overflowX: 'auto', width: '100%' }}>
                    <table style={{ minWidth: 760, width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                      <thead><tr style={{ color: 'var(--nd-text-3)', fontSize: 11 }}>
                        {cols.map(([k, label]) => (
                          <th key={k} onClick={() => toggle(k)} style={{ textAlign: k === 'name' ? 'left' : 'right', padding: '6px 10px', whiteSpace: 'nowrap', cursor: 'pointer', fontWeight: 600, color: allSort.key === k ? 'var(--nd-green)' : undefined }}>
                            {label}{allSort.key === k ? (allSort.dir === 'asc' ? ' ▲' : ' ▼') : ' ⇅'}
                          </th>
                        ))}
                      </tr></thead>
                      <tbody>
                        {funds.map((f: any) => (
                          <tr key={f.schemeCode} style={{ borderTop: '1px solid var(--nd-border)' }}>
                            <td style={{ padding: '8px 10px', maxWidth: 280 }}>
                              <div style={{ fontWeight: 600, color: 'var(--nd-text-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={f.name}>{f.name}</div>
                              <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.category}</div>
                            </td>
                            <td style={{ textAlign: 'right', padding: '8px 10px', whiteSpace: 'nowrap' }}>{f.nav != null ? `₹${f.nav}` : '—'}</td>
                            <ReturnCells f={f} />
                            <td style={{ textAlign: 'right', padding: '8px 10px', whiteSpace: 'nowrap', color: 'var(--nd-text-2)' }}>{f.vol != null ? `${f.vol.toFixed(0)}%` : '—'}</td>
                            <td style={{ textAlign: 'right', padding: '8px 10px', whiteSpace: 'nowrap', fontWeight: 700, color: pctColor(f.riskAdjusted) }}>{f.riskAdjusted != null ? f.riskAdjusted.toFixed(2) : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {/* pagination */}
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'center', marginTop: 14, flexWrap: 'wrap' }}>
                    <button disabled={cur <= 1} onClick={() => go(cur - 1)} style={{ padding: '5px 12px', borderRadius: 7, border: '1px solid var(--nd-border)', background: 'transparent', color: cur <= 1 ? 'var(--nd-text-3)' : 'var(--nd-text-1)', cursor: cur <= 1 ? 'default' : 'pointer', fontSize: 12 }}>← Prev</button>
                    {pageNums.map((p, i) => (
                      <React.Fragment key={p}>
                        {i > 0 && p - pageNums[i - 1] > 1 && <span style={{ color: 'var(--nd-text-3)' }}>…</span>}
                        <button onClick={() => go(p)} style={{ minWidth: 30, padding: '5px 9px', borderRadius: 7, border: `1px solid ${p === cur ? 'var(--nd-green)' : 'var(--nd-border)'}`, background: p === cur ? 'var(--nd-green)' : 'transparent', color: p === cur ? '#fff' : 'var(--nd-text-2)', cursor: 'pointer', fontSize: 12 }}>{p}</button>
                      </React.Fragment>
                    ))}
                    <button disabled={cur >= pages} onClick={() => go(cur + 1)} style={{ padding: '5px 12px', borderRadius: 7, border: '1px solid var(--nd-border)', background: 'transparent', color: cur >= pages ? 'var(--nd-text-3)' : 'var(--nd-text-1)', cursor: cur >= pages ? 'default' : 'pointer', fontSize: 12 }}>Next →</button>
                  </div>
                  <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', textAlign: 'center', marginTop: 6 }}>Page {cur} of {pages} · sorting applies to this page · returns ≥1Y are annualised (CAGR)</div>
                </>
              )}
            </div>
          );
        })()}
      </div>
    </div>
  );
};

export default MutualFunds;
