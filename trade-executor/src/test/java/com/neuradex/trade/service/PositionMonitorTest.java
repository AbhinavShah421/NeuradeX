package com.neuradex.trade.service;

import com.neuradex.trade.dto.OpenPosition;
import com.neuradex.trade.dto.TradeOutcome;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

import java.time.Instant;
import java.time.temporal.ChronoUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

/**
 * The closing leg — the half of the trade lifecycle that did not exist until
 * 2026-09-09. Every executor trade since 2026-08-18 stayed open forever because
 * an entry was published and nothing ever published a close.
 */
@ExtendWith(MockitoExtension.class)
class PositionMonitorTest {

    @Mock private PriceFeed priceFeed;
    @Mock private RabbitTemplate rabbitTemplate;
    @Mock private GrowwOrderService growwOrderService;

    private final OpenPositionStore store = new OpenPositionStore();

    private PositionMonitor monitor() {
        return new PositionMonitor(store, priceFeed, rabbitTemplate, growwOrderService);
    }

    private OpenPosition longPosition() {
        return OpenPosition.builder()
                .tradeId("t1").symbol("MIDHANI").action("BUY")
                .entryPrice(100.0).qty(10)
                .stopLoss(98.0).takeProfit(104.0)
                .confidence(0.71).paperTrade(true)
                .portfolioValue(100000)
                .openedAt(Instant.now().minus(30, ChronoUnit.MINUTES))
                .build();
    }

    // ── Which exit fires ──────────────────────────────────────────────────────

    @Test
    void stopAndTargetFireOnTheLevelsThatArrivedWithTheSignal() {
        PositionMonitor m = monitor();
        OpenPosition p = longPosition();

        assertThat(m.exitReason(p, 101.0, false)).isNull();
        assertThat(m.exitReason(p, 98.0, false)).isEqualTo("stop_loss");
        assertThat(m.exitReason(p, 97.5, false)).isEqualTo("stop_loss");
        assertThat(m.exitReason(p, 104.0, false)).isEqualTo("take_profit");
    }

    @Test
    void aLevelOfZeroDisablesThatLegRatherThanFiringAtOnce() {
        // A restored position may have no stored stop or target. Treating 0 as a
        // price would stop out every long instantly, since any price is above it.
        PositionMonitor m = monitor();
        OpenPosition noLevels = OpenPosition.builder()
                .tradeId("t2").symbol("X").action("BUY").entryPrice(100).qty(1)
                .stopLoss(0).takeProfit(0).openedAt(Instant.now()).build();

        assertThat(m.exitReason(noLevels, 100.0, false)).isNull();
        assertThat(m.exitReason(noLevels, 1.0, false)).isNull();
        // The square-off still applies — it is the one exit that needs no level.
        assertThat(m.exitReason(noLevels, 100.0, true)).isEqualTo("end_of_day");
    }

    @Test
    void aTickSpanningBothLevelsResolvesToTheStop() {
        // A 30-second poll can straddle both. Which came first is unknowable from a
        // snapshot, and assuming the target books wins that may not have happened.
        PositionMonitor m = monitor();
        OpenPosition wide = OpenPosition.builder()
                .tradeId("t3").symbol("X").action("BUY").entryPrice(100).qty(1)
                .stopLoss(120).takeProfit(90).openedAt(Instant.now()).build();

        assertThat(m.exitReason(wide, 100.0, false)).isEqualTo("stop_loss");
    }

    @Test
    void anUnpricedPositionIsNotAnUnchangedOne() {
        // A null price means the feed could not price it. It must not read as
        // "no movement" — that would hold a stopped-out position indefinitely
        // while reporting nothing wrong.
        PositionMonitor m = monitor();
        assertThat(m.exitReason(longPosition(), null, false)).isNull();
        assertThat(m.exitReason(longPosition(), 0.0, false)).isNull();
    }

    // ── The published close ───────────────────────────────────────────────────

    @Test
    void closePublishesTheOutcomeUnderTheEntrysTradeId() {
        OpenPosition p = longPosition();
        store.tryOpen(p);

        monitor().close(p, 104.0, "take_profit");

        ArgumentCaptor<TradeOutcome> captor = ArgumentCaptor.forClass(TradeOutcome.class);
        verify(rabbitTemplate).convertAndSend(anyString(), anyString(), captor.capture());
        TradeOutcome out = captor.getValue();

        // Same id as the entry, or feedback-service's ON CONFLICT (trade_id) upsert
        // stores a second half-row instead of completing the first.
        assertThat(out.getTradeId()).isEqualTo("t1");
        assertThat(out.getExitPrice()).isEqualTo(104.0);
        assertThat(out.getFillPrice()).isEqualTo(100.0);   // entry_price is preserved
        assertThat(out.getPnl()).isEqualTo(40.0);          // (104 - 100) * 10
        assertThat(out.getOutcome()).isEqualTo("WIN");
        assertThat(out.getExitReason()).isEqualTo("take_profit");
        assertThat(out.getTimestampClose()).isNotNull();
        assertThat(out.getDurationMinutes()).isGreaterThanOrEqualTo(30);
        assertThat(store.holds("MIDHANI")).isFalse();
    }

    @Test
    void pnlPctIsAFractionNotAPercentage() {
        // trade_records.pnl_pct is a fraction everywhere else (mean |pnl_pct|
        // 0.0062 over 194 stored paper trades) and determine_outcome's WIN/LOSS
        // threshold is 0.001. Publishing 4.0 here instead of 0.04 would not fail —
        // it would feed the weight learner a 100x signal on every closed trade.
        OpenPosition p = longPosition();
        store.tryOpen(p);

        monitor().close(p, 104.0, "take_profit");

        ArgumentCaptor<TradeOutcome> captor = ArgumentCaptor.forClass(TradeOutcome.class);
        verify(rabbitTemplate).convertAndSend(anyString(), anyString(), captor.capture());
        assertThat(captor.getValue().getPnlPct()).isEqualTo(0.04);
    }

    @Test
    void closingTwiceOnlyPublishesOnce() {
        // The scheduled tick and an inbound SELL can reach the same position
        // concurrently. Only the caller that wins the remove may publish.
        OpenPosition p = longPosition();
        store.tryOpen(p);
        PositionMonitor m = monitor();

        m.close(p, 104.0, "take_profit");
        m.close(p, 98.0, "stop_loss");

        verify(rabbitTemplate).convertAndSend(anyString(), anyString(), any(TradeOutcome.class));
    }

    @Test
    void aPaperCloseNeverPlacesABrokerOrder() {
        OpenPosition p = longPosition();   // paperTrade = true
        store.tryOpen(p);

        monitor().close(p, 104.0, "take_profit");

        verifyNoInteractions(growwOrderService);
    }

    @Test
    void aLiveCloseFlattensThePositionAtTheBroker() {
        OpenPosition live = OpenPosition.builder()
                .tradeId("t9").symbol("SBIN").action("BUY").entryPrice(800).qty(5)
                .stopLoss(784).takeProfit(820).paperTrade(false).openedAt(Instant.now()).build();
        store.tryOpen(live);

        monitor().close(live, 784.0, "stop_loss");

        verify(growwOrderService).closePosition("SBIN", 5.0, "BUY");
    }

    // ── The scheduled sweep ───────────────────────────────────────────────────

    @Test
    void anEmptyStoreDoesNotCallThePriceFeed() {
        // The tick runs every 30s all day. With nothing held it must not poll a
        // Groww-backed endpoint that already 403s under load.
        monitor().tick();
        verifyNoInteractions(priceFeed);
    }

    @Test
    void classifyMatchesTheFeedbackServiceThreshold() {
        assertThat(PositionMonitor.classify(0.002)).isEqualTo("WIN");
        assertThat(PositionMonitor.classify(-0.002)).isEqualTo("LOSS");
        assertThat(PositionMonitor.classify(0.0005)).isEqualTo("BREAK_EVEN");
        assertThat(PositionMonitor.classify(-0.001)).isEqualTo("BREAK_EVEN");
    }

    @Test
    void shortPositionPnlAndLevelsAreInverted() {
        OpenPosition shortPos = OpenPosition.builder()
                .tradeId("s1").symbol("X").action("SELL").entryPrice(100).qty(10)
                .stopLoss(102).takeProfit(96).openedAt(Instant.now()).build();
        PositionMonitor m = monitor();

        assertThat(m.exitReason(shortPos, 102.0, false)).isEqualTo("stop_loss");
        assertThat(m.exitReason(shortPos, 96.0, false)).isEqualTo("take_profit");
        assertThat(m.exitReason(shortPos, 99.0, false)).isNull();
        assertThat(shortPos.pnlAt(96.0)).isEqualTo(40.0);
        assertThat(shortPos.pnlPctAt(96.0)).isEqualTo(0.04);
    }

    @Test
    void aFailedBrokerExitDoesNotLeaveTheSymbolClaimed() {
        // If the exit order throws, the position is already out of the store. It
        // stays out: re-adding it would risk a second exit order on the next tick,
        // which is worse than an unpublished close.
        OpenPosition live = OpenPosition.builder()
                .tradeId("t9").symbol("SBIN").action("BUY").entryPrice(800).qty(5)
                .stopLoss(784).takeProfit(820).paperTrade(false).openedAt(Instant.now()).build();
        store.tryOpen(live);
        org.mockito.Mockito.doThrow(new IllegalStateException("rejected"))
                .when(growwOrderService).closePosition(anyString(), anyDouble(), anyString());

        monitor().close(live, 784.0, "stop_loss");

        assertThat(store.holds("SBIN")).isFalse();
        verify(rabbitTemplate, never()).convertAndSend(anyString(), anyString(), any(TradeOutcome.class));
    }

    @Test
    void duplicateLegsOnOneSymbolAreAllTrackedAndEachClosesOnce() {
        // The first live rehydrate found five open rows across two symbols — three
        // MIDHANI legs opened inside two minutes before the dedupe guard existed.
        // A symbol-keyed store dropped three of them on the floor, leaving exactly
        // the permanently-open rows this whole change is meant to end.
        OpenPosition a = longPosition();
        OpenPosition b = OpenPosition.builder()
                .tradeId("t2").symbol("MIDHANI").action("BUY").entryPrice(101).qty(10)
                .stopLoss(99).takeProfit(105).paperTrade(true).openedAt(Instant.now()).build();
        store.restore(a);
        store.restore(b);
        assertThat(store.size()).isEqualTo(2);
        assertThat(store.symbols()).containsExactly("MIDHANI");

        // A new BUY is still refused — dedupe going forward, without losing the past.
        assertThat(store.tryOpen(longPosition())).isFalse();

        PositionMonitor m = monitor();
        m.close(a, 104.0, "take_profit");
        assertThat(store.size()).isEqualTo(1);      // b survives a's close
        m.close(b, 105.0, "take_profit");
        assertThat(store.size()).isZero();

        verify(rabbitTemplate, org.mockito.Mockito.times(2))
                .convertAndSend(anyString(), anyString(), any(TradeOutcome.class));
    }
}
