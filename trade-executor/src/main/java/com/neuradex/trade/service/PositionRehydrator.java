package com.neuradex.trade.service;

import com.neuradex.trade.dto.OpenPosition;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/**
 * Reloads still-open positions at boot.
 *
 * <p>The store is in memory, so without this a restart between 09:15 and 15:30
 * would silently orphan everything held: the rows stay open in the database and
 * nothing alive knows to close them. That is the exact failure being fixed here,
 * and a deploy in market hours would quietly recreate it.
 *
 * <p>It is best-effort by design. feedback-service being slow or down must not
 * stop the executor from starting and taking new signals — a missing rehydrate
 * leaves the old rows stranded (recoverable, and now logged loudly), while a
 * refusal to start drops every signal for the rest of the session.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class PositionRehydrator implements ApplicationRunner {

    private final OpenPositionStore store;
    private final RestTemplate restTemplate;

    @Value("${feedback.url:http://feedback-service:8012}")
    private String feedbackUrl;

    @Override
    public void run(ApplicationArguments args) {
        String url = feedbackUrl.replaceAll("/+$", "") + "/trades/open?days=1";
        try {
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> rows = restTemplate.getForObject(url, List.class);
            if (rows == null || rows.isEmpty()) {
                log.info("No open positions to restore");
                return;
            }
            int restored = 0, skipped = 0;
            for (Map<String, Object> row : rows) {
                OpenPosition p = toPosition(row);
                if (p == null) { skipped++; continue; }
                store.restore(p);
                restored++;
                log.info("Restored open position: {} {} @ {} (stop={} target={}, tradeId={})",
                        p.getQty(), p.getSymbol(), p.getEntryPrice(),
                        p.getStopLoss(), p.getTakeProfit(), p.getTradeId());
            }
            log.info("Rehydrated {} open position(s), {} unusable", restored, skipped);
        } catch (Exception e) {
            log.error("Could not rehydrate open positions from {} — any position held "
                    + "before this restart will NOT be closed by this process: {}",
                    url, e.getMessage());
        }
    }

    @SuppressWarnings("unchecked")
    private OpenPosition toPosition(Map<String, Object> row) {
        String symbol = str(row.get("symbol"));
        String tradeId = str(row.get("trade_id"));
        double entry = num(row.get("entry_price"));
        if (symbol == null || tradeId == null || entry <= 0) {
            log.warn("Skipping unusable open row: {}", row);
            return null;
        }
        // The executor writes fill_qty / stop_loss / take_profit into market_context
        // (see _store_trade_record) because trade_records has no column for them.
        Map<String, Object> ctx = row.get("market_context") instanceof Map
                ? (Map<String, Object>) row.get("market_context") : Map.of();

        double qty = num(ctx.get("fill_qty"));
        if (qty <= 0) {
            log.warn("Skipping {} — stored row has no quantity, so it cannot be sized "
                    + "or closed correctly", symbol);
            return null;
        }
        Map<String, Object> votes = row.get("agent_signals") instanceof Map
                ? (Map<String, Object>) row.get("agent_signals") : Map.of();

        return OpenPosition.builder()
                .tradeId(tradeId)
                .symbol(symbol.toUpperCase())
                .action(str(row.get("action")) == null ? "BUY" : str(row.get("action")))
                .entryPrice(entry)
                .qty(qty)
                // A restored position with no stop or target is not given one here.
                // Zero disables that leg in PositionMonitor and the square-off still
                // applies, which is the honest reading: we do not know what the risk
                // engine decided, and inventing a level would be a fabricated exit.
                .stopLoss(num(ctx.get("stop_loss")))
                .takeProfit(num(ctx.get("take_profit")))
                .confidence(num(row.get("ensemble_confidence")))
                .paperTrade(Boolean.TRUE.equals(row.get("paper_trade")))
                .agentVotes(votes)
                .portfolioValue(num(ctx.get("portfolio_value")))
                .openedAt(instant(row.get("timestamp_open")))
                .build();
    }

    private static String str(Object o) {
        return o == null || o.toString().isBlank() ? null : o.toString();
    }

    private static double num(Object o) {
        if (o == null) return 0.0;
        try {
            double d = Double.parseDouble(o.toString());
            return Double.isFinite(d) ? d : 0.0;
        } catch (NumberFormatException e) {
            return 0.0;
        }
    }

    private static Instant instant(Object o) {
        if (o == null) return Instant.now();
        try {
            return OffsetDateTime.parse(o.toString()).toInstant();
        } catch (Exception e) {
            log.warn("Unparseable timestamp_open {} — using now, duration will be wrong", o);
            return Instant.now();
        }
    }
}
