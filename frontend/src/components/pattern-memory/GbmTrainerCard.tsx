import React from 'react';
import { ModelRow } from './shared';

interface GbmTrainerCardProps {
  models: ModelRow[];
  training: boolean;
  trainMsg: { ok: boolean; text: string } | null;
  trainGbm: () => void;
}

const GbmTrainerCard: React.FC<GbmTrainerCardProps> = ({ models, training, trainMsg, trainGbm }) => {
  if (!models.some(m => m.name === 'gbm')) return null;

  return (
    <div className="nd-pm-card" style={{ borderLeft: '3px solid #22c55e' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span className="material-icons" style={{ fontSize: 18, color: '#22c55e' }}>account_tree</span>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>GBM Trainer</h3>
        {models.find(m => m.name === 'gbm')?.trained && (
          <span style={{
            fontSize: 10, fontWeight: 600, color: '#22c55e',
            background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)',
            borderRadius: 20, padding: '2px 10px',
          }}>Trained</span>
        )}
      </div>
      <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
        Train the Gradient Boosting Classifier on your latest memory bank data. Requires ≥250 labelled cases.
        Runs in the background (~30 s) — accuracy and AUC will update once complete.
      </p>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <button
          className="nd-btn nd-btn-primary"
          onClick={trainGbm}
          disabled={training}
          style={{ borderRadius: 9, padding: '9px 18px', fontSize: 13, fontWeight: 600, gap: 7, background: '#22c55e', borderColor: '#22c55e' }}
        >
          <span className="material-icons" style={{ fontSize: 15, animation: training ? 'nd-spin 0.9s linear infinite' : 'none' }}>
            {training ? 'autorenew' : 'play_arrow'}
          </span>
          {training ? 'Training…' : 'Train GBM'}
        </button>
        {trainMsg && (
          <span style={{ fontSize: 12, color: trainMsg.ok ? 'var(--nd-green)' : '#e74c3c', lineHeight: 1.5 }}>
            {trainMsg.text}
          </span>
        )}
      </div>
    </div>
  );
};

export default GbmTrainerCard;
