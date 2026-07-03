package com.neuradex.trade.service;

import com.neuradex.trade.dto.RiskValidated;
import com.neuradex.trade.dto.TradeOutcome;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;

/**
 * Unit tests for {@link PaperTradingService} — the simulated fill/slippage logic used
 * whenever the platform is running in paper-trading mode. No external dependencies to
 * mock; this class is pure computation.
 */
class PaperTradingServiceTest {

    private final PaperTradingService service = new PaperTradingService();

    private RiskValidated validated(String action, double price, double positionSize) {
        RiskValidated v = new RiskValidated();
        v.setSymbol("INFY");
        v.setAction(action);
        v.setCurrentPrice(price);
        v.setPositionSize(positionSize);
        v.setStopLoss(price * 0.98);
        v.setTakeProfit(price * 1.03);
        v.setConfidence(0.80);
        v.setPortfolioValue(50000.0);
        return v;
    }

    @Test
    void buyOrderFillsAboveMarketPriceBySlippage() {
        TradeOutcome outcome = service.execute(validated("BUY", 1000.0, 20));

        // slippage = price * 0.0005 = 0.5, BUY fills above the current price
        assertThat(outcome.getFillPrice()).isCloseTo(1000.5, within(1e-9));
        assertThat(outcome.getFillQty()).isEqualTo(20.0);
        assertThat(outcome.getAction()).isEqualTo("BUY");
        assertThat(outcome.getStatus()).isEqualTo("FILLED");
        assertThat(outcome.isPaperTrade()).isTrue();
        assertThat(outcome.getTradeId()).isNotBlank();
    }

    @Test
    void sellOrderFillsBelowMarketPriceBySlippage() {
        TradeOutcome outcome = service.execute(validated("SELL", 1000.0, 20));

        // slippage = price * 0.0005 = 0.5, SELL fills below the current price
        assertThat(outcome.getFillPrice()).isCloseTo(999.5, within(1e-9));
        assertThat(outcome.getAction()).isEqualTo("SELL");
        assertThat(outcome.isPaperTrade()).isTrue();
    }

    @Test
    void executeCarriesThroughStopLossAndTakeProfitAndPortfolioValueUnchanged() {
        RiskValidated v = validated("BUY", 500.0, 5);

        TradeOutcome outcome = service.execute(v);

        assertThat(outcome.getStopLoss()).isEqualTo(v.getStopLoss());
        assertThat(outcome.getTakeProfit()).isEqualTo(v.getTakeProfit());
        assertThat(outcome.getPortfolioValue()).isEqualTo(v.getPortfolioValue());
        assertThat(outcome.getSymbol()).isEqualTo("INFY");
    }

    @Test
    void eachExecutionGetsAUniqueTradeId() {
        RiskValidated v = validated("BUY", 500.0, 5);

        TradeOutcome first = service.execute(v);
        TradeOutcome second = service.execute(v);

        assertThat(first.getTradeId()).isNotEqualTo(second.getTradeId());
    }
}
