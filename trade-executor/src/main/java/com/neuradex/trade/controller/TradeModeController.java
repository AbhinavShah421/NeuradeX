package com.neuradex.trade.controller;

import com.neuradex.trade.config.TradeModeConfig;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.time.Instant;
import java.util.Map;

@RestController
@RequestMapping("/mode")
@RequiredArgsConstructor
public class TradeModeController {

    private final TradeModeConfig tradeModeConfig;

    @GetMapping
    public Map<String, Object> getMode() {
        return Map.of(
            "paper_mode", tradeModeConfig.isPaperMode(),
            "timestamp", Instant.now().toString()
        );
    }

    @PostMapping
    public Map<String, Object> setMode(@RequestParam boolean paper) {
        boolean previous = tradeModeConfig.setPaperMode(paper);
        return Map.of(
            "paper_mode", paper,
            "previous", previous,
            "timestamp", Instant.now().toString()
        );
    }
}
