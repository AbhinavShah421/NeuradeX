---
id: portfolio
title: Portfolio
sidebar_position: 4
---

# Portfolio — `/api/portfolio`

**File:** [`backend/app/api/portfolio.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/portfolio.py)

| Method | Path | Line | Description |
|---|---|---|---|
| `GET` | `/api/portfolio/` | [95](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/portfolio.py#L95) | User's holdings from Groww API |
| `POST` | `/api/portfolio/add` | [158](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/portfolio.py#L158) | Manually add a holding |
| `GET` | `/api/portfolio/performance` | [174](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/portfolio.py#L174) | P&L and performance metrics |
| `GET` | `/api/portfolio/alerts` | [193](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/portfolio.py#L193) | Active price / pattern alerts |
| `POST` | `/api/portfolio/alerts` | [207](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/portfolio.py#L207) | Create a new alert |

**External call:** Groww broker API via [`backend/app/utils/groww_client.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/utils/groww_client.py) for live holdings.  
**Alerts storage:** MongoDB `alerts` collection.
