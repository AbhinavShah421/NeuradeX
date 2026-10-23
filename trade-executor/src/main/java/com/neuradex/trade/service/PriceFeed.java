package com.neuradex.trade.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Last-traded prices for the symbols this executor is holding.
 *
 * <p>Deliberately points at {@code POST /api/stocks/ltp} and NOT at
 * {@code /api/stocks/directory/prices}, which looks like the same endpoint and
 * is not: when Groww does not answer, that one returns a <em>simulated</em>
 * price so the directory grid has something to render. A placeholder is the
 * right answer for a table of names and a catastrophic one here — this feed
 * decides when a real position is stopped out. The strict endpoint omits any
 * symbol it cannot genuinely price, so a missing key means "unknown", never
 * "unchanged".
 */
@Slf4j
@Service
public class PriceFeed {

    private final RestTemplate restTemplate;
    private final String backendUrl;

    public PriceFeed(RestTemplate restTemplate,
                     @Value("${backend.url:http://backend:8000}") String backendUrl) {
        this.restTemplate = restTemplate;
        this.backendUrl = backendUrl.replaceAll("/+$", "");
    }

    /**
     * @return symbol → price, containing ONLY symbols that were really priced.
     *         An empty map means the feed is unavailable, which callers must treat
     *         as "do nothing", never as "no movement".
     */
    @SuppressWarnings("unchecked")
    public Map<String, Double> ltp(List<String> symbols) {
        if (symbols == null || symbols.isEmpty()) return Collections.emptyMap();

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        HttpEntity<Map<String, Object>> req =
                new HttpEntity<>(Map.of("symbols", symbols), headers);

        try {
            ResponseEntity<Map> resp = restTemplate.postForEntity(
                    backendUrl + "/api/stocks/ltp", req, Map.class);

            if (!resp.getStatusCode().is2xxSuccessful() || resp.getBody() == null) {
                log.warn("Price feed returned {} for {} symbols", resp.getStatusCode(), symbols.size());
                return Collections.emptyMap();
            }
            Object raw = resp.getBody().get("prices");
            if (!(raw instanceof Map)) return Collections.emptyMap();

            Map<String, Double> out = new HashMap<>();
            ((Map<String, Object>) raw).forEach((sym, val) -> {
                if (val == null) return;
                try {
                    double p = Double.parseDouble(val.toString());
                    // A non-positive or non-finite price is not a price. Letting one
                    // through would compare a stop against garbage and close the
                    // position at it.
                    if (p > 0 && Double.isFinite(p)) out.put(sym.toUpperCase(), p);
                } catch (NumberFormatException ignored) {
                    log.warn("Unparseable price for {}: {}", sym, val);
                }
            });
            if (out.size() < symbols.size()) {
                log.debug("Priced {}/{} held symbols", out.size(), symbols.size());
            }
            return out;
        } catch (Exception e) {
            log.warn("Price feed unavailable ({}) — positions left untouched this tick", e.getMessage());
            return Collections.emptyMap();
        }
    }
}
