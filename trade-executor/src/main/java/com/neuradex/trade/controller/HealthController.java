package com.neuradex.trade.controller;

import com.neuradex.trade.config.TradeModeConfig;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.Map;

@RestController
@RequiredArgsConstructor
public class HealthController {

    private final TradeModeConfig tradeModeConfig;

    @GetMapping("/health")
    public Map<String, Object> health() {
        return Map.of(
                "status", "ok",
                "service", "trade-executor",
                "paper_mode", tradeModeConfig.isPaperMode(),
                "timestamp", Instant.now().toString()
        );
    }
}
