import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../services/api';

type Tab = 'holdings' | 'screener';

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
        <div style={{ display: 'flex', borderBottom: '1px solid var(--nd-border)', padding: '0 16px' }}>
          {([['holdings', 'My Funds'], ['screener', 'Screener']] as [Tab, string][]).map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} style={{
              padding: '12px 16px', border: 'none', background: 'none', cursor: 'pointer', fontSize: 13,
              fontWeight: tab === id ? 700 : 500, color: tab === id ? 'var(--nd-green)' : 'var(--nd-text-2)',
              borderBottom: tab === id ? '2px solid var(--nd-green)' : '2px solid transparent', marginBottom: -1,
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
                        <td style={{ textAlign: 'right', padding: '8px 10px' }}><button onClick={() => removeFund(f.schemeCode)} style={{ background: 'none', border: 'none', color: 'var(--nd-red)', cursor: 'pointer', fontSize: 16 }}>×</button></td>
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
                      <tr key={f.schemeCode} style={{ borderTop: '1px solid var(--nd-border)', background: f.aiPick ? 'var(--nd-green-50)' : 'transparent' }}>
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
      </div>
    </div>
  );
};

export default MutualFunds;
