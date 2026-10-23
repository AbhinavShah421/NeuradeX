package com.neuradex.trade.dto;

import lombok.Builder;
import lombok.Getter;

import java.time.Instant;
import java.util.Map;

/**
 * A position this executor opened and has not yet closed.
 *
 * <p>Everything here except the two {@code last*} fields is captured at entry and
 * never changes, because the closing leg has to be publishable from this record
 * alone. The close is published with the SAME {@code tradeId} as the entry — the
 * feedback-service INSERT is {@code ON CONFLICT (trade_id) DO UPDATE}, so an
 * entry and its close collapse into one row only if the id is carried through.
 * Generating a fresh id on close would store two half-rows instead of one trade.
 */
@Getter
@Builder
public class OpenPosition {

    private final String tradeId;
    private final String symbol;
    private final String action;          // direction of the ENTRY leg
    private final double entryPrice;
    private final double qty;
    private final double stopLoss;        // absolute price, computed upstream by risk-engine
    private final double takeProfit;      // absolute price, computed upstream by risk-engine
    private final double confidence;
    private final boolean paperTrade;
    private final Map<String, Object> agentVotes;
    private final double portfolioValue;
    private final Instant openedAt;

    /** Last price actually observed for this symbol; 0 until the first successful poll. */
    @Builder.Default
    private volatile double lastPrice = 0.0;
    @Builder.Default
    private volatile Instant lastPriceAt = null;

    public void markPrice(double price, Instant at) {
        this.lastPrice = price;
        this.lastPriceAt = at;
    }

    /** Rupee P&L of closing here. Long-only today; SELL entries invert. */
    public double pnlAt(double exitPrice) {
        double perShare = "SELL".equals(action) ? entryPrice - exitPrice : exitPrice - entryPrice;
        return perShare * qty;
    }

    /**
     * P&L as a FRACTION, not a percentage. trade_records.pnl_pct is stored as a
     * fraction everywhere else in this system (measured: mean |pnl_pct| 0.0062
     * across 194 paper trades, i.e. 0.62%), and determine_outcome's WIN/LOSS
     * threshold is 0.001. Publishing 0.62 here instead of 0.0062 would not fail —
     * it would quietly feed the weight learner a 100x signal on every trade.
     */
    public double pnlPctAt(double exitPrice) {
        if (entryPrice <= 0) return 0.0;
        return pnlAt(exitPrice) / (entryPrice * qty);
    }
}
