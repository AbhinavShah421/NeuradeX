import React, { useState } from 'react';
import { usePatternMemoryData } from '../hooks/usePatternMemoryData';
import HeadlineStats from '../components/pattern-memory/HeadlineStats';
import DatasetCard from '../components/pattern-memory/DatasetCard';
import AgentLearningCard from '../components/pattern-memory/AgentLearningCard';
import RefreshControl from '../components/pattern-memory/RefreshControl';
import BreakdownPanel from '../components/pattern-memory/BreakdownPanel';
import GbmTrainerCard from '../components/pattern-memory/GbmTrainerCard';
import AgentDetailSheet from '../components/pattern-memory/AgentDetailSheet';
import { LearningAgent } from '../components/pattern-memory/shared';

const PatternMemory: React.FC = () => {
  const {
    stats, loading, seeding, seedMsg, sweeping, lastSweep, learning,
    models, modelBusy, modelError, setModelError, training, trainMsg, dataset,
    total, overallWin, sortedAgents,
    loadDataset, toggleModel, setWeightOverride, trainGbm,
    runSweep, runSeed,
  } = usePatternMemoryData();

  const [agentPopup, setAgentPopup] = useState<{ agent: LearningAgent; rank: number } | null>(null);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, paddingBottom: 8 }}>

      {/* ═══ 1. INTRO BANNER ════════════════════════════════════════════ */}
      <div className="nd-pm-card" style={{
        background: 'linear-gradient(135deg, rgba(0,179,134,0.08) 0%, var(--nd-surface) 55%)',
        borderColor: 'rgba(0,179,134,0.22)',
        display: 'flex', gap: 14, alignItems: 'flex-start',
      }}>
        <div style={{
          width: 40, height: 40, borderRadius: 11, flexShrink: 0,
          background: 'rgba(0,179,134,0.15)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <span className="material-icons" style={{ color: 'var(--nd-green)', fontSize: 22 }}>memory</span>
        </div>
        <div>
          <h2 style={{ margin: '0 0 6px', fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>
            Pattern Memory Bank
          </h2>
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.65, color: 'var(--nd-text-2)' }}>
            Every backtest, paper trade and live decision is fingerprinted and stored with
            its real outcome. When a new situation appears the engine retrieves similar past
            cases and only acts when their track record supports it.{' '}
            <span style={{ color: 'var(--nd-text-1)', fontWeight: 500 }}>
              The more the bank learns, the more selective and accurate it becomes.
            </span>
          </p>
        </div>
      </div>

      {/* ═══ 2. HEADLINE STATS ══════════════════════════════════════════ */}
      <HeadlineStats loading={loading} total={total} overallWin={overallWin} symbolsCount={stats?.topSymbols.length ?? 0} />

      {/* ═══ 2b. 1-SECOND DATASET ═══════════════════════════════════════ */}
      <DatasetCard dataset={dataset} loadDataset={loadDataset} />

      {/* ═══ 3. AGENT LEARNING ══════════════════════════════════════════ */}
      <AgentLearningCard
        learning={learning} models={models} modelBusy={modelBusy} modelError={modelError} setModelError={setModelError}
        toggleModel={toggleModel} setWeightOverride={setWeightOverride} sortedAgents={sortedAgents}
        onSelectAgent={(agent, rank) => setAgentPopup({ agent, rank })}
      />

      {/* ═══ 4. REFRESH CONTROL ═════════════════════════════════════════ */}
      <RefreshControl sweeping={sweeping} lastSweep={lastSweep} runSweep={runSweep} seeding={seeding} seedMsg={seedMsg} runSeed={runSeed} />

      {/* ═══ 5. BREAKDOWN ═══════════════════════════════════════════════ */}
      <BreakdownPanel stats={stats} />

      {/* ═══ 6. GBM TRAINER ════════════════════════════════════════════ */}
      <GbmTrainerCard models={models} training={training} trainMsg={trainMsg} trainGbm={trainGbm} />

      {/* ═══ AGENT DETAIL POPUP ═════════════════════════════════════════ */}
      {agentPopup && (
        <AgentDetailSheet agent={agentPopup.agent} rank={agentPopup.rank} onClose={() => setAgentPopup(null)} />
      )}
    </div>
  );
};

export default PatternMemory;
