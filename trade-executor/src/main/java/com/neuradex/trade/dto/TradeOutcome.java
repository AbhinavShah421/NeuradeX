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

    private String status;

    @JsonProperty("agent_votes")
    private Map<String, String> agentVotes;

    @JsonProperty("executed_at")
    private String executedAt;

    @JsonProperty("portfolio_value")
    private double portfolioValue;

    // Populated after close
    private double pnl;

    @JsonProperty("pnl_pct")
    private double pnlPct;
}
