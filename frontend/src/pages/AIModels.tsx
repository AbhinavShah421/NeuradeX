import React, { useEffect, useState } from 'react';
import apiService from '../services/api';

interface ModelRow {
  name: string;
  label: string;
  kind: string;
  desc: string;
  enabled: boolean;
  weight: number | null;
  trained?: boolean;
  meta?: any;
}

const KIND_COLOR: Record<string, string> = {
  rule: '#64748b', data: '#0ea5e9', learned: '#a855f7', model: '#22c55e',
};

const AIModels: React.FC = () => {
  const [models, setModels] = useState<ModelRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [training, setTraining] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = async () => {
    try {
      const r: any = await apiService.aiModels();
      setModels(r.data?.models ?? []);
    } catch { /* ignore */ } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const toggle = async (m: ModelRow) => {
    setBusy(m.name);
    try {
      await apiService.setAiModel(m.name, { enabled: !m.enabled });
      setModels(prev => prev.map(x => x.name === m.name ? { ...x, enabled: !x.enabled } : x));
    } catch { setMsg({ ok: false, text: `Could not update ${m.label}` }); }
    finally { setBusy(null); }
  };

  const setWeight = async (m: ModelRow, weight: number | null) => {
    setBusy(m.name);
    try {
      if (weight === null) await apiService.setAiModel(m.name, { clearWeight: true });
      else await apiService.setAiModel(m.name, { weight });
      setModels(prev => prev.map(x => x.name === m.name ? { ...x, weight } : x));
    } catch { setMsg({ ok: false, text: `Could not set weight for ${m.label}` }); }
    finally { setBusy(null); }
  };

  const trainGbm = async () => {
    setTraining(true); setMsg(null);
    try {
      const r: any = await apiService.trainGbm(250);
      const d = r.data || {};
      if (d.status === 'ok') {
        setMsg({ ok: true, text: `GBM trained on ${d.samples} samples — holdout accuracy ${(d.accuracy * 100).toFixed(1)}%, AUC ${d.auc ?? '—'}.` });
        load();
      } else {
        setMsg({ ok: false, text: `Training: ${d.status} (${d.samples ?? 0} samples).` });
      }
    } catch { setMsg({ ok: false, text: 'GBM training failed.' }); }
    finally { setTraining(false); }
  };

  if (loading) return <div style={{ padding: 24, color: 'var(--nd-text-3)' }}>Loading models…</div>;

  const gbm = models.find(m => m.name === 'gbm');

  return (
    <div style={{ paddingBottom: 40 }}>
      <div style={{ fontSize: 12.5, color: 'var(--nd-text-3)', marginBottom: 16, maxWidth: 760 }}>
        Each model votes independently in the ensemble. Toggle one off to remove its voice, or pin a manual
        vote-weight (clear it to fall back to the learned/default weight). Risk models (volatility, anomaly)
        and the regime filter also gate or reweight the others.
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 340px), 1fr))', gap: 12 }}>
        {models.map(m => (
          <div key={m.name} className="nd-card" style={{ padding: '14px 16px', minWidth: 0,
            opacity: m.enabled ? 1 : 0.6, borderLeft: `3px solid ${KIND_COLOR[m.kind] || '#64748b'}` }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>{m.label}</div>
                <span style={{ fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4,
                  color: KIND_COLOR[m.kind] || '#64748b' }}>{m.kind}</span>
                {m.name === 'gbm' && (
                  <span style={{ fontSize: 9.5, fontWeight: 700, marginLeft: 6, padding: '1px 6px', borderRadius: 4,
                    background: m.trained ? 'rgba(34,197,94,0.14)' : 'rgba(239,68,68,0.14)',
                    color: m.trained ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                    {m.trained ? 'trained' : 'not trained'}
                  </span>
                )}
              </div>
              {/* toggle */}
              <button onClick={() => toggle(m)} disabled={busy === m.name}
                style={{ width: 42, height: 24, borderRadius: 12, border: 'none', cursor: 'pointer', flexShrink: 0,
                  background: m.enabled ? 'var(--nd-green)' : 'var(--nd-surface-3, #444)', position: 'relative', transition: 'all .15s' }}>
                <span style={{ position: 'absolute', top: 3, left: m.enabled ? 21 : 3, width: 18, height: 18,
                  borderRadius: '50%', background: '#fff', transition: 'all .15s' }} />
              </button>
            </div>
            <div style={{ fontSize: 11, color: 'var(--nd-text-3)', margin: '6px 0 10px', minHeight: 28 }}>{m.desc}</div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--nd-text-2)' }}>Weight</span>
              <input type="range" min={0} max={2} step={0.1}
                value={m.weight ?? 1.0} disabled={!m.enabled || busy === m.name}
                onChange={e => setWeight(m, parseFloat(e.target.value))}
                style={{ flex: 1 }} />
              <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)', minWidth: 50 }}>
                {m.weight === null ? 'auto' : m.weight.toFixed(1)}
              </span>
              {m.weight !== null && (
                <button onClick={() => setWeight(m, null)} disabled={busy === m.name}
                  style={{ background: 'none', border: 'none', color: 'var(--nd-blue)', cursor: 'pointer', fontSize: 10.5, padding: 0 }}>
                  auto
                </button>
              )}
            </div>

            {m.name === 'gbm' && m.meta?.trainedAt && (
              <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 8 }}>
                {m.meta.samples} samples · acc {m.meta.accuracy != null ? `${(m.meta.accuracy * 100).toFixed(1)}%` : '—'} · AUC {m.meta.auc ?? '—'} · {String(m.meta.trainedAt).replace('T', ' ')}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* GBM trainer */}
      <div className="nd-card" style={{ padding: '16px 18px', marginTop: 16 }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 4 }}>Train the Gradient-Boosted P(up) model</div>
        <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', marginBottom: 12, maxWidth: 700 }}>
          Trains the non-linear classifier on backfill (pattern fingerprint → realised forward return) samples,
          rotating through a slice of the universe each run. It also <strong>auto-retrains nightly (~03:00 IST)</strong>,
          advancing through the whole universe over successive nights. {gbm?.trained ? 'Train now to refresh on demand.' : 'It abstains from voting until trained.'}
        </div>
        <button onClick={trainGbm} disabled={training}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 16px', borderRadius: 9, border: 'none',
            background: 'var(--nd-purple)', color: '#fff', fontWeight: 700, fontSize: 13, cursor: training ? 'wait' : 'pointer' }}>
          <span className={`material-icons${training ? ' nd-spin' : ''}`} style={{ fontSize: 17 }}>{training ? 'autorenew' : 'model_training'}</span>
          {training ? 'Training… (up to a few minutes)' : 'Train GBM (250-symbol slice)'}
        </button>
      </div>

      {msg && (
        <div onClick={() => setMsg(null)} style={{ marginTop: 14, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10,
          background: msg.ok ? 'var(--nd-green-50)' : 'var(--nd-red-50)',
          border: `1px solid ${msg.ok ? '#22c55e55' : '#ef444455'}`, borderRadius: 10, padding: '12px 16px' }}>
          <span className="material-icons" style={{ color: msg.ok ? 'var(--nd-green)' : 'var(--nd-red)', fontSize: 20 }}>
            {msg.ok ? 'check_circle' : 'error_outline'}
          </span>
          <span style={{ fontSize: 13, color: msg.ok ? '#065f46' : 'var(--nd-red)' }}>{msg.text}</span>
        </div>
      )}
    </div>
  );
};

export default AIModels;
