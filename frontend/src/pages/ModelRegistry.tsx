import React, { useEffect, useState } from 'react';

// Relative URL → routes through nginx to the backend MLflow proxy.
// Works on localhost and through ngrok (never hardcode the host).
const MLFLOW_BASE = '/api/mlflow';

interface ModelVersion {
  name: string;
  version: string;
  status: string;
  creation_timestamp: number;
  last_updated_timestamp: number;
  run_id: string;
}

interface RegisteredModel {
  name: string;
  latest_versions: ModelVersion[];
  description?: string;
}

interface RunMetrics {
  accuracy?: number;
  sharpe_ratio?: number;
  train_samples?: number;
}

const ModelRegistry: React.FC = () => {
  const [models, setModels] = useState<RegisteredModel[]>([]);
  const [metrics, setMetrics] = useState<Record<string, RunMetrics>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${MLFLOW_BASE}/registered-models/search?max_results=100`);
        if (!res.ok) throw new Error(`MLflow returned ${res.status}`);
        const data = await res.json();
        const registered: RegisteredModel[] = data.registered_models ?? [];
        setModels(registered);

        const runIds = registered.flatMap(m => m.latest_versions.map(v => v.run_id)).filter(Boolean);
        const metricMap: Record<string, RunMetrics> = {};
        await Promise.all(
          runIds.map(async runId => {
            try {
              const r = await fetch(`${MLFLOW_BASE}/runs/get?run_id=${runId}`);
              if (!r.ok) return;
              const d = await r.json();
              const mets = d.run?.data?.metrics ?? [];
              const obj: RunMetrics = {};
              for (const m of mets) {
                if (m.key === 'accuracy') obj.accuracy = m.value;
                if (m.key === 'sharpe_ratio') obj.sharpe_ratio = m.value;
                if (m.key === 'train_samples') obj.train_samples = m.value;
              }
              metricMap[runId] = obj;
            } catch {}
          })
        );
        setMetrics(metricMap);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const ts = (ms: number) => new Date(ms).toLocaleString();

  if (loading) return (
    <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-text-3)' }}>Loading MLflow model registry...</div>
  );

  if (error) return (
    <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-red)' }}>
      Failed to connect to MLflow: {error}
      <div style={{ marginTop: 8, fontSize: 13, color: 'var(--nd-text-3)' }}>
        Make sure MLflow is running on port 5000
      </div>
    </div>
  );

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1100, margin: '0 auto' }}>
      <h2 style={{ margin: '0 0 8px', fontSize: 22, fontWeight: 700, color: 'var(--nd-text-1)' }}>
        Model Registry
      </h2>
      <p style={{ margin: '0 0 24px', color: 'var(--nd-text-3)', fontSize: 13 }}>
        Registered models in MLflow — {models.length} model{models.length !== 1 ? 's' : ''} found
      </p>

      {models.length === 0 && (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 40, textAlign: 'center', color: 'var(--nd-text-3)' }}>
          No models registered yet. Training runs will appear here after model-trainer completes a run.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {models.map(model => (
          <div key={model.name} style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 20 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
              <div>
                <h3 style={{ margin: '0 0 4px', fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>
                  {model.name}
                </h3>
                <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
                  {model.latest_versions.length} version{model.latest_versions.length !== 1 ? 's' : ''} registered
                </span>
              </div>
              <a
                href={`http://localhost:5000/#/models/${model.name}`}
                target="_blank"
                rel="noreferrer"
                style={{ fontSize: 12, color: 'var(--nd-accent)', textDecoration: 'none' }}
              >
                Open in MLflow →
              </a>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {model.latest_versions.map(ver => {
                const mets = metrics[ver.run_id] ?? {};
                return (
                  <div key={ver.version} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '12px 16px', display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'center' }}>
                    <div>
                      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 2 }}>Version</div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>v{ver.version}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 2 }}>Status</div>
                      <span style={{
                        fontSize: 12,
                        fontWeight: 500,
                        padding: '2px 8px',
                        borderRadius: 4,
                        background: ver.status === 'READY' ? 'var(--nd-green-10)' : 'var(--nd-surface)',
                        color: ver.status === 'READY' ? 'var(--nd-green)' : 'var(--nd-text-2)',
                      }}>
                        {ver.status}
                      </span>
                    </div>
                    {mets.accuracy != null && (
                      <div>
                        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 2 }}>Accuracy</div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: mets.accuracy >= 0.52 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                          {(mets.accuracy * 100).toFixed(1)}%
                        </div>
                      </div>
                    )}
                    {mets.sharpe_ratio != null && (
                      <div>
                        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 2 }}>Sharpe</div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: mets.sharpe_ratio >= 1.0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                          {mets.sharpe_ratio.toFixed(2)}
                        </div>
                      </div>
                    )}
                    {mets.train_samples != null && (
                      <div>
                        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 2 }}>Train Samples</div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>
                          {mets.train_samples.toLocaleString()}
                        </div>
                      </div>
                    )}
                    <div style={{ marginLeft: 'auto' }}>
                      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 2 }}>Registered</div>
                      <div style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>{ts(ver.creation_timestamp)}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ModelRegistry;
