package com.neuradex.trade.consumer;

import com.neuradex.trade.config.TradeModeConfig;
import com.neuradex.trade.dto.RiskValidated;
import com.neuradex.trade.dto.TradeOutcome;
import com.neuradex.trade.service.GrowwOrderService;
import com.neuradex.trade.service.PaperTradingService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class RiskValidatedConsumer {

    private static final double MIN_CONVICTION  = 0.72;  // ensemble confidence floor for live orders
    private static final double MIN_AGREEMENT   = 0.55;  // fraction of agents that must agree

    private final PaperTradingService paperTradingService;
    private final GrowwOrderService growwOrderService;
    private final RabbitTemplate rabbitTemplate;
    private final TradeModeConfig tradeModeConfig;

    @RabbitListener(queues = "risk.validated")
    public void onRiskValidated(RiskValidated validated) {
        boolean paperMode = tradeModeConfig.isPaperMode();
        log.info("Received risk.validated: {} {} @ {} (paper={}, confidence={})",
                validated.getAction(), validated.getSymbol(),
                validated.getCurrentPrice(), paperMode, validated.getConfidence());

        try {
            validateIncoming(validated);

            // Conviction gate — live trades only fire on high-confidence signals
            if (!paperMode && validated.getConfidence() < MIN_CONVICTION) {
                log.info("[LIVE] Skipped — low conviction: {} {} confidence={} < threshold={}",
                        validated.getAction(), validated.getSymbol(),
                        validated.getConfidence(), MIN_CONVICTION);
                return;
            }

            TradeOutcome outcome;
            if (paperMode) {
                outcome = paperTradingService.execute(validated);
            } else {
                outcome = growwOrderService.execute(validated);
            }

            rabbitTemplate.convertAndSend("trade.outcomes", "", outcome);
            log.info("Published trade.outcomes for {} tradeId={}", outcome.getSymbol(), outcome.getTradeId());

        } catch (Exception e) {
            // Rethrow so Spring AMQP's retry interceptor (spring.rabbitmq.listener.simple.retry.*
            // in application.properties) retries transient failures (e.g. Groww API blips) a
            // bounded number of times with backoff, then hands the message to the
            // RepublishMessageRecoverer (see RabbitConfig) which republishes it to
            // risk.validated.dlq instead of the order silently vanishing.
            log.error("Trade execution failed for {} (will retry): {}", validated.getSymbol(), e.getMessage());
            throw new IllegalStateException(
                "Trade execution failed for " + validated.getSymbol() + ": " + e.getMessage(), e);
        }
    }

    private void validateIncoming(RiskValidated v) {
        if (v.getSymbol() == null || v.getSymbol().isBlank())
            throw new IllegalArgumentException("Missing symbol");
        if (v.getAction() == null || (!v.getAction().equals("BUY") && !v.getAction().equals("SELL")))
            throw new IllegalArgumentException("Invalid action: " + v.getAction());
        if (v.getCurrentPrice() <= 0)
            throw new IllegalArgumentException("current_price must be > 0, got " + v.getCurrentPrice());
        if (v.getPositionSize() <= 0)
            throw new IllegalArgumentException("position_size must be > 0, got " + v.getPositionSize());
    }
}
