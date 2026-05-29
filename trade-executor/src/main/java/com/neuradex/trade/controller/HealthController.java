package com.neuradex.trade.controller;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.Map;

@RestController
public class HealthController {

    @Value("${trade.paper-mode:true}")
    private boolean paperMode;

    @GetMapping("/health")
    public Map<String, Object> health() {
        return Map.of(
                "status", "ok",
                "service", "trade-executor",
                "paper_mode", paperMode,
                "timestamp", Instant.now().toString()
        );
    }
}
