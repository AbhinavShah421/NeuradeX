package com.neuradex.trade.service;

import com.neuradex.trade.dto.RiskValidated;
import com.neuradex.trade.dto.TradeOutcome;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestTemplate;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * Unit tests for {@link GrowwOrderService}. The internal RestTemplate field is
 * replaced with a Mockito mock via reflection (the service builds its own
 * RestTemplate in its no-arg constructor, so there is no constructor seam) — this
 * keeps the tests free of any real network call to Groww's API.
 */
@ExtendWith(MockitoExtension.class)
class GrowwOrderServiceTest {

    @Mock
    private RestTemplate restTemplate;

    private GrowwOrderService service;

    @BeforeEach
    void setUp() {
        service = new GrowwOrderService();
        ReflectionTestUtils.setField(service, "restTemplate", restTemplate);
        ReflectionTestUtils.setField(service, "baseUrl", "https://fake.groww.test/v1/api");
        ReflectionTestUtils.setField(service, "apiToken", "test-token");
    }

    private RiskValidated validated(String action, double price, double positionSize) {
        RiskValidated v = new RiskValidated();
        v.setSymbol("TCS");
        v.setAction(action);
        v.setCurrentPrice(price);
        v.setPositionSize(positionSize);
        v.setStopLoss(price * 0.98);
        v.setTakeProfit(price * 1.03);
        v.setConfidence(0.85);
        v.setPortfolioValue(100000.0);
        return v;
    }

    @Test
    void executePlacesOrderAndReturnsFilledOutcomeOnSuccess() {
        Map<String, Object> body = Map.of(
                "order_id", "GRW-123",
                "average_price", "3500.50"
        );
        when(restTemplate.postForEntity(anyString(), any(), eq(Map.class)))
                .thenReturn(new ResponseEntity<>(body, HttpStatus.OK));

        TradeOutcome outcome = service.execute(validated("BUY", 3500.0, 10));

        assertThat(outcome.getTradeId()).isEqualTo("GRW-123");
        assertThat(outcome.getSymbol()).isEqualTo("TCS");
        assertThat(outcome.getAction()).isEqualTo("BUY");
        assertThat(outcome.getFillPrice()).isEqualTo(3500.50);
        assertThat(outcome.getFillQty()).isEqualTo(10);
        assertThat(outcome.getStatus()).isEqualTo("FILLED");
        assertThat(outcome.isPaperTrade()).isFalse();
    }

    @Test
    void executeRejectsOrderWhenPositionSizeRoundsToZeroQuantity() {
        // positionSize of 0.4 shares floors to qty=0, which must be rejected before any
        // HTTP call is made — this is the order-sizing guard rail.
        RiskValidated tinyOrder = validated("BUY", 3500.0, 0.4);

        assertThatThrownBy(() -> service.execute(tinyOrder))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("quantity must be");

        verifyNoInteractions(restTemplate);
    }

    @Test
    void executePropagatesExceptionWhenGrowwApiCallFails() {
        // A transient network/API failure must propagate out of execute() rather than
        // being swallowed — RiskValidatedConsumer relies on this to trigger the
        // Spring AMQP retry/DLQ path instead of silently losing the order.
        when(restTemplate.postForEntity(anyString(), any(), eq(Map.class)))
                .thenThrow(new ResourceAccessException("Connection refused"));

        assertThatThrownBy(() -> service.execute(validated("SELL", 3500.0, 5)))
                .isInstanceOf(ResourceAccessException.class);
    }

    @Test
    void executeThrowsWhenGrowwRespondsWithNonSuccessStatus() {
        when(restTemplate.postForEntity(anyString(), any(), eq(Map.class)))
                .thenReturn(new ResponseEntity<>(Map.of("error", "rejected"), HttpStatus.BAD_REQUEST));

        assertThatThrownBy(() -> service.execute(validated("BUY", 3500.0, 5)))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("Groww API returned");
    }
}
