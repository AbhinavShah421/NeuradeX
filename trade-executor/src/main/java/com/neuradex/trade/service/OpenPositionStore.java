package com.neuradex.trade.service;

import com.neuradex.trade.dto.OpenPosition;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * What this executor currently holds.
 *
 * <p>Before this existed the executor had no memory between messages, which
 * produced both halves of the same bug: it opened MIDHANI three times inside two
 * minutes (twice at an identical price) because nothing said "already holding",
 * and none of the three ever closed because nothing was tracking them to close.
 *
 * <h2>Why a symbol maps to a LIST</h2>
 * The obvious shape is one position per symbol, and going forward that is the
 * rule {@link #tryOpen} enforces. But it cannot be the storage shape, because the
 * duplicates that already exist have to be closable: the first rehydrate found
 * five open rows across two symbols, and a symbol-keyed map silently dropped
 * three of them — leaving exactly the permanently-open rows this change is meant
 * to end. Refusing new duplicates and closing existing ones are different
 * questions, so they are answered by different methods over one list.
 */
@Slf4j
@Service
public class OpenPositionStore {

    private final Map<String, CopyOnWriteArrayList<OpenPosition>> bySymbol = new ConcurrentHashMap<>();

    /**
     * Claim the symbol for a NEW position. Returns false if anything is already
     * held on it — atomically, so two risk.validated messages for one symbol
     * arriving on different threads cannot both see "not held" and both open.
     */
    public boolean tryOpen(OpenPosition position) {
        String sym = key(position.getSymbol());
        boolean[] claimed = {false};
        bySymbol.compute(sym, (k, existing) -> {
            if (existing != null && !existing.isEmpty()) return existing;
            claimed[0] = true;
            CopyOnWriteArrayList<OpenPosition> list =
                    existing != null ? existing : new CopyOnWriteArrayList<>();
            list.add(position);
            return list;
        });
        return claimed[0];
    }

    /**
     * Restore a position from storage at boot. Unlike {@link #tryOpen} this
     * appends unconditionally — a duplicate that is already open in the database
     * is a fact, and refusing to track it does not make it go away.
     */
    public void restore(OpenPosition position) {
        bySymbol.computeIfAbsent(key(position.getSymbol()), k -> new CopyOnWriteArrayList<>())
                .add(position);
    }

    public boolean holds(String symbol) {
        CopyOnWriteArrayList<OpenPosition> list = bySymbol.get(key(symbol));
        return list != null && !list.isEmpty();
    }

    /** The oldest open leg on this symbol, or null. */
    public OpenPosition get(String symbol) {
        CopyOnWriteArrayList<OpenPosition> list = bySymbol.get(key(symbol));
        return list == null || list.isEmpty() ? null : list.get(0);
    }

    /** Every open leg on this symbol — a close-on-signal must flatten all of them. */
    public List<OpenPosition> forSymbol(String symbol) {
        CopyOnWriteArrayList<OpenPosition> list = bySymbol.get(key(symbol));
        return list == null ? List.of() : List.copyOf(list);
    }

    /**
     * Remove one specific leg by trade id. Returns it, or null if something else
     * already claimed it — which is what makes a double close impossible.
     */
    public OpenPosition release(String symbol, String tradeId) {
        String sym = key(symbol);
        OpenPosition[] taken = {null};
        bySymbol.computeIfPresent(sym, (k, list) -> {
            for (OpenPosition p : list) {
                if (p.getTradeId().equals(tradeId) && list.remove(p)) {
                    taken[0] = p;
                    break;
                }
            }
            return list.isEmpty() ? null : list;
        });
        return taken[0];
    }

    /** Snapshot — safe to iterate while the monitor closes positions. */
    public Collection<OpenPosition> all() {
        List<OpenPosition> out = new ArrayList<>();
        bySymbol.values().forEach(out::addAll);
        return out;
    }

    /** Distinct symbols held — what the price feed needs to ask for. */
    public List<String> symbols() {
        return List.copyOf(bySymbol.keySet());
    }

    /** Number of open LEGS, which is not the number of symbols when duplicates exist. */
    public int size() {
        return bySymbol.values().stream().mapToInt(List::size).sum();
    }

    private static String key(String symbol) {
        return symbol == null ? "" : symbol.toUpperCase();
    }
}
