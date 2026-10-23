package com.neuradex.trade.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Builder;
import lombok.Data;

import java.util.Map;

@Data
@Builder
public class TradeOutcome {

    @JsonProperty("trade_id")
    private String tradeId;

    private String symbol;
    private String action;

    @JsonProperty("fill_price")
    private double fillPrice;

    @JsonProperty("fill_qty")
    private double fillQty;

    @JsonProperty("stop_loss")
    private double stopLoss;

    @JsonProperty("take_profit")
    private double takeProfit;

    @JsonProperty("paper_trade")
    private boolean paperTrade;

    // The ensemble confidence this trade cleared the risk gate on. RiskValidated
    // has carried it all along and it was logged on the way past, but it was
    // never put on the outcome — so every stored trade read 0.00 confidence and
    // nothing downstream could tell a 0.61 entry from a 0.95 one.
    private double confidence;

    private String status;

    // Passed straight through from RiskValidated — vote objects, not strings.
    @JsonProperty("agent_votes")
    private Map<String, Object> agentVotes;

    @JsonProperty("executed_at")
    private String executedAt;

    @JsonProperty("portfolio_value")
    private double portfolioValue;

    // ── Populated only on the CLOSING leg ──────────────────────────────────
    // These are boxed, not primitives, on purpose. feedback-service decides
    // "is this a close?" by whether any of exit_price / timestamp_close /
    // outcome is present; a primitive double serialises an entry's exitPrice as
    // 0.0 rather than omitting it, which would make every entry look like a
    // close at zero and hand the weight learner a -100% trade. Nulls are
    // omitted by Jackson, so an entry stays recognisably an entry.
    private Double pnl;

    @JsonProperty("pnl_pct")
    private Double pnlPct;

    @JsonProperty("exit_price")
    private Double exitPrice;

    /** WIN / LOSS / BREAK_EVEN — the result, distinct from `status` ("FILLED"). */
    private String outcome;

    @JsonProperty("timestamp_close")
    private String timestampClose;

    @JsonProperty("duration_minutes")
    private Integer durationMinutes;

    /** stop_loss | take_profit | end_of_day | signal — why the position was closed. */
    @JsonProperty("exit_reason")
    private String exitReason;
}
