package com.neuradex.trade.config;

import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.rabbit.retry.MessageRecoverer;
import org.springframework.amqp.rabbit.retry.RepublishMessageRecoverer;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * NOTE on dead-lettering strategy: "risk.validated" is also declared (as a plain
 * durable queue, no arguments) both by risk-engine's own RabbitConfig (publisher side)
 * and by market-data-service's rabbitmq_setup.py, which owns the platform-wide
 * topology. RabbitMQ requires every declaration of a given queue name to use identical
 * arguments, so we deliberately do NOT attach native x-dead-letter-exchange/
 * x-dead-letter-routing-key arguments to that existing queue here — doing so would make
 * this service's declaration disagree with the others and blow up the channel with
 * PRECONDITION_FAILED on connect. Instead we use Spring AMQP's listener-level retry
 * (spring.rabbitmq.listener.simple.retry.* in application.properties) plus a
 * RepublishMessageRecoverer that republishes exhausted messages to a brand-new DLX/DLQ
 * pair that only this service owns.
 */
@Configuration
public class RabbitConfig {

    @Bean
    public Jackson2JsonMessageConverter jsonConverter() {
        Jackson2JsonMessageConverter converter = new Jackson2JsonMessageConverter();
        converter.setAlwaysConvertToInferredType(true);
        return converter;
    }

    @Bean
    public RabbitTemplate rabbitTemplate(ConnectionFactory cf) {
        RabbitTemplate tpl = new RabbitTemplate(cf);
        tpl.setMessageConverter(jsonConverter());
        return tpl;
    }

    @Bean
    public DirectExchange riskValidatedExchange() {
        return new DirectExchange("risk.validated", true, false);
    }

    @Bean
    public FanoutExchange tradeOutcomesExchange() {
        return new FanoutExchange("trade.outcomes", true, false);
    }

    @Bean
    public Queue riskValidatedQueue() {
        return QueueBuilder.durable("risk.validated").build();
    }

    @Bean
    public Queue tradeOutcomesQueue() {
        return QueueBuilder.durable("trade.outcomes.feedback").build();
    }

    @Bean
    public Binding riskValidatedBinding() {
        return BindingBuilder
                .bind(riskValidatedQueue())
                .to(riskValidatedExchange())
                .with("validated");
    }

    @Bean
    public Binding tradeOutcomesBinding() {
        return BindingBuilder
                .bind(tradeOutcomesQueue())
                .to(tradeOutcomesExchange());
    }

    // ── Dead-letter topology for risk.validated ────────────────────────────────
    // New exchange/queue names owned solely by this service — safe to declare
    // with whatever arguments we like since nothing else declares them.

    @Bean
    public DirectExchange riskValidatedDlx() {
        return new DirectExchange("risk.validated.dlx", true, false);
    }

    @Bean
    public Queue riskValidatedDlq() {
        return QueueBuilder.durable("risk.validated.dlq").build();
    }

    @Bean
    public Binding riskValidatedDlqBinding() {
        return BindingBuilder
                .bind(riskValidatedDlq())
                .to(riskValidatedDlx())
                .with("risk.validated.dlq");
    }

    /**
     * Wired automatically into the retry interceptor that Spring Boot builds from
     * spring.rabbitmq.listener.simple.retry.* — once a message (order placement,
     * paper trade, etc.) exhausts its retry attempts, it is republished here (with
     * x-exception-message/x-original-exchange headers attached) instead of being
     * rejected and lost.
     */
    @Bean
    public MessageRecoverer riskValidatedMessageRecoverer(RabbitTemplate rabbitTemplate) {
        return new RepublishMessageRecoverer(rabbitTemplate, "risk.validated.dlx", "risk.validated.dlq");
    }
}
