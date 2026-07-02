package com.neuradex.trade.consumer;

import com.neuradex.trade.config.TradeModeConfig;
import com.neuradex.trade.dto.RiskValidated;
import com.neuradex.trade.dto.TradeOutcome;
import com.neuradex.trade.service.GrowwOrderService;
import com.neuradex.trade.service.PaperTradingService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * Unit tests for {@link RiskValidatedConsumer}, focused on the two behaviors this
 * class is responsible for beyond simple delegation: the live-trading conviction gate,
 * and — central to the H3 dead-letter/retry fix — making sure a failed order
 * placement propagates out of the listener method instead of being swallowed, since
 * Spring AMQP's retry interceptor can only retry/dead-letter an exception it actually
 * sees.
 */
@ExtendWith(MockitoExtension.class)
class RiskValidatedConsumerTest {

    @Mock
    private PaperTradingService paperTradingService;
    @Mock
    private GrowwOrderService growwOrderService;
    @Mock
    private RabbitTemplate rabbitTemplate;
    @Mock
    private TradeModeConfig tradeModeConfig;

    private RiskValidatedConsumer consumer() {
        return new RiskValidatedConsumer(paperTradingService, growwOrderService, rabbitTemplate, tradeModeConfig);
    }

    private RiskValidated validated(String action, double confidence) {
        RiskValidated v = new RiskValidated();
        v.setSymbol("HDFC");
        v.setAction(action);
        v.setCurrentPrice(1600.0);
        v.setPositionSize(10);
        v.setConfidence(confidence);
        return v;
    }

    @Test
    void publishesTradeOutcomeOnSuccessfulPaperExecution() {
        when(tradeModeConfig.isPaperMode()).thenReturn(true);
        TradeOutcome outcome = TradeOutcome.builder().tradeId("t1").symbol("HDFC").build();
        when(paperTradingService.execute(any())).thenReturn(outcome);

        consumer().onRiskValidated(validated("BUY", 0.90));

        verify(rabbitTemplate).convertAndSend("trade.outcomes", "", outcome);
        verify(growwOrderService, never()).execute(any());
    }

    @Test
    void orderExecutionFailureIsNotSwallowedSoItCanBeRetried() {
        when(tradeModeConfig.isPaperMode()).thenReturn(true);
        when(paperTradingService.execute(any())).thenThrow(new RuntimeException("simulated failure"));

        // Prior to the H3 fix this exception was caught, logged, and the message was
        // acked — silently losing the order. It must now propagate so the configured
        // Spring AMQP retry/DLQ handling (see RabbitConfig) actually gets a chance to
        // run.
        assertThatThrownBy(() -> consumer().onRiskValidated(validated("BUY", 0.90)))
                .isInstanceOf(RuntimeException.class);

        verify(rabbitTemplate, never()).convertAndSend(any(String.class), any(String.class), any(Object.class));
    }

    @Test
    void rejectsPayloadWithInvalidActionBeforeExecutingAnyTrade() {
        when(tradeModeConfig.isPaperMode()).thenReturn(true);
        RiskValidated malformed = validated("HOLD", 0.90); // only BUY/SELL are valid actions

        // validateIncoming()'s IllegalArgumentException is caught and wrapped alongside
        // every other failure mode so the retry/DLQ path (see H3) has one consistent
        // exception type to work with; the original cause is preserved.
        assertThatThrownBy(() -> consumer().onRiskValidated(malformed))
                .isInstanceOf(IllegalStateException.class)
                .hasCauseInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Invalid action");

        verifyNoInteractions(paperTradingService, growwOrderService);
    }

    @Test
    void liveTradeBelowConvictionThresholdIsSkippedWithoutError() {
        when(tradeModeConfig.isPaperMode()).thenReturn(false);
        // confidence 0.60 is below the 0.72 MIN_CONVICTION floor for live trades
        RiskValidated lowConviction = validated("BUY", 0.60);

        consumer().onRiskValidated(lowConviction);

        verifyNoInteractions(growwOrderService, paperTradingService);
        verify(rabbitTemplate, never()).convertAndSend(any(String.class), any(String.class), any(Object.class));
    }
}
