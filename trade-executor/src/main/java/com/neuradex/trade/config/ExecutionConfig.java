package com.neuradex.trade.config;

import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.web.client.RestTemplate;

import java.time.Duration;

/**
 * Turns on the scheduler that PositionMonitor runs under, and supplies the one
 * RestTemplate used for both the price poll and Groww order placement.
 *
 * <p>The timeouts are the point. A default RestTemplate has none, so a broker or
 * backend that accepts the connection and then stops responding blocks the
 * scheduler thread indefinitely — and the thread it blocks is the one that closes
 * positions. Every held position would sit through its stop while the executor
 * looked healthy.
 */
@Configuration
@EnableScheduling
public class ExecutionConfig {

    @Bean
    public RestTemplate restTemplate(RestTemplateBuilder builder) {
        return builder
                .setConnectTimeout(Duration.ofSeconds(5))
                .setReadTimeout(Duration.ofSeconds(10))
                .build();
    }
}
