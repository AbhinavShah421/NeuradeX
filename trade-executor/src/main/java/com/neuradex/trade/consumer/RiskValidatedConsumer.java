package com.neuradex.trade.consumer;

import com.neuradex.trade.config.TradeModeConfig;
import com.neuradex.trade.dto.OpenPosition;
import com.neuradex.trade.dto.RiskValidated;
import com.neuradex.trade.dto.TradeOutcome;
import com.neuradex.trade.service.GrowwOrderService;
import com.neuradex.trade.service.OpenPositionStore;
import com.neuradex.trade.service.PaperTradingService;
import com.neuradex.trade.service.PositionMonitor;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

import java.time.Instant;

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
    private final OpenPositionStore positions;
    private final PositionMonitor positionMonitor;

    @RabbitListener(queues = "risk.validated")
    public void onRiskValidated(RiskValidated validated) {
        boolean paperMode = tradeModeConfig.isPaperMode();
        log.info("Received risk.validated: {} {} @ {} (paper={}, confidence={})",
                validated.getAction(), validated.getSymbol(),
                validated.getCurrentPrice(), paperMode, validated.getConfidence());

        try {
            validateIncoming(validated);

            String symbol = validated.getSymbol().toUpperCase();
            OpenPosition held = positions.get(symbol);

            // ── A SELL on something we hold is an EXIT, not a new short ────────
            // Nothing upstream distinguishes the two, and this executor has never
            // opened a short. Treating it as an entry is what produced a second
            // open row per symbol instead of closing the first.
            if (held != null && "SELL".equals(validated.getAction())) {
                // Every leg, not just the first: a symbol carrying duplicates from
                // before the dedupe guard existed must be flattened completely, or
                // the leftovers go straight back to being permanently open.
                var legs = positions.forSymbol(symbol);
                log.info("Closing {} on signal @ {} ({} leg(s))",
                        symbol, validated.getCurrentPrice(), legs.size());
                for (OpenPosition leg : legs) {
                    positionMonitor.close(leg, validated.getCurrentPrice(), "signal");
                }
                return;
            }

            // ── Refuse to stack a second position on a symbol already held ─────
            // Observed 2026-09-09: MIDHANI opened at 09:40, 09:42 and 09:42 — three
            // positions on one symbol inside two minutes, two of them at an
            // identical price. The executor kept no position state, so each
            // risk.validated message looked like the first one.
            if (held != null) {
                log.info("Skipped {} {} — already holding {} @ {} since {} (one position per symbol)",
                        validated.getAction(), symbol, held.getQty(),
                        String.format("%.2f", held.getEntryPrice()), held.getOpenedAt());
                return;
            }

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

            // Register BEFORE publishing. If the claim loses a race with another
            // message for the same symbol, that other message owns the position and
            // this fill must not be announced as a second one.
            OpenPosition position = OpenPosition.builder()
                    .tradeId(outcome.getTradeId())
                    .symbol(symbol)
                    .action(outcome.getAction())
                    .entryPrice(outcome.getFillPrice())
                    .qty(outcome.getFillQty())
                    .stopLoss(outcome.getStopLoss())
                    .takeProfit(outcome.getTakeProfit())
                    .confidence(outcome.getConfidence())
                    .paperTrade(outcome.isPaperTrade())
                    .agentVotes(outcome.getAgentVotes())
                    .portfolioValue(outcome.getPortfolioValue())
                    .openedAt(Instant.now())
                    .build();
            if (!positions.tryOpen(position)) {
                log.warn("Race on {} — another message claimed the symbol first; "
                        + "this fill is not published", symbol);
                return;
            }

            rabbitTemplate.convertAndSend("trade.outcomes", "", outcome);
            log.info("Published trade.outcomes for {} tradeId={} (stop={} target={}, {} held)",
                    outcome.getSymbol(), outcome.getTradeId(),
                    String.format("%.2f", outcome.getStopLoss()),
                    String.format("%.2f", outcome.getTakeProfit()), positions.size());

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
