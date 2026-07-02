package com.neuradex.risk.service;

import com.neuradex.risk.dto.EnsembleDecision;
import com.neuradex.risk.dto.RiskValidated;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

/**
 * Unit tests for the core risk-check rules in {@link RiskValidatorService}: signal
 * filtering (HOLD / low confidence / bad price), position sizing math, and the
 * risk.validated publish step. The RabbitMQ dependency is mocked so these tests need
 * neither a broker nor a network connection.
 */
@ExtendWith(MockitoExtension.class)
class RiskValidatorServiceTest {

    @Mock
    private RabbitTemplate rabbitTemplate;

    private RiskValidatorService service;

    @BeforeEach
    void setUp() {
        service = new RiskValidatorService(rabbitTemplate);
        // @Value fields are only populated by Spring's environment; since this is a
        // plain unit test (no ApplicationContext), set them explicitly to the same
        // defaults declared in application.properties.
        ReflectionTestUtils.setField(service, "minConfidence", 0.60);
        ReflectionTestUtils.setField(service, "maxPositionPct", 0.05);
        ReflectionTestUtils.setField(service, "maxRiskPct", 0.02);
        ReflectionTestUtils.setField(service, "atrStopMultiplier", 2.0);
        ReflectionTestUtils.setField(service, "atrProfitMultiplier", 3.0);
        ReflectionTestUtils.setField(service, "defaultPortfolioValue", 100000.0);
    }

    private EnsembleDecision decision(String action, double confidence, double price, double atr, double portfolioValue) {
        EnsembleDecision d = new EnsembleDecision();
        d.setSymbol("RELIANCE");
        d.setFinalAction(action);
        d.setWeightedConfidence(confidence);
        d.setCurrentPrice(price);
        d.setAtr(atr);
        d.setPortfolioValue(portfolioValue);
        return d;
    }

    @Test
    void holdSignalIsSkipped() {
        Optional<RiskValidated> result = service.validate(decision("HOLD", 0.95, 100, 2, 100000));

        assertThat(result).isEmpty();
    }

    @Test
    void lowConfidenceSignalIsRejected() {
        // confidence (0.50) below the 0.60 minConfidence threshold must be rejected
        Optional<RiskValidated> result = service.validate(decision("BUY", 0.50, 100, 2, 100000));

        assertThat(result).isEmpty();
    }

    @Test
    void nonPositivePriceIsRejected() {
        Optional<RiskValidated> result = service.validate(decision("BUY", 0.90, 0, 2, 100000));

        assertThat(result).isEmpty();
    }

    @Test
    void validBuySignalIsSizedWithinRiskAndPositionLimits() {
        // price=100, atr=2, portfolio=100000, maxRiskPct=0.02, maxPositionPct=0.05, stopMult=2, profitMult=3
        // stopDistance = atr * stopMult = 4
        // sharesFromRisk = (portfolio * maxRiskPct) / stopDistance = 2000 / 4 = 500
        // maxPositionShares = (portfolio * maxPositionPct) / price = 5000 / 100 = 50
        // positionShares = min(500, 50) = 50  (position-limit binds, not risk-limit)
        Optional<RiskValidated> result = service.validate(decision("BUY", 0.90, 100, 2, 100000));

        assertThat(result).isPresent();
        RiskValidated v = result.get();
        assertThat(v.getAction()).isEqualTo("BUY");
        assertThat(v.getSymbol()).isEqualTo("RELIANCE");
        assertThat(v.getPositionSize()).isCloseTo(50.0, within(1e-9));
        assertThat(v.getStopLoss()).isCloseTo(96.0, within(1e-9));   // 100 - 4
        assertThat(v.getTakeProfit()).isCloseTo(106.0, within(1e-9)); // 100 + 2*3
        assertThat(v.getPortfolioValue()).isCloseTo(100000.0, within(1e-9));
    }

    @Test
    void validSellSignalInvertsStopAndTakeProfit() {
        Optional<RiskValidated> result = service.validate(decision("SELL", 0.90, 100, 2, 100000));

        assertThat(result).isPresent();
        RiskValidated v = result.get();
        assertThat(v.getAction()).isEqualTo("SELL");
        assertThat(v.getStopLoss()).isCloseTo(104.0, within(1e-9));   // 100 + 4
        assertThat(v.getTakeProfit()).isCloseTo(94.0, within(1e-9));  // 100 - 2*3
    }

    @Test
    void zeroAtrFallsBackToOnePercentOfPrice() {
        // atr <= 0 => atr defaults to price * 0.01 = 200 * 0.01 = 2, same math as the
        // validBuySignal case above but exercised via the fallback branch.
        Optional<RiskValidated> result = service.validate(decision("BUY", 0.90, 200, 0, 100000));

        assertThat(result).isPresent();
        assertThat(result.get().getStopLoss()).isCloseTo(196.0, within(1e-9)); // 200 - (2*2)
    }

    @Test
    void nonPositivePortfolioValueFallsBackToDefaultPortfolio() {
        // portfolioValue <= 0 in the incoming decision => service uses defaultPortfolioValue (100000)
        Optional<RiskValidated> result = service.validate(decision("BUY", 0.90, 100, 2, 0));

        assertThat(result).isPresent();
        assertThat(result.get().getPortfolioValue()).isCloseTo(100000.0, within(1e-9));
    }

    @Test
    void publishValidatedSendsToRiskValidatedExchangeWithValidatedRoutingKey() {
        RiskValidated validated = RiskValidated.builder()
                .symbol("TCS")
                .action("BUY")
                .positionSize(10)
                .build();

        service.publishValidated(validated);

        verify(rabbitTemplate).convertAndSend("risk.validated", "validated", validated);
    }

    @Test
    void holdSignalNeverTouchesRabbitTemplate() {
        service.validate(decision("HOLD", 0.95, 100, 2, 100000));

        verifyNoInteractions(rabbitTemplate);
    }
}
