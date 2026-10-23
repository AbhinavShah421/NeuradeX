"""Dry run: what WOULD the Orders pipeline decide with the directional-contest fix?

Re-aggregates REAL agent votes (pulled from ensemble-engine's decision buffer)
under two formulas and compares them. Changes nothing — pure analysis.

  current     buy = sum(w*c for BUY) / TOTAL weight
              HOLD votes sit in the denominator, so they dilute every
              directional score. Measured ceiling 0.560 vs a 0.60 gate.

  directional the backend ensemble's approach: HOLD carries no information
              about DIRECTION, so it is excluded from the contest. Confidence
              is the winner's share of the DIRECTIONAL mass plus the share of
              directional voters, on the same 0.30 + 0.65*(...) scale the
              backend uses, so the numbers are comparable across pipelines.
"""
import json
import sys
import time
import urllib.request

URL = "http://localhost:8007/decisions/recent"
GATE = 0.60
DIR_DOMINANCE = 1.3      # matches backend ENSEMBLE_DIR_DOMINANCE
DIR_MIN_VOTERS = 2       # matches backend ENSEMBLE_DIR_MIN_VOTERS


def fetch():
    try:
        with urllib.request.urlopen(URL, timeout=8) as r:
            d = json.load(r)
    except Exception as exc:
        print("fetch failed:", exc)
        return []
    rows = d if isinstance(d, list) else d.get("decisions", d.get("data", []))
    return [x.get("payload", x) for x in rows if isinstance(x, dict)]


def current_formula(votes):
    """Reimplementation of ensemble-engine aggregate_signals."""
    buy = sell = hold = total = 0.0
    for _, v in votes.items():
        w, c, s = float(v.get("weight", 0.1)), float(v.get("confidence", 0.5)), v.get("signal", "HOLD")
        wc = w * c
        if s == "BUY":    buy += wc
        elif s == "SELL": sell += wc
        else:             hold += wc
        total += w
    if total == 0:
        return "HOLD", 0.0
    buy, sell, hold = buy / total, sell / total, hold / total
    top = max(buy, sell, hold)
    action = "BUY" if buy == top else ("SELL" if sell == top else "HOLD")
    return action, top * total      # coverage_factor == total


def directional_formula(votes):
    """Backend-style directional contest: HOLD excluded from the entry contest."""
    bm = sm = 0.0
    bn = sn = 0
    for _, v in votes.items():
        w, c, s = float(v.get("weight", 0.1)), float(v.get("confidence", 0.5)), v.get("signal", "HOLD")
        if s == "BUY":    bm += w * c; bn += 1
        elif s == "SELL": sm += w * c; sn += 1
    if bm > 0 and bm >= DIR_DOMINANCE * sm and bn >= DIR_MIN_VOTERS:
        action, win_mass, win_n = "BUY", bm, bn
    elif sm > 0 and sm >= DIR_DOMINANCE * bm and sn >= DIR_MIN_VOTERS:
        action, win_mass, win_n = "SELL", sm, sn
    else:
        return "HOLD", None
    dir_mass = (bm + sm) or 1.0
    dir_n = (bn + sn) or 1
    conf = 0.30 + 0.65 * (0.6 * (win_mass / dir_mass) + 0.4 * (win_n / dir_n))
    return action, conf


seen, samples = set(), []
rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 6
for i in range(rounds):
    for p in fetch():
        votes = p.get("agent_votes") or {}
        key = (p.get("symbol"), p.get("weighted_confidence"), len(votes),
               tuple(sorted((k, v.get("signal")) for k, v in votes.items())))
        if votes and key not in seen:
            seen.add(key)
            samples.append(p)
    if i < rounds - 1:
        time.sleep(10)

if not samples:
    print("no samples collected")
    raise SystemExit(1)

cur_actions, dir_actions = {}, {}
cur_pass = dir_pass = 0
dir_confs = []
flips = []

for p in samples:
    votes = p["agent_votes"]
    ca, cc = current_formula(votes)
    da, dc = directional_formula(votes)
    cur_actions[ca] = cur_actions.get(ca, 0) + 1
    dir_actions[da] = dir_actions.get(da, 0) + 1
    if cc >= GATE:
        cur_pass += 1
    if dc is not None:
        dir_confs.append(dc)
        if dc >= GATE:
            dir_pass += 1
            flips.append((p.get("symbol"), ca, round(cc, 3), da, round(dc, 3)))

n = len(samples)
print("DRY RUN — %d unique decisions with real agent votes" % n)
print("  gate (MIN_CONFIDENCE_TO_TRADE) = %.2f\n" % GATE)
print("  CURRENT     actions=%s" % cur_actions)
print("              would trade: %d/%d (%.0f%%)" % (cur_pass, n, 100 * cur_pass / n))
print("  DIRECTIONAL actions=%s" % dir_actions)
print("              would trade: %d/%d (%.0f%%)" % (dir_pass, n, 100 * dir_pass / n))
if dir_confs:
    dir_confs.sort()
    print("              directional confidence: min=%.3f med=%.3f max=%.3f"
          % (dir_confs[0], dir_confs[len(dir_confs) // 2], dir_confs[-1]))
if flips:
    print("\n  decisions that would become TRADES (symbol, now, conf -> then, conf):")
    for f in flips[:12]:
        print("    %-12s %-5s %.3f  ->  %-5s %.3f" % f)
else:
    print("\n  no decision would clear the gate under either formula")
