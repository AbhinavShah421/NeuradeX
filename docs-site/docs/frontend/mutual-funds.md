---
id: mutual-funds
title: Mutual Funds Page
sidebar_label: Mutual Funds
---

# Mutual Funds (`/mutual-funds`, nav "Funds")

Real mutual-fund tracking, screening and AI optimisation on official AMFI NAV data
(see [Mutual Funds API](../api/mutual-funds.md)). Three tabs:

## My Funds
- **Search-to-add**: type a fund name → pick it → add with units + invested.
- Table of live **NAV, current value, P&L and 1M/3M/6M/1Y/3Y returns** per fund.
- **✨ AI scan & replace** — verdict per fund (HOLD / REVIEW / REPLACE) with a
  better risk-adjusted peer to switch to.

## Optimize
Whole-portfolio AI optimiser with a **risk selector** (conservative / moderate /
aggressive):
- **Asset allocation** (Equity / Hybrid / Debt) current-vs-target bars.
- KPI cards: keep / replace / consolidate counts + avg risk-adjusted.
- **Action plan** cards: KEEP / REPLACE / CONSOLIDATE per fund (REPLACE shows the
  suggested peer; CONSOLIDATE points to your best fund in that category).
- An **AI summary** (LLM, rule-based fallback).

## Screener
- Category chips (equity caps, hybrid, debt, sectoral) + a **Rank by: 1Y Return /
  Risk-adjusted** toggle.
- Leaderboard with NAV, returns, **Vol** and **Risk-adj** columns; ⭐ AI top-picks
  highlighted (subtle tint, readable on dark theme).

> Groww doesn't expose MF holdings via API, so My Funds are entered manually; all
> NAV/return data is live and real.
