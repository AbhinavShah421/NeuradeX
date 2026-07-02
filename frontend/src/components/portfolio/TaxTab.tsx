import React from 'react';
import { inr } from './shared';

interface TaxTabProps {
  tax: any;
}

const TaxTab: React.FC<TaxTabProps> = ({ tax }) => {
  return (
    <div style={{ padding: '18px 20px' }}>
      {!tax ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Analysing capital gains…</div>
      ) : tax.note ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>{tax.note}</div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
            {[
              ['Unrealised gains', `₹${inr(tax.unrealisedGains)}`, 'var(--nd-green)'],
              ['Harvestable losses', `₹${inr(tax.harvestableLosses)}`, 'var(--nd-red)'],
              ['Potential offset', `₹${inr(tax.potentialOffset)}`, 'var(--nd-text-1)'],
              ['Est. tax saved', `₹${inr(tax.estTaxSaved)}`, 'var(--nd-green)'],
            ].map(([l, v, c], i) => (
              <div key={i} className="nd-card" style={{ padding: '12px 16px' }}><div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{l}</div><div style={{ fontSize: 15, fontWeight: 700, color: c as string }}>{v}</div></div>
            ))}
          </div>
          {tax.tips.map((t: string, i: number) => <div key={i} style={{ fontSize: 12, color: 'var(--nd-text-2)', padding: '3px 0' }}>💡 {t}</div>)}
          {tax.harvestCandidates?.length > 0 && (
            <div className="nd-card" style={{ padding: '12px 16px', marginTop: 12 }}>
              <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 6 }}>Loss-harvest candidates</div>
              {tax.harvestCandidates.map((c: any) => (
                <div key={c.symbol} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, padding: '5px 0', borderBottom: '1px solid var(--nd-border)' }}>
                  <span style={{ fontWeight: 600 }}>{c.symbol}</span>
                  <span style={{ color: 'var(--nd-red)' }}>₹{inr(c.gain)} ({c.gainPct}%)</span>
                </div>
              ))}
            </div>
          )}
          <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginTop: 12 }}>{tax.caveat}</div>
        </>
      )}
    </div>
  );
};

export default TaxTab;
