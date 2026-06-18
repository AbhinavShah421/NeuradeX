---
id: orders
title: Orders
sidebar_position: 5
---

# Orders — `/api/orders`

**File:** [`backend/app/api/orders.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/orders.py)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/orders/` | Place an order via Groww API |
| `GET` | `/api/orders/` | **Live Groww order book** (today's orders, real status) — used by the Portfolio "Today's Orders" panel for cancel + Groww↔app sync |
| `POST` | `/api/orders/cancel` | Cancel a pending order by `{ "order_id", "segment" }` |

## Place Order — Request Body

```json
{
  "symbol": "RELIANCE",
  "quantity": 5,
  "transaction_type": "BUY",
  "order_type": "LIMIT",
  "price": 1402.5,
  "product": "CNC",
  "exchange": "NSE"
}
```

The client always sends Groww's required **`validity` (DAY)** and a unique
**`order_reference_id`** (both were missing before and caused `GA001`
rejections). Order failures surface Groww's actual error message verbatim.
The Portfolio AI flows place **LIMIT** orders with a protective price collar;
`MARKET` is still supported (omit `price`).
