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
        default="legacy as of 2026-08-20 — rolled back from inverted",
        affects_trades=True,
        status="INCONCLUSIVE",
        measured_on=date(2026, 8, 20),
        evidence=(
            "Rolled back to legacy after the inversion coincided with a real "
            "day-level equity break: paper P&L went from -Rs.1/day (14 days) to "
            "-Rs.917/day (8 days), Welch t=-2.55 on daily P&L. "
            "The cause was isolated by splitting the same-day baseline (mean cf "
            "over ALL decisions, i.e. what a blind long earns) from selection "
            "(mean cf over our BUYs). Baseline barely moved: -0.194% -> -0.233%, "
            "t=-0.64. Selection edge over baseline collapsed: +0.260%/day -> "
            "-0.043%/day, t=-1.65. The universe did not get worse; the picking "
            "did — which points at the filter and away from the near-total "
            "universe churn (only 5 of 70 traded symbols overlap the break). "
            "Legacy is the only configuration with a measured POSITIVE selection "
            "edge (+0.260%/day). "
            "Caveats: 8v8 days, |t|=1.65 is under the gate's 2.0 bar, so this is "
            "INCONCLUSIVE and ships on judgement — but it is a choice between two "
            "live configurations, not a discovery claim. Note also that the "
            "extension finding that originally motivated the inversion still "
            "stands on raw forward returns; what failed was inferring an ENTRY "
            "RULE from it. A band variant blocking both extremes was designed, "
            "measured and NOT shipped: day-clustered, every candidate threshold "
            "was |t|<1 and the best-looking one flipped sign. "
            "Rollback of this rollback: NEURADEX_TREND_FILTER=inverted."
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
    Behaviour(
        env="NEURADEX_DAILY_LOSS_LIMIT_ABS",
        read_in="backend/app/services/risk_guard.py",
        default="1500 (rupees) — blocks new paper entries once breached",
        affects_trades=True,
        status="UNMEASURED",
        measured_on=date(2026, 8, 19),
        evidence=(
            "A guardrail sized from the measured loss distribution, not from an "
            "edge measurement — hence UNMEASURED rather than INCONCLUSIVE. Paper "
            "equity fell Rs.7,338 over the 8 sessions from 2026-08-10 (mean "
            "-Rs.917/day, worst -Rs.1,947); Rs.1,500 is ~1.6x the mean and ~3 "
            "full stops under 1% risk sizing. It caps the downside tail; it "
            "cannot create edge and is not claimed to. Blocks entries only — "
            "exits keep firing. Rollback: set to 0."
        ),
    ),
    Behaviour(
        env="NEURADEX_DAILY_LOSS_LIMIT_PCT",
        read_in="backend/app/services/risk_guard.py",
        default="5.0 (percent of capital deployed today)",
        affects_trades=True,
        status="UNMEASURED",
        measured_on=date(2026, 8, 19),
        evidence=(
            "Secondary leg, mirroring autopilot's existing 5% threshold. Kept "
            "because deployed capital varies with how many sessions ran, but it "
            "is the weaker leg: at 15 sessions x Rs.50k a Rs.1,600 loss is only "
            "-0.21% and would sleep through. The absolute limit is primary. "
            "Rollback: set to 0."
        ),
    ),
    Behaviour(
        env="NEURADEX_RISK_SIZING",
        read_in="backend/app/services/sessions_service.py:575",
        default="1 — risk-budget sizing; 0 restores flat 95%-of-cash",
        affects_trades=True,
        status="UNMEASURED",
        measured_on=date(2026, 8, 20),
        evidence=(
            "Changes position SIZE, never selection — the same trades are taken, "
            "with the rupee risk held constant instead of the deployed capital. "
            "Shrink-only: bounded by the legacy 95%-of-cash notional, so it can "
            "only reduce a position. Motivated by a measured mechanism rather "
            "than an edge claim: the post-2026-08-10 universe (5 of 70 symbols "
            "overlap with the prior one) moves 0.714% per trade against 0.569% "
            "before, and under flat sizing that 25% larger move lands straight "
            "on P&L. Not put through the validation gate because the gate "
            "measures edge, and this makes no edge claim. Rollback: set to 0."
        ),
    ),
    Behaviour(
        env="NEURADEX_RISK_PCT",
        read_in="backend/app/services/sessions_service.py:576",
        default="0.01 — 1% of session capital risked per trade (~Rs.500 on Rs.50k)",
        affects_trades=True,
        status="UNMEASURED",
        evidence=(
            "Chosen to sit under the Phase-1 daily limit: 1% of Rs.50k is Rs.500, "
            "so roughly three full stops reach the Rs.1,500 daily breaker. Half "
            "the Java risk-engine's 2% (RiskValidatorService), deliberately, "
            "while the book has no demonstrated edge."
        ),
    ),
    Behaviour(
        env="NEURADEX_MAX_POS_PCT",
        read_in="backend/app/services/sessions_service.py:577",
        default="0.95 — notional ceiling, identical to the legacy formula",
        affects_trades=True,
        status="UNMEASURED",
        evidence=(
            "At its default it changes nothing — 0.95 reproduces the legacy "
            "95%-of-cash cap exactly, which is what makes risk sizing strictly "
            "shrink-only. It is nonetheless a switch that can change position "
            "size (raising it above 0.95 would let positions exceed the previous "
            "behaviour), so it is not NEUTRAL."
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
