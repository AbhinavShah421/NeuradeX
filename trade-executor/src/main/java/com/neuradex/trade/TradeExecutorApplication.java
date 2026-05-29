package com.neuradex.trade;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.retry.annotation.EnableRetry;

@SpringBootApplication
@EnableRetry
public class TradeExecutorApplication {
    public static void main(String[] args) {
        SpringApplication.run(TradeExecutorApplication.class, args);
    }
}
