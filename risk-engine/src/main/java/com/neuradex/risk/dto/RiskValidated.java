package com.neuradex.risk.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Builder;
import lombok.Data;

import java.time.Instant;
import java.util.Map;

@Data
@Builder
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

    // Passed through from EnsembleDecision — vote objects, not bare strings.
    @JsonProperty("agent_votes")
    private Map<String, Object> agentVotes;

    @JsonProperty("validated_at")
    private String validatedAt;

    @JsonProperty("portfolio_value")
    private double portfolioValue;
}
