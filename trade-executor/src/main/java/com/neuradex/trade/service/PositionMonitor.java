package com.neuradex.trade.service;

import com.neuradex.trade.dto.OpenPosition;
import com.neuradex.trade.dto.TradeOutcome;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.DayOfWeek;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Closes what the executor opened.
 *
 * <p>Before this existed, PaperTradingService.execute published an entry and
 * nothing ever published a close, so every trade the Orders pipeline took stayed
 * open forever: five permanently-open rows on a day with two real trades,
 * accumulating daily, and a weight learner that never saw a single outcome from
 * this path.
 *
 * <h2>This is not a second exit engine</h2>
 * It enforces the stop_loss and take_profit that arrive ON the RiskValidated
 * message, already computed upstream by risk-engine, plus a same-day square-off
 * that the live path's product=MIS makes mandatory anyway. It invents no trailing
 * stop, no time-stop and no target of its own — those are strategy, they live in
 * the session runner, and duplicating them here would give the system two
 * disagreeing opinions about when to get out.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PositionMonitor {

    private static final ZoneId IST = ZoneId.of("Asia/Kolkata");

    /** Containers run UTC; every clock decision here is made in IST explicitly. */
    private static final LocalTime MARKET_OPEN  = LocalTime.of(9, 15);
    private static final LocalTime SQUARE_OFF   = LocalTime.of(15, 20);
    /** Keep polling a little past the bell so a failed square-off gets retries. */
    private static final LocalTime MONITOR_STOP = LocalTime.of(15, 45);

    private final OpenPositionStore store;
    private final PriceFeed priceFeed;
    private final RabbitTemplate rabbitTemplate;
    private final GrowwOrderService growwOrderService;

    /** A price older than this is not allowed to close a position. */
    @Value("${trade.monitor.max-price-age-minutes:10}")
    private long maxPriceAgeMinutes;

    @Scheduled(fixedDelayString = "${trade.monitor.interval-ms:30000}",
               initialDelayString = "${trade.monitor.initial-delay-ms:20000}")
    public void tick() {
        if (store.size() == 0) return;

        ZonedDateTime nowIst = ZonedDateTime.now(IST);
        LocalTime t = nowIst.toLocalTime();
        boolean weekday = nowIst.getDayOfWeek() != DayOfWeek.SATURDAY
                       && nowIst.getDayOfWeek() != DayOfWeek.SUNDAY;
        if (!weekday || t.isBefore(MARKET_OPEN) || t.isAfter(MONITOR_STOP)) return;

        List<OpenPosition> held = new ArrayList<>(store.all());
        // Distinct symbols, not one request slot per leg — three MIDHANI legs are
        // one quote, and the price endpoint caps at 100 symbols.
        Map<String, Double> prices = priceFeed.ltp(store.symbols());
        Instant now = Instant.now();
        boolean squareOff = !t.isBefore(SQUARE_OFF);

        for (OpenPosition p : held) {
            Double fresh = prices.get(p.getSymbol());
            if (fresh != null) p.markPrice(fresh, now);

            String reason = exitReason(p, fresh, squareOff);
            if (reason == null) continue;

            Double price = fresh != null ? fresh : usableStalePrice(p, now);
            if (price == null) {
                // Only reachable at square-off with no priceable quote. Closing at a
                // number we cannot stand behind is worse than staying open, so this
                // is loud and retried rather than resolved with a guess.
                log.error("SQUARE-OFF BLOCKED — no usable price for {} (held {} @ {}). "
                        + "Position stays open; will retry.",
                        p.getSymbol(), p.getQty(), p.getEntryPrice());
                continue;
            }
            close(p, price, reason);
        }
    }

    /**
     * Which exit, if any, fires at this price.
     *
     * <p>When one polling interval spans both the stop and the target, the tick
     * order inside it is unknowable from a snapshot. It resolves to the STOP:
     * assuming the good fill would book wins that may not have happened and
     * flatter every downstream measurement.
     */
    String exitReason(OpenPosition p, Double price, boolean squareOff) {
        if (price != null && price > 0) {
            boolean isLong = !"SELL".equals(p.getAction());
            double sl = p.getStopLoss(), tp = p.getTakeProfit();
            if (isLong) {
                if (sl > 0 && price <= sl) return "stop_loss";
                if (tp > 0 && price >= tp) return "take_profit";
            } else {
                if (sl > 0 && price >= sl) return "stop_loss";
                if (tp > 0 && price <= tp) return "take_profit";
            }
        }
        return squareOff ? "end_of_day" : null;
    }

    private Double usableStalePrice(OpenPosition p, Instant now) {
        Instant at = p.getLastPriceAt();
        if (at == null || p.getLastPrice() <= 0) return null;
        return Duration.between(at, now).toMinutes() <= maxPriceAgeMinutes ? p.getLastPrice() : null;
    }

    /** Close a position and publish the closing leg. Safe to call concurrently. */
    public void close(OpenPosition position, double exitPrice, String reason) {
        // Release first, by trade id: whoever wins the remove owns the close, so a
        // scheduled tick and an inbound SELL cannot both publish one — and one leg
        // closing does not release its duplicates on the same symbol.
        OpenPosition p = store.release(position.getSymbol(), position.getTradeId());
        if (p == null) return;

        try {
            if (!p.isPaperTrade()) {
                growwOrderService.closePosition(p.getSymbol(), p.getQty(), p.getAction());
            }

            double pnl = p.pnlAt(exitPrice);
            double pnlPct = p.pnlPctAt(exitPrice);
            Instant closedAt = Instant.now();

            TradeOutcome close = TradeOutcome.builder()
                    // SAME trade id as the entry — feedback-service upserts on it, so
                    // this updates the entry row instead of storing a second half-trade.
                    .tradeId(p.getTradeId())
                    .symbol(p.getSymbol())
                    .action(p.getAction())
                    .fillPrice(p.getEntryPrice())     // -> entry_price, preserved on the row
                    .fillQty(p.getQty())
                    .stopLoss(p.getStopLoss())
                    .takeProfit(p.getTakeProfit())
                    .paperTrade(p.isPaperTrade())
                    .confidence(p.getConfidence())
                    .agentVotes(p.getAgentVotes())
                    .portfolioValue(p.getPortfolioValue())
                    .executedAt(p.getOpenedAt().toString())   // -> timestamp_open, the ENTRY time
                    .status("CLOSED")
                    .exitPrice(exitPrice)
                    .pnl(pnl)
                    .pnlPct(pnlPct)                            // fraction, not percent
                    .outcome(classify(pnlPct))
                    .timestampClose(closedAt.toString())
                    .durationMinutes((int) Duration.between(p.getOpenedAt(), closedAt).toMinutes())
                    .exitReason(reason)
                    .build();

            rabbitTemplate.convertAndSend("trade.outcomes", "", close);
            log.info("[CLOSE:{}] {} {} @ {} (entry {}) pnl={} ({}%) tradeId={}",
                    reason, p.getAction(), p.getSymbol(), String.format("%.2f", exitPrice),
                    String.format("%.2f", p.getEntryPrice()), String.format("%.2f", pnl),
                    String.format("%.2f", pnlPct * 100), p.getTradeId());
        } catch (Exception e) {
            // The position is already out of the store. Putting it back would risk a
            // double close on the next tick; leaving it out risks an unpublished
            // close. The unpublished close is recoverable from the log and the broker,
            // a double exit order is not.
            log.error("CLOSE PUBLISH FAILED for {} @ {} ({}) — position released, "
                    + "outcome NOT recorded: {}",
                    p.getSymbol(), exitPrice, reason, e.getMessage(), e);
        }
    }

    /** Mirrors feedback-service determine_outcome (threshold 0.001 on the fraction). */
    static String classify(double pnlPct) {
        if (pnlPct > 0.001) return "WIN";
        if (pnlPct < -0.001) return "LOSS";
        return "BREAK_EVEN";
    }
}
