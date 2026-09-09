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

    // Populated after close
    private double pnl;

    @JsonProperty("pnl_pct")
    private double pnlPct;
}
