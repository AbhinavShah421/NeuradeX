package com.neuradex.trade.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;

import java.util.concurrent.atomic.AtomicBoolean;

@Configuration
public class TradeModeConfig {

    private final AtomicBoolean paperMode;

    public TradeModeConfig(@Value("${trade.paper-mode:true}") boolean initial) {
        this.paperMode = new AtomicBoolean(initial);
    }

    public boolean isPaperMode() {
        return paperMode.get();
    }

    public boolean setPaperMode(boolean value) {
        return paperMode.getAndSet(value);
    }
}
