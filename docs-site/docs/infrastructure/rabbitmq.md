---
id: rabbitmq
title: RabbitMQ Topology
sidebar_position: 1
---

# RabbitMQ Topology

**Setup file:** [`market-data-service/app/services/rabbitmq_setup.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py)  
**Management UI:** http://localhost:15672 (guest / guest)

## Exchanges

| Exchange | Type | Line | Purpose |
|---|---|---|---|
| `market.data` | `fanout` | [11](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L11) | Broadcast price tick to all 5 agents |
| `agent.signals` | `direct` | [12](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L12) | Route each agent's signal by routing_key |
| `ensemble.decision` | `direct` | [13](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L13) | Send ensemble decision to risk-engine |
| `risk.validated` | `direct` | [14](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L14) | Send validated decision to trade-executor |
| `trade.orders` | `direct` | [15](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L15) | Internal order routing |
| `trade.outcomes` | `fanout` | [16](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L16) | Broadcast trade result to feedback + rl-agent |
| `model.retrain` | `direct` | [17](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L17) | Trigger model retraining |
| `notifications` | `fanout` | [18](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L18) | System notifications (news events, etc.) |

## Queue Bindings

| Queue | Exchange | Routing Key | Consumer | Line |
|---|---|---|---|---|
| `market.data.technical` | `market.data` | _(fanout)_ | technical-agent | [24](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L24) |
| `market.data.sentiment` | `market.data` | _(fanout)_ | sentiment-agent | [25](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L25) |
| `market.data.macro` | `market.data` | _(fanout)_ | macro-agent | [26](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L26) |
| `market.data.pattern` | `market.data` | _(fanout)_ | pattern-agent | [27](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L27) |
| `market.data.rl` | `market.data` | _(fanout)_ | rl-agent | [28](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L28) |
| `agent.signals` | `agent.signals` | `technical` | ensemble-engine | [30](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L30) |
| `agent.signals` | `agent.signals` | `sentiment` | ensemble-engine | [31](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L31) |
| `agent.signals` | `agent.signals` | `macro` | ensemble-engine | [32](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L32) |
| `agent.signals` | `agent.signals` | `pattern` | ensemble-engine | [33](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L33) |
| `agent.signals` | `agent.signals` | `rl` | ensemble-engine | [34](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L34) |
| `ensemble.decision` | `ensemble.decision` | `decision` | risk-engine | [36](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L36) |
| `risk.validated` | `risk.validated` | `validated` | trade-executor | [37](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L37) |
| `trade.orders` | `trade.orders` | `order` | trade-executor | [38](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L38) |
| `trade.outcomes.feedback` | `trade.outcomes` | _(fanout)_ | feedback-service | [40](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L40) |
| `trade.outcomes.rl` | `trade.outcomes` | _(fanout)_ | rl-agent | [41](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L41) |
| `model.retrain` | `model.retrain` | `retrain` | model-trainer | [43](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L43) |
| `notifications.all` | `notifications` | _(fanout)_ | UI / dashboards | [44](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_setup.py#L44) |
