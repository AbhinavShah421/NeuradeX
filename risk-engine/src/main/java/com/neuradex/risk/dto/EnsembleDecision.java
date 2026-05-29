package com.neuradex.risk.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.Map;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class EnsembleDecision {

    private String symbol;

    @JsonProperty("final_action")
    private String finalAction;

    @JsonProperty("weighted_confidence")
    private double weightedConfidence;

    @JsonProperty("agreement_score")
    private double agreementScore;

    private double uncertainty;

    @JsonProperty("agent_votes")
    private Map<String, String> agentVotes;

    // Last ATR value used for position sizing (populated by ensemble or market data)
    private double atr;

    // Current price at time of signal
    @JsonProperty("current_price")
    private double currentPrice;

    @JsonProperty("portfolio_value")
    private double portfolioValue;
}
