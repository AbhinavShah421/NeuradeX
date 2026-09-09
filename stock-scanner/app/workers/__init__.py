"""Specialised scanner workers, composed under the one universe sweep.

The scanner used to answer a single question — "which names are fit to trade
intraday" — inside one 2,700-line pass. This package splits the *analysis* into
focused workers while keeping ONE sweep of the universe:

    sectors.py    which sectors are actually working today, and how broadly
    movers.py     the day's gainers and losers, with an attributed reason
    promotion.py  a micro-scanner's nomination, and the review it must pass

**Why workers, not worker processes.** The obvious reading of "multiple
scanners" is several services each sweeping the market. That would multiply a
2,298-symbol fetch by the number of workers, against a data source that already
returns 403s under load, to compute things that are all derivable from the same
per-symbol record the sweep already builds. So the sweep stays single, and each
worker is a pure function over its output: same inputs, independent questions,
no extra API cost, and each one testable on a list of dicts.

Every worker takes the `movers` list — one entry per analysed symbol, carrying
price, change_pct, gap_pct, rel_volume, rsi, atr_pct plus what the setup scorer
concluded (grade, action, signal_score) — and returns its own view.
"""
from .movers import attribute_move, rank_movers
from .promotion import Promotion, build_promotion, review_promotion
from .sectors import rank_sectors, sector_breadth

__all__ = [
    "rank_sectors", "sector_breadth",
    "rank_movers", "attribute_move",
    "Promotion", "build_promotion", "review_promotion",
]
