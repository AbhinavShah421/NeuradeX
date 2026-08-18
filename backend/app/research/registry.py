"""The evidence register: what justifies each live trading behaviour.

Pillar B. The validation gate on its own is advisory — a research tool nobody
has to consult. This is what makes it binding.

Every switch that changes live trading behaviour must be declared here with the
evidence that justifies its current setting. `test_registry.py` fails the build
if live code reads a behaviour switch that has no entry, so a new knob cannot be
added quietly and an old one cannot drift out of view.

Enforcement is at build time, not at startup. A runtime check that refused to
boot on missing evidence would take the trading system down over a bookkeeping
gap, which is a worse failure than the one it prevents. Instead the register is
surfaced through `GET /api/monitor/evidence` and the System Map, so an
unjustified behaviour is visible rather than fatal.

Honesty about what the statuses mean
------------------------------------
    PASS          a gate verdict cleared: day-clustered, cost-netted, OOS-checked
    INCONCLUSIVE  measured, but too few days to call — shipped on judgement
    UNMEASURED    running on reasoning alone, never put through the gate
    NEUTRAL       does not affect trade selection (plumbing, logging, capacity)

Most entries are honestly INCONCLUSIVE or UNMEASURED today. That is the point:
the register makes the size of the evidence debt visible instead of letting each
switch look equally well-founded.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

EvidenceStatus = Literal["PASS", "INCONCLUSIVE", "UNMEASURED", "NEUTRAL"]


@dataclass(frozen=True)
class Behaviour:
    """One switch that can change what the system trades."""

    env: str
    read_in: str                       # file:line where live code consults it
    default: str                       # behaviour when the variable is unset
    affects_trades: bool
    status: EvidenceStatus
    evidence: str                      # what was measured, or why nothing was
    measured_on: date | None = None

    @property
    def justified(self) -> bool:
        """A behaviour is justified if it either cleared the gate or cannot
        affect trade selection at all."""
        return self.status in ("PASS", "NEUTRAL")

    def as_dict(self) -> dict:
        return {
            "env": self.env,
            "read_in": self.read_in,
            "default": self.default,
            "affects_trades": self.affects_trades,
            "status": self.status,
            "evidence": self.evidence,
            "measured_on": self.measured_on.isoformat() if self.measured_on else None,
            "justified": self.justified,
        }


BEHAVIOURS: tuple[Behaviour, ...] = (
    Behaviour(
        env="NEURADEX_TREND_FILTER",
        read_in="backend/app/services/sessions_service.py:584",
        default="inverted — blocks buying strength, admits weakness",
        affects_trades=True,
        status="INCONCLUSIVE",
        measured_on=date(2026, 8, 18),
        evidence=(
            "The direction is supported: every 'price is extended' feature is "
            "negative for a long on raw forward returns as well as on cf_pnl_pct "
            "(price_vs_vwap t=-3.53..-2.58 across 5/15/30/60m), and the one "
            "feature with no directional story (atr) is the one that fails, so "
            "the method discriminates. But 31 days is under the 60-day floor, so "
            "the gate returns INCONCLUSIVE and this ships on judgement. "
            "Rollback: NEURADEX_TREND_FILTER=legacy."
        ),
    ),
    Behaviour(
        env="NEURADEX_GRACE_UPSIDE",
        read_in="backend/app/services/backtest_service.py:863",
        default="off — the 10-minute entry grace also suspends target and trail",
        affects_trades=True,
        status="INCONCLUSIVE",
        measured_on=date(2026, 8, 16),
        evidence=(
            "The suspension is a real bug, but the 85k-entry A/B put the fix at "
            "roughly zero edge while truncating the largest runners. Shipped "
            "default-off deliberately. Not re-measured through the gate, so the "
            "A/B's significance is unverified under day clustering."
        ),
    ),
    Behaviour(
        env="ENSEMBLE_VOTE_MODE",
        read_in="backend/app/agents/ensemble.py:130",
        default="directional — 'legacy' restores max-vote",
        affects_trades=True,
        status="UNMEASURED",
        evidence=(
            "No vote-aggregation reshape has ever beaten what ships on held-out "
            "data, so the current setting is not known to be wrong — but it has "
            "never been put through the gate either."
        ),
    ),
    Behaviour(
        env="ENSEMBLE_DIR_DOMINANCE",
        read_in="backend/app/agents/ensemble.py:131",
        default="1.3 — winning side must carry 1.3x the runner-up's mass",
        affects_trades=True,
        status="UNMEASURED",
        evidence="Threshold chosen by reasoning. Never measured against outcomes.",
    ),
    Behaviour(
        env="ENSEMBLE_DIR_MIN_VOTERS",
        read_in="backend/app/agents/ensemble.py:132",
        default="2 — at least two agents must back a directional call",
        affects_trades=True,
        status="UNMEASURED",
        evidence="Threshold chosen by reasoning. Never measured against outcomes.",
    ),
    Behaviour(
        env="AGGREGATE_AGENT_SIGNALS",
        read_in="ensemble-engine/app/main.py:349",
        default="off — the backend ensemble aggregates instead",
        affects_trades=False,
        status="NEUTRAL",
        measured_on=date(2026, 8, 18),
        evidence=(
            "Turned off when the duplicate agent microservices were removed: the "
            "legacy collector could only ever reach 1/5 coverage, scaling "
            "confidence to ~0.07 which every gate rejected. Removing a path that "
            "produced no executable decisions cannot change trade selection."
        ),
    ),
    Behaviour(
        env="ENSEMBLE_PUBLISH_ENABLED",
        read_in="backend/app/utils/decision_publisher.py",
        default="on",
        affects_trades=False,
        status="NEUTRAL",
        evidence=(
            "Bridges backend decisions onto ensemble.raw for the microservice "
            "pipeline. Transport only — it does not alter which decisions the "
            "backend reaches."
        ),
    ),
)


def unjustified() -> tuple[Behaviour, ...]:
    """Behaviours that can move trades without a cleared gate verdict."""
    return tuple(b for b in BEHAVIOURS if b.affects_trades and not b.justified)


def evidence_debt() -> dict:
    """Summary for the monitor: how much live behaviour rests on unproven ground."""
    trade_affecting = [b for b in BEHAVIOURS if b.affects_trades]
    return {
        "total": len(BEHAVIOURS),
        "affects_trades": len(trade_affecting),
        "passed": sum(1 for b in trade_affecting if b.status == "PASS"),
        "inconclusive": sum(1 for b in trade_affecting if b.status == "INCONCLUSIVE"),
        "unmeasured": sum(1 for b in trade_affecting if b.status == "UNMEASURED"),
        "unjustified": [b.env for b in unjustified()],
        "behaviours": [b.as_dict() for b in BEHAVIOURS],
    }
