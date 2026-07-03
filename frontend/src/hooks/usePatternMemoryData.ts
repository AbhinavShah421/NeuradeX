import { useCallback, useEffect, useState } from 'react';
import apiService from '../services/api';
import { MemStats, ModelRow, LearningAgent, LearningSummary } from '../components/pattern-memory/shared';
import { getErrorMessage } from '../utils/errors';

// Data-fetching / polling / mutation logic for the PatternMemory page.
// Returns the same state shape the page component used to manage directly.
export function usePatternMemoryData() {
  const [stats,     setStats]     = useState<MemStats | null>(null);
  const [loading,       setLoading]       = useState(true);
  const [seeding,       setSeeding]       = useState(false);
  const [seedMsg,       setSeedMsg]       = useState('');
  const [sweeping,      setSweeping]      = useState(false);
  const [lastSweep,     setLastSweep]     = useState<any>(null);
  const [learning,      setLearning]      = useState<LearningSummary | null>(null);
  const [models,        setModels]        = useState<ModelRow[]>([]);
  const [modelBusy,     setModelBusy]     = useState<string | null>(null);
  const [training,      setTraining]      = useState(false);
  const [trainMsg,      setTrainMsg]      = useState<{ ok: boolean; text: string } | null>(null);
  const [dataset,       setDataset]       = useState<{ summary: any; rows: any[] } | null>(null);
  const [modelError, setModelError] = useState<string | null>(null);

  const loadDataset = useCallback(async () => {
    try {
      const r: any = await apiService.candleCoverage();
      setDataset({ summary: r.summary ?? {}, rows: r.data ?? [] });
    } catch {}
  }, []);

  const loadModels = useCallback(async () => {
    try { const r: any = await apiService.aiModels(); setModels(r.data?.models ?? []); } catch {}
  }, []);

  const toggleModel = async (m: ModelRow) => {
    const next = !m.enabled;
    setModels(prev => prev.map(x => x.name === m.name ? { ...x, enabled: next } : x));
    setModelBusy(m.name);
    setModelError(null);
    try {
      await apiService.setAiModel(m.name, { enabled: next });
      await loadModels();
    } catch (err: unknown) {
      setModels(prev => prev.map(x => x.name === m.name ? { ...x, enabled: m.enabled } : x));
      setModelError(`Failed to ${next ? 'enable' : 'disable'} ${m.name}: ${getErrorMessage(err, 'unknown error')}`);
    } finally { setModelBusy(null); }
  };

  const setWeightOverride = async (m: ModelRow, weight: number | null) => {
    setModels(prev => prev.map(x => x.name === m.name ? { ...x, weight } : x));
    setModelBusy(m.name);
    setModelError(null);
    try {
      if (weight === null) await apiService.setAiModel(m.name, { clearWeight: true });
      else await apiService.setAiModel(m.name, { weight });
      await loadModels(); // re-read true server state, not just optimistic guess
    } catch (err: unknown) {
      setModels(prev => prev.map(x => x.name === m.name ? { ...x, weight: m.weight } : x));
      setModelError(`Failed to set weight for ${m.name}: ${getErrorMessage(err, 'unknown error')}`);
    } finally { setModelBusy(null); }
  };

  const trainGbm = async () => {
    setTraining(true); setTrainMsg(null);
    try {
      const r: any = await apiService.trainGbm(250);
      const d = r.data || {};
      if (d.status === 'ok') {
        setTrainMsg({ ok: true, text: `GBM trained on ${d.samples} samples — accuracy ${(d.accuracy * 100).toFixed(1)}%, AUC ${d.auc ?? '—'}.` });
        loadModels();
      } else { setTrainMsg({ ok: false, text: `Training: ${d.status} (${d.samples ?? 0} samples).` }); }
    } catch { setTrainMsg({ ok: false, text: 'GBM training failed.' }); }
    finally { setTraining(false); }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.memoryStats();
      // Backend sometimes returns the raw stats object instead of the {data} envelope.
      const typed = res as { data?: MemStats };
      setStats(typed.data ?? (res as unknown as MemStats));
    } catch { setStats(null); }
    finally { setLoading(false); }
    try {
      const ls = await apiService.learningSummary();
      setLearning((ls.data as LearningSummary) ?? null);
    } catch { /* ignore */ }
  }, []);

  const loadSweep = useCallback(async () => {
    try {
      const s = await apiService.memorySweepStatus();
      setLastSweep(s.last ?? null);
      return !!s.running;
    } catch { return false; }
  }, []);

  useEffect(() => { load(); loadSweep(); loadModels(); loadDataset(); }, [load, loadSweep, loadModels, loadDataset]);

  const runSweep = async () => {
    setSweeping(true);
    try {
      await apiService.memorySweep();
      const poll = setInterval(async () => {
        const running = await loadSweep();
        if (!running) { clearInterval(poll); setSweeping(false); await load(); }
      }, 4000);
    } catch { setSweeping(false); }
  };

  const runSeed = async () => {
    setSeeding(true);
    setSeedMsg('Replaying historical candles — this can take a minute…');
    try {
      const res = await apiService.memorySeed({ lookback_days: 365, horizon: 3, stride: 1 });
      const typed = res as { data?: { totalInserted?: number; symbolsProcessed?: number } };
      const d = typed.data ?? {};
      setSeedMsg(`✓ Seeded ${d.totalInserted?.toLocaleString() ?? 0} cases across ${d.symbolsProcessed ?? 0} stocks.`);
      await load();
    } catch (e: unknown) {
      setSeedMsg(`✗ Seeding failed: ${getErrorMessage(e)}`);
    } finally { setSeeding(false); }
  };

  const total = stats?.totalCases ?? 0;
  const overallWin =
    stats && stats.byAction.length
      ? stats.byAction.reduce((s, a) => s + a.winRate * a.count, 0) /
        Math.max(1, stats.byAction.reduce((s, a) => s + a.count, 0))
      : 0;

  const sortedAgents: LearningAgent[] = Array.isArray(learning?.agents)
    ? [...learning.agents].sort((a, b) => b.weight - a.weight)
    : [];

  return {
    stats, loading, seeding, seedMsg, sweeping, lastSweep, learning,
    models, modelBusy, modelError, setModelError, training, trainMsg, dataset,
    total, overallWin, sortedAgents,
    loadDataset, loadModels, toggleModel, setWeightOverride, trainGbm,
    load, loadSweep, runSweep, runSeed,
  };
}
