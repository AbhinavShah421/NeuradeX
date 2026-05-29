package com.neuradex.trade.consumer;

import com.neuradex.trade.dto.RiskValidated;
import com.neuradex.trade.dto.TradeOutcome;
import com.neuradex.trade.service.GrowwOrderService;
import com.neuradex.trade.service.PaperTradingService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class RiskValidatedConsumer {

    private final PaperTradingService paperTradingService;
    private final GrowwOrderService growwOrderService;
    private final RabbitTemplate rabbitTemplate;

    @Value("${trade.paper-mode:true}")
    private boolean paperMode;

    @RabbitListener(queues = "risk.validated")
    public void onRiskValidated(RiskValidated validated) {
        log.info("Received risk.validated: {} {} @ {} (paper={})",
                validated.getAction(), validated.getSymbol(),
                validated.getCurrentPrice(), paperMode);

        try {
            TradeOutcome outcome;
            if (paperMode) {
                outcome = paperTradingService.execute(validated);
            } else {
                outcome = growwOrderService.execute(validated);
            }

            rabbitTemplate.convertAndSend("trade.outcomes", "", outcome);
            log.info("Published trade.outcomes for {} tradeId={}", outcome.getSymbol(), outcome.getTradeId());

        } catch (Exception e) {
            log.error("Trade execution failed for {}: {}", validated.getSymbol(), e.getMessage());
        }
    }
}
