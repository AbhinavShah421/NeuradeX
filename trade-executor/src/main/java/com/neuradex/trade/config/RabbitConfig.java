package com.neuradex.trade.config;

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
}
