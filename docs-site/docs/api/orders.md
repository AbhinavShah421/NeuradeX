---
id: orders
title: Orders
sidebar_position: 5
---

# Orders — `/api/orders`

**File:** [`backend/app/api/orders.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/orders.py)

| Method | Path | Line | Description |
|---|---|---|---|
| `POST` | `/api/orders/` | [48](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/orders.py#L48) | Place an order via Groww API |
| `GET` | `/api/orders/` | [147](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/orders.py#L147) | List historical orders |

## Place Order — Request Body

```json
{
  "symbol": "RELIANCE",
  "quantity": 5,
  "transaction_type": "BUY",
  "order_type": "MARKET",
  "price": null,
  "product": "CNC",
  "exchange": "NSE"
}
```
