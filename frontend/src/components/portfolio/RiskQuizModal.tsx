import React from 'react';
import { RISK_QUIZ, riskFromScore } from './shared';

interface PlanForm {
  goalAmount: string;
  years: string;
  risk: string;
  currentCorpus: string;
  monthly: string;
}

interface RiskQuizModalProps {
  quizAns: number[];
  setQuizAns: (fn: (a: number[]) => number[]) => void;
  planForm: PlanForm;
  setPlanForm: (form: PlanForm) => void;
  onClose: () => void;
}

const RiskQuizModal: React.FC<RiskQuizModalProps> = ({ quizAns, setQuizAns, planForm, setPlanForm, onClose }) => {
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 14, width: '100%', maxWidth: 460, maxHeight: '88vh', overflow: 'auto', padding: 22 }}>
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>Find your risk profile</div>
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginBottom: 14 }}>5 quick questions → recommended risk level for your plan.</div>
        {RISK_QUIZ.map((item, qi) => (
          <div key={qi} style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 5 }}>{item.q}</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {item.opts.map(([label, pts]) => (
                <button key={label} onClick={() => setQuizAns(a => a.map((v, i) => i === qi ? pts : v))} style={{
                  padding: '5px 10px', fontSize: 11.5, borderRadius: 7, cursor: 'pointer',
                  border: `1px solid ${quizAns[qi] === pts ? 'var(--nd-green)' : 'var(--nd-border)'}`,
                  background: quizAns[qi] === pts ? 'rgba(34,197,94,0.12)' : 'transparent',
                  color: quizAns[qi] === pts ? 'var(--nd-green)' : 'var(--nd-text-2)',
                }}>{label}</button>
              ))}
            </div>
          </div>
        ))}
        {(() => { const total = quizAns.reduce((s, v) => s + v, 0); const risk = riskFromScore(total);
          return (
            <div style={{ marginTop: 8, padding: '10px 12px', background: 'var(--nd-surface)', borderRadius: 8, fontSize: 12.5 }}>
              Recommended: <strong style={{ color: 'var(--nd-green)', textTransform: 'capitalize' }}>{risk}</strong>
              <span style={{ color: 'var(--nd-text-3)' }}> (score {total}/15)</span>
              <button onClick={() => { setPlanForm({ ...planForm, risk }); onClose(); }} style={{ float: 'right', padding: '6px 14px', borderRadius: 7, border: 'none', background: 'var(--nd-green)', color: '#fff', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>Use this</button>
            </div>
          ); })()}
      </div>
    </div>
  );
};

export default RiskQuizModal;
