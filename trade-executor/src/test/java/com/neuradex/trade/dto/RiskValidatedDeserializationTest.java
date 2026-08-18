package com.neuradex.trade.dto;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Regression test for the wire contract on `risk.validated`.
 *
 * <p>The ensemble started emitting per-agent vote <em>objects</em>
 * ({@code {signal, confidence, weight}}) rather than bare signal strings.
 * risk-engine's DTO was widened to {@code Map<String, Object>}; this service's was
 * not, so from 2026-08-17 Jackson threw MismatchedInputException on every approved
 * trade and Spring AMQP dropped it as a fatal conversion error. Both approvals on
 * 2026-08-18 died this way — the pipeline validated trades it could never execute.
 *
 * <p>The payload below is copied verbatim from the message that failed in
 * production, so this test fails again if the DTO ever narrows back.
 */
class RiskValidatedDeserializationTest {

    private static final String PRODUCTION_PAYLOAD = """
        {"symbol":"JYOTICNC","action":"SELL","confidence":0.612,
         "position_size":5.599104143337066,"stop_loss":910.86,"take_profit":866.21,
         "current_price":893.0,"risk_pct":0.001,
         "agent_votes":{
           "technical":{"signal":"HOLD","confidence":0.5,"weight":1.017},
           "pattern":{"signal":"HOLD","confidence":0.5,"weight":1.085},
           "momentum":{"signal":"SELL","confidence":0.625,"weight":1.297},
           "volatility":{"signal":"HOLD","confidence":0.586,"weight":0.99},
           "sentiment":{"signal":"SELL","confidence":0.689,"weight":1.04}},
         "validated_at":"2026-08-18T04:59:11Z","portfolio_value":100000.0}
        """;

    @Test
    void deserializesNestedAgentVoteObjects() throws Exception {
        RiskValidated validated = new ObjectMapper().readValue(PRODUCTION_PAYLOAD, RiskValidated.class);

        assertThat(validated.getSymbol()).isEqualTo("JYOTICNC");
        assertThat(validated.getAction()).isEqualTo("SELL");
        assertThat(validated.getConfidence()).isEqualTo(0.612);
        assertThat(validated.getStopLoss()).isEqualTo(910.86);

        assertThat(validated.getAgentVotes()).hasSize(5);
        @SuppressWarnings("unchecked")
        Map<String, Object> momentum = (Map<String, Object>) validated.getAgentVotes().get("momentum");
        assertThat(momentum.get("signal")).isEqualTo("SELL");
        assertThat(momentum.get("weight")).isEqualTo(1.297);
    }

    /** The older flat shape must keep working — replay/backtest paths still emit it. */
    @Test
    void stillDeserializesFlatStringVotes() throws Exception {
        String flat = """
            {"symbol":"TCS","action":"BUY","confidence":0.71,
             "agent_votes":{"technical":"BUY","pattern":"HOLD"}}
            """;

        RiskValidated validated = new ObjectMapper().readValue(flat, RiskValidated.class);

        assertThat(validated.getAgentVotes()).containsEntry("technical", "BUY");
    }
}
