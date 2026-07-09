package com.neuradex.risk.config;

import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.config.SimpleRabbitListenerContainerFactory;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.rabbit.retry.MessageRecoverer;
import org.springframework.amqp.rabbit.retry.RepublishMessageRecoverer;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.SimpleMessageConverter;
import org.springframework.boot.autoconfigure.amqp.SimpleRabbitListenerContainerFactoryConfigurer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * NOTE on dead-lettering strategy: "ensemble.decision" and "risk.validated" are also
 * declared (as plain durable queues, no arguments) by market-data-service's
 * rabbitmq_setup.py, which owns the platform-wide topology. RabbitMQ requires every
 * declaration of a given queue name to use identical arguments, so we deliberately do
 * NOT attach native x-dead-letter-exchange/x-dead-letter-routing-key arguments to those
 * existing queues here — doing so would make this service's declaration disagree with
 * market-data-service's and blow up the channel with PRECONDITION_FAILED on connect.
 * Instead we use Spring AMQP's listener-level retry (spring.rabbitmq.listener.simple.retry.*
 * in application.properties) plus a RepublishMessageRecoverer that republishes
 * exhausted messages to a brand-new DLX/DLQ pair that only this service owns.
 */
@Configuration
public class RabbitConfig {

    @Bean
    public Jackson2JsonMessageConverter jsonConverter() {
        return new Jackson2JsonMessageConverter();
    }

    @Bean
    public RabbitTemplate rabbitTemplate(ConnectionFactory cf) {
        RabbitTemplate tpl = new RabbitTemplate(cf);
        tpl.setMessageConverter(jsonConverter());
        return tpl;
    }

    /**
     * Listener container factory with an explicit SimpleMessageConverter (raw
     * byte[] passthrough). WHY: Spring Boot auto-adopts the single
     * Jackson2JsonMessageConverter bean (defined above for the outbound
     * RabbitTemplate) into the listener side too. The @RabbitListener consumer
     * takes byte[] and parses JSON by hand, so with the Jackson converter every
     * application/json message failed with "Cannot deserialize [B from Object
     * value" — 2,940 conversion failures in 4h, each retried and dead-lettered,
     * so the risk engine validated nothing. This factory keeps inbound bytes
     * raw while the template keeps JSON for outbound RiskValidated. The
     * configurer still applies all spring.rabbitmq.listener.simple.* properties
     * (ack mode, prefetch, the bounded retry interceptor).
     */
    @Bean
    public SimpleRabbitListenerContainerFactory rabbitListenerContainerFactory(
            SimpleRabbitListenerContainerFactoryConfigurer configurer, ConnectionFactory cf) {
        SimpleRabbitListenerContainerFactory factory = new SimpleRabbitListenerContainerFactory();
        configurer.configure(factory, cf);
        factory.setMessageConverter(new SimpleMessageConverter());
        return factory;
    }

    @Bean
    public DirectExchange ensembleDecisionExchange() {
        return new DirectExchange("ensemble.decision", true, false);
    }

    @Bean
    public DirectExchange riskValidatedExchange() {
        return new DirectExchange("risk.validated", true, false);
    }

    @Bean
    public Queue ensembleDecisionQueue() {
        return QueueBuilder.durable("ensemble.decision").build();
    }

    @Bean
    public Queue riskValidatedQueue() {
        return QueueBuilder.durable("risk.validated").build();
    }

    @Bean
    public Binding ensembleDecisionBinding() {
        return BindingBuilder
                .bind(ensembleDecisionQueue())
                .to(ensembleDecisionExchange())
                .with("decision");
    }

    @Bean
    public Binding riskValidatedBinding() {
        return BindingBuilder
                .bind(riskValidatedQueue())
                .to(riskValidatedExchange())
                .with("validated");
    }

    // ── Dead-letter topology for ensemble.decision ────────────────────────────
    // New exchange/queue names owned solely by this service — safe to declare
    // with whatever arguments we like since nothing else declares them.

    @Bean
    public DirectExchange ensembleDecisionDlx() {
        return new DirectExchange("ensemble.decision.dlx", true, false);
    }

    @Bean
    public Queue ensembleDecisionDlq() {
        return QueueBuilder.durable("ensemble.decision.dlq").build();
    }

    @Bean
    public Binding ensembleDecisionDlqBinding() {
        return BindingBuilder
                .bind(ensembleDecisionDlq())
                .to(ensembleDecisionDlx())
                .with("ensemble.decision.dlq");
    }

    /**
     * Wired automatically into the retry interceptor that Spring Boot builds from
     * spring.rabbitmq.listener.simple.retry.* — once a message exhausts its retry
     * attempts, it is republished here (with x-exception-message/x-original-exchange
     * headers attached) instead of being silently acked and dropped.
     */
    @Bean
    public MessageRecoverer ensembleDecisionMessageRecoverer(RabbitTemplate rabbitTemplate) {
        return new RepublishMessageRecoverer(rabbitTemplate, "ensemble.decision.dlx", "ensemble.decision.dlq");
    }
}
