package com.neuradex.trade.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.Map;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class RiskValidated {

    private String symbol;
    private String action;
    private double confidence;

    @JsonProperty("position_size")
    private double positionSize;

    @JsonProperty("stop_loss")
    private double stopLoss;

    @JsonProperty("take_profit")
    private double takeProfit;

    @JsonProperty("current_price")
    private double currentPrice;

    @JsonProperty("risk_pct")
    private double riskPct;

    // Vote objects ({signal, confidence, weight}), not bare strings — the
    // ensemble started emitting the richer shape 2026-08-17. Typed as String
    // here until 2026-08-18, which made Jackson reject every approved trade.
    @JsonProperty("agent_votes")
    private Map<String, Object> agentVotes;

    @JsonProperty("validated_at")
    private String validatedAt;

    @JsonProperty("portfolio_value")
    private double portfolioValue;
}
