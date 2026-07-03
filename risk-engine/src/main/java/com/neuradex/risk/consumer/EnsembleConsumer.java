package com.neuradex.risk.consumer;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.neuradex.risk.dto.EnsembleDecision;
import com.neuradex.risk.dto.RiskValidated;
import com.neuradex.risk.service.RiskValidatorService;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.util.Optional;

@Slf4j
@Component
@RequiredArgsConstructor
public class EnsembleConsumer {

    private final RiskValidatorService riskValidatorService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    static class EnsembleMessage {
        @JsonProperty("payload")
        private EnsembleDecision payload;
    }

    @RabbitListener(queues = "ensemble.decision")
    public void onEnsembleDecision(byte[] rawBytes) {
        try {
            String rawMessage = new String(rawBytes, StandardCharsets.UTF_8);
            EnsembleDecision decision;
            EnsembleMessage wrapper = objectMapper.readValue(rawMessage, EnsembleMessage.class);
            decision = (wrapper.getPayload() != null) ? wrapper.getPayload()
                    : objectMapper.readValue(rawMessage, EnsembleDecision.class);

            log.info("Received ensemble decision: {} {} (conf={})",
                    decision.getFinalAction(), decision.getSymbol(), decision.getWeightedConfidence());

            Optional<RiskValidated> validated = riskValidatorService.validate(decision);
            validated.ifPresent(riskValidatorService::publishValidated);
        } catch (Exception e) {
            // Rethrow (instead of swallowing) so Spring AMQP's retry interceptor
            // (spring.rabbitmq.listener.simple.retry.* in application.properties) can
            // retry transient failures a bounded number of times with backoff, then
            // hand the message to the RepublishMessageRecoverer (see RabbitConfig)
            // which republishes it to ensemble.decision.dlq instead of losing it.
            log.error("Failed to process ensemble decision (will retry): {}", e.getMessage());
            throw new IllegalStateException("Failed to process ensemble decision: " + e.getMessage(), e);
        }
    }
}
