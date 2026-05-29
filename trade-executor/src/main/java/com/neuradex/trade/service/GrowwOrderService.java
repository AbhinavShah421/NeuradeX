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

    public GrowwOrderService() {
        this.restTemplate = new RestTemplate();
    }

    @Retryable(maxAttempts = 3, backoff = @Backoff(delay = 1000, multiplier = 2))
    public TradeOutcome execute(RiskValidated validated) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setBearerAuth(apiToken);

        String orderType = "BUY".equals(validated.getAction()) ? "BUY" : "SELL";
        int qty = (int) Math.floor(validated.getPositionSize());

        Map<String, Object> body = Map.of(
                "trading_symbol", validated.getSymbol(),
                "exchange", "NSE",
                "transaction_type", orderType,
                "order_type", "MARKET",
                "quantity", qty,
                "product", "CNC"
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
}
