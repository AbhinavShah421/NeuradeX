package com.neuradex.risk.config;

import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

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
}
