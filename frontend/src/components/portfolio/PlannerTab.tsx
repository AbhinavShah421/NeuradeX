import React from 'react';
import { inr } from './shared';

interface PlanForm {
  goalAmount: string;
  years: string;
  risk: string;
  currentCorpus: string;
  monthly: string;
}

interface PlannerTabProps {
  plan: any;
  planForm: PlanForm;
  setPlanForm: (form: PlanForm) => void;
  planning: boolean;
  runPlan: () => void;
  setQuizOpen: (open: boolean) => void;
}

const PlannerTab: React.FC<PlannerTabProps> = ({ plan, planForm, setPlanForm, planning, runPlan, setQuizOpen }) => {
  return (
    <div style={{ padding: '18px 20px' }}>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 16 }}>
        {[['goalAmount', 'Goal ₹'], ['years', 'Years'], ['currentCorpus', 'Current corpus ₹'], ['monthly', 'Monthly SIP ₹ (optional)']].map(([k, label]) => (
          <div key={k}><div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 3 }}>{label}</div>
            <input className="nd-input" style={{ width: k === 'years' ? 80 : 150 }} value={(planForm as any)[k]}
              onChange={e => setPlanForm({ ...planForm, [k]: e.target.value.replace(/[^0-9]/g, '') })} /></div>
        ))}
        <div><div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 3 }}>Risk</div>
          <select className="nd-input" value={planForm.risk} onChange={e => setPlanForm({ ...planForm, risk: e.target.value })}>
            <option value="conservative">Conservative</option><option value="moderate">Moderate</option><option value="aggressive">Aggressive</option>
          </select></div>
        <button onClick={() => setQuizOpen(true)} style={{ padding: '9px 14px', borderRadius: 8, border: '1px solid var(--nd-blue)', background: 'transparent', color: 'var(--nd-blue)', fontWeight: 600, fontSize: 12, cursor: 'pointer' }}>📋 Find my risk</button>
        <button onClick={runPlan} disabled={planning} style={{ padding: '9px 18px', borderRadius: 8, border: 'none', background: 'var(--nd-green)', color: '#fff', fontWeight: 700, fontSize: 12.5, cursor: 'pointer' }}>{planning ? 'Planning…' : 'Plan'}</button>
      </div>
      {plan && (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
            {[
              ['Required SIP', plan.requiredSip != null ? `₹${inr(plan.requiredSip)}/mo` : `₹${inr(plan.monthlySip)}/mo`],
              ['Projected corpus', `₹${inr(plan.projectedCorpus)}`],
              ['You invest', `₹${inr(plan.invested)}`],
              ['Wealth gained', `₹${inr(plan.wealthGained)}`],
              ['Assumed return', `${plan.assumedReturnPct}% p.a.`],
            ].map(([l, v], i) => (
              <div key={i} className="nd-card" style={{ padding: '12px 16px' }}>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{l}</div>
                <div style={{ fontSize: 15, fontWeight: 700, color: i === 3 ? 'var(--nd-green)' : 'var(--nd-text-1)' }}>{v}</div>
              </div>
            ))}
          </div>
          {plan.goalAmount && (
            <div style={{ fontSize: 12.5, marginBottom: 14, color: plan.onTrack ? 'var(--nd-green)' : '#f59e0b' }}>
              {plan.onTrack ? `✓ On track — projected ₹${inr(plan.projectedCorpus)} meets your ₹${inr(plan.goalAmount)} goal.` : `⚠ Short of goal — increase the SIP or horizon. Range: ₹${inr(plan.pessimistic)}–₹${inr(plan.optimistic)}.`}
            </div>
          )}
          {/* projection chart */}
          {plan.projection?.length > 1 && (() => {
            const pts = plan.projection; const W = 600, H = 140, PL = 8, PR = 8, PT = 10, PB = 18;
            const max = Math.max(...pts.map((p: any) => p.optimistic));
            const sx = (i: number) => PL + i / (pts.length - 1) * (W - PL - PR);
            const sy = (v: number) => PT + (1 - v / max) * (H - PT - PB);
            const line = (key: string) => pts.map((p: any, i: number) => `${sx(i).toFixed(1)},${sy(p[key]).toFixed(1)}`).join(' ');
            return (
              <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
                <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 6 }}>Projected growth ({plan.years} yrs)</div>
                <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 140 }} preserveAspectRatio="none">
                  <polyline points={`${line('optimistic')} ${pts.map((p: any, i: number) => `${sx(pts.length - 1 - i).toFixed(1)},${sy(p.pessimistic ? pts[pts.length - 1 - i].pessimistic : 0).toFixed(1)}`).join(' ')}`} fill="#22c55e15" stroke="none" />
                  <polyline points={line('expected')} fill="none" stroke="#22c55e" strokeWidth="2" />
                  <text x={PL} y={H - 6} fontSize="9" fill="var(--nd-text-3)">Yr 1</text>
                  <text x={W - PR} y={H - 6} fontSize="9" fill="var(--nd-text-3)" textAnchor="end">Yr {plan.years}</text>
                </svg>
              </div>
            );
          })()}
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>AI asset allocation ({plan.risk})</div>
          {plan.sleeves.map((s: any) => (
            <div key={s.sleeve} style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5 }}><span style={{ fontWeight: 600 }}>{s.sleeve} · {s.pct}%</span></div>
              <div style={{ height: 7, background: 'var(--nd-border)', borderRadius: 4, margin: '3px 0' }}><div style={{ height: 7, width: `${s.pct}%`, background: 'var(--nd-blue)', borderRadius: 4 }} /></div>
              <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{s.how}</div>
            </div>
          ))}
          <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginTop: 8 }}>{plan.note}</div>
        </>
      )}
    </div>
  );
};

export default PlannerTab;
