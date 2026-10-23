package com.neuradex.trade.controller;

import com.neuradex.trade.dto.OpenPosition;
import com.neuradex.trade.service.OpenPositionStore;
import com.neuradex.trade.service.PositionMonitor;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * What the executor currently holds.
 *
 * <p>The position state lives in memory, which makes it the one part of this
 * service that cannot be read from the database or the queue. Without this
 * endpoint, "why has the executor not closed X" is unanswerable from outside.
 */
@Slf4j
@RestController
@RequestMapping("/positions")
@RequiredArgsConstructor
public class PositionController {

    private final OpenPositionStore store;
    private final PositionMonitor monitor;

    @GetMapping
    public Map<String, Object> open() {
        List<Map<String, Object>> rows = store.all().stream().map(p -> {
            Map<String, Object> m = new HashMap<>();
            m.put("trade_id", p.getTradeId());
            m.put("symbol", p.getSymbol());
            m.put("action", p.getAction());
            m.put("entry_price", p.getEntryPrice());
            m.put("qty", p.getQty());
            m.put("stop_loss", p.getStopLoss());
            m.put("take_profit", p.getTakeProfit());
            m.put("paper_trade", p.isPaperTrade());
            m.put("opened_at", String.valueOf(p.getOpenedAt()));
            m.put("last_price", p.getLastPrice());
            m.put("last_price_at", String.valueOf(p.getLastPriceAt()));
            // Unrealised, at the last price actually observed — never at a
            // substituted one, so a stale feed shows as a stale timestamp rather
            // than as a confident number.
            m.put("unrealized_pnl", p.getLastPrice() > 0 ? p.pnlAt(p.getLastPrice()) : null);
            return m;
        }).toList();
        return Map.of("count", rows.size(), "positions", rows);
    }

    /**
     * Manually flatten one position at a price the caller supplies.
     *
     * <p>The escape hatch for a position the monitor cannot close — typically an
     * unpriceable symbol that blocked its own square-off. It takes an explicit
     * price rather than fetching one, because the situation it exists for is
     * precisely the one where no price can be fetched.
     */
    @PostMapping("/{symbol}/close")
    public ResponseEntity<Map<String, Object>> forceClose(
            @PathVariable String symbol, @RequestParam double price) {
        OpenPosition p = store.get(symbol);
        if (p == null) {
            return ResponseEntity.status(404).body(Map.of("error", "not holding " + symbol));
        }
        if (!(price > 0) || !Double.isFinite(price)) {
            return ResponseEntity.badRequest().body(Map.of("error", "price must be > 0"));
        }
        log.warn("MANUAL CLOSE requested for {} @ {}", symbol, price);
        monitor.close(p, price, "manual");
        return ResponseEntity.ok(Map.of("closed", symbol, "price", price));
    }
}
