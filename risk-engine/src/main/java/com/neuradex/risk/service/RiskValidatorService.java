package com.neuradex.risk.service;

import com.neuradex.risk.dto.EnsembleDecision;
import com.neuradex.risk.dto.RiskValidated;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.Optional;

@Slf4j
@Service
public class RiskValidatorService {

    private final RabbitTemplate rabbitTemplate;

    @Value("${risk.min-confidence:0.60}")
    private double minConfidence;

    @Value("${risk.max-position-pct:0.05}")
    private double maxPositionPct;

    @Value("${risk.max-risk-pct:0.02}")
    private double maxRiskPct;

    @Value("${risk.atr-stop-multiplier:2.0}")
    private double atrStopMultiplier;

    @Value("${risk.atr-profit-multiplier:3.0}")
    private double atrProfitMultiplier;

    @Value("${risk.default-portfolio-value:100000.0}")
    private double defaultPortfolioValue;

    public RiskValidatorService(RabbitTemplate rabbitTemplate) {
        this.rabbitTemplate = rabbitTemplate;
    }

    public Optional<RiskValidated> validate(EnsembleDecision decision) {
        String action = decision.getFinalAction();

        if (!"BUY".equals(action) && !"SELL".equals(action)) {
            log.debug("Skipping HOLD signal for {}", decision.getSymbol());
            return Optional.empty();
        }

        if (decision.getWeightedConfidence() < minConfidence) {
            log.info("Rejected {} — confidence {} < threshold {}",
                    decision.getSymbol(), decision.getWeightedConfidence(), minConfidence);
            return Optional.empty();
        }

        double price = decision.getCurrentPrice();
        double atr = decision.getAtr();
        double portfolio = decision.getPortfolioValue() > 0
                ? decision.getPortfolioValue()
                : defaultPortfolioValue;

        if (price <= 0) {
            log.warn("Invalid price for {} — price={}, skipping", decision.getSymbol(), price);
            return Optional.empty();
        }
        // Use a default ATR of 1% of price if not provided
        if (atr <= 0) {
            atr = price * 0.01;
        }

        double stopDistance = atr * atrStopMultiplier;
        double stopLoss;
        double takeProfit;

        if ("BUY".equals(action)) {
            stopLoss = price - stopDistance;
            takeProfit = price + atr * atrProfitMultiplier;
        } else {
            stopLoss = price + stopDistance;
            takeProfit = price - atr * atrProfitMultiplier;
        }

        // Risk per share = stop distance; max risk = portfolio * maxRiskPct
        double maxRiskCapital = portfolio * maxRiskPct;
        double sharesFromRisk = maxRiskCapital / stopDistance;

        // Max position = portfolio * maxPositionPct
        double maxPositionShares = (portfolio * maxPositionPct) / price;

        double positionShares = Math.min(sharesFromRisk, maxPositionShares);
        double positionValue = positionShares * price;
        double riskPct = (stopDistance * positionShares) / portfolio;

        log.info("Risk validated {} {} @ {} | size={} shares | SL={} | TP={} | risk={}%",
                action, decision.getSymbol(), price, (int) positionShares, stopLoss, takeProfit, String.format("%.2f", riskPct * 100));

        RiskValidated validated = RiskValidated.builder()
                .symbol(decision.getSymbol())
                .action(action)
                .confidence(decision.getWeightedConfidence())
                .positionSize(positionShares)
                .stopLoss(stopLoss)
                .takeProfit(takeProfit)
                .currentPrice(price)
                .riskPct(riskPct)
                .agentVotes(decision.getAgentVotes())
                .validatedAt(Instant.now().toString())
                .portfolioValue(portfolio)
                .build();

        return Optional.of(validated);
    }

    public void publishValidated(RiskValidated validated) {
        rabbitTemplate.convertAndSend("risk.validated", "validated", validated);
        log.info("Published risk.validated for {}", validated.getSymbol());
    }
}
