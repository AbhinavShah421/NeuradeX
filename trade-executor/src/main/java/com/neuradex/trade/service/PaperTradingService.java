package com.neuradex.trade.service;

import com.neuradex.trade.dto.RiskValidated;
import com.neuradex.trade.dto.TradeOutcome;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.UUID;

@Slf4j
@Service
public class PaperTradingService {

    // Simulated slippage: 0.05% of price
    private static final double SLIPPAGE_FACTOR = 0.0005;

    public TradeOutcome execute(RiskValidated validated) {
        String action = validated.getAction();
        double price = validated.getCurrentPrice();
        double slippage = price * SLIPPAGE_FACTOR;

        double fillPrice = "BUY".equals(action) ? price + slippage : price - slippage;
        double fillQty = validated.getPositionSize();

        log.info("[PAPER] {} {} shares of {} @ {:.2f} (slippage={:.4f})",
                action, fillQty, validated.getSymbol(), fillPrice, slippage);

        return TradeOutcome.builder()
                .tradeId(UUID.randomUUID().toString())
                .symbol(validated.getSymbol())
                .action(action)
                .fillPrice(fillPrice)
                .fillQty(fillQty)
                .stopLoss(validated.getStopLoss())
                .takeProfit(validated.getTakeProfit())
                .paperTrade(true)
                .status("FILLED")
                .agentVotes(validated.getAgentVotes())
                .executedAt(Instant.now().toString())
                .portfolioValue(validated.getPortfolioValue())
                .build();
    }
}
