package com.neuradex.trade.service;

import com.neuradex.trade.dto.RiskValidated;
import com.neuradex.trade.dto.TradeOutcome;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.retry.annotation.Backoff;
import org.springframework.retry.annotation.Retryable;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@Slf4j
@Service
public class GrowwOrderService {

    private final RestTemplate restTemplate;

    @Value("${groww.api.base-url:https://groww.in/v1/api}")
    private String baseUrl;

    @Value("${groww.api.token:}")
    private String apiToken;

    public GrowwOrderService(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    @Retryable(maxAttempts = 3, backoff = @Backoff(delay = 1000, multiplier = 2))
    public TradeOutcome execute(RiskValidated validated) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setBearerAuth(apiToken);

        String orderType = "BUY".equals(validated.getAction()) ? "BUY" : "SELL";
        int qty = (int) Math.floor(validated.getPositionSize());
        if (qty <= 0) {
            throw new IllegalArgumentException(
                "Order quantity must be ≥ 1 for " + validated.getSymbol() +
                " (positionSize=" + validated.getPositionSize() + ")");
        }

        Map<String, Object> body = Map.of(
                "trading_symbol", validated.getSymbol(),
                "exchange", "NSE",
                "transaction_type", orderType,
                "order_type", "MARKET",
                "quantity", qty,
                "product", "MIS"   // intraday margin — all live trades are squared off same day
        );

        HttpEntity<Map<String, Object>> request = new HttpEntity<>(body, headers);

        try {
            ResponseEntity<Map> response = restTemplate.postForEntity(
                    baseUrl + "/order/create", request, Map.class);

            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                Map<?, ?> resp = response.getBody();
                double fillPrice = validated.getCurrentPrice();
                if (resp.containsKey("average_price")) {
                    fillPrice = Double.parseDouble(resp.get("average_price").toString());
                }

                double slippage = Math.abs(fillPrice - validated.getCurrentPrice());
                log.info("[LIVE] {} {} shares of {} @ {} (slippage={})",
                        orderType, qty, validated.getSymbol(), fillPrice, slippage);

                String orderId = resp.containsKey("order_id") ? resp.get("order_id").toString() : UUID.randomUUID().toString();
                return TradeOutcome.builder()
                        .tradeId(orderId)
                        .symbol(validated.getSymbol())
                        .action(validated.getAction())
                        .fillPrice(fillPrice)
                        .fillQty(qty)
                        .stopLoss(validated.getStopLoss())
                        .takeProfit(validated.getTakeProfit())
                        .paperTrade(false)
                        .confidence(validated.getConfidence())
                        .status("FILLED")
                        .agentVotes(validated.getAgentVotes())
                        .executedAt(Instant.now().toString())
                        .portfolioValue(validated.getPortfolioValue())
                        .build();
            } else {
                throw new RuntimeException("Groww API returned: " + response.getStatusCode());
            }
        } catch (Exception e) {
            log.error("Groww order failed for {}: {}", validated.getSymbol(), e.getMessage());
            throw e;
        }
    }

    /**
     * Place the order that FLATTENS an existing position.
     *
     * <p>Called only from PositionMonitor, and only when the position is not a
     * paper trade. The transaction type is the opposite of the entry: a long is
     * closed by selling. Live entries go out as product=MIS, so a position not
     * squared off here is squared off by the broker at its own time and price,
     * which is why a failure to place this is an error rather than a retry-later.
     *
     * @return the exchange order id
     */
    @Retryable(maxAttempts = 3, backoff = @Backoff(delay = 1000, multiplier = 2))
    public String closePosition(String symbol, double qty, String entryAction) {
        int shares = (int) Math.floor(qty);
        if (shares <= 0) {
            throw new IllegalArgumentException(
                "Cannot close " + symbol + " — quantity rounds to zero (qty=" + qty + ")");
        }
        String exitType = "SELL".equals(entryAction) ? "BUY" : "SELL";

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setBearerAuth(apiToken);

        Map<String, Object> body = Map.of(
                "trading_symbol", symbol,
                "exchange", "NSE",
                "transaction_type", exitType,
                "order_type", "MARKET",
                "quantity", shares,
                "product", "MIS"
        );

        ResponseEntity<Map> response = restTemplate.postForEntity(
                baseUrl + "/order/create", new HttpEntity<>(body, headers), Map.class);

        if (!response.getStatusCode().is2xxSuccessful() || response.getBody() == null) {
            throw new IllegalStateException(
                "Exit order rejected for " + symbol + ": " + response.getStatusCode());
        }
        Object orderId = response.getBody().get("order_id");
        log.info("[LIVE] EXIT {} {} shares of {} (orderId={})", exitType, shares, symbol, orderId);
        return orderId != null ? orderId.toString() : "";
    }
}
