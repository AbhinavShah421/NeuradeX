---
id: system
title: System / Docker Control
sidebar_position: 11
---

# System / Docker Control — `/api/system`

**File:** [`backend/app/api/system.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/system.py)

Powers the frontend's floating **system-status panel**. Talks to the Docker Engine
API over the mounted unix socket (`/var/run/docker.sock`) using an httpx UDS
transport — no extra Python package. Scoped to this project's containers only
(name prefix `stock-prediction-`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/system/services` | List every project container with `state`, `status`, `health`, live `cpu_pct` / `mem_used_mb`, and recent-log `log_severity`. Returns aggregate `totals`. |
| `POST` | `/api/system/services/{name}/{action}` | `action` ∈ `start` \| `stop` \| `restart`. Refuses to act on `stock-prediction-backend` (serves the request). |
| `POST` | `/api/system/restart-all` | Restart all running project containers in parallel (skips the backend). |
| `GET` | `/api/system/services/{name}/logs?tail=N` | Interactive HTML log viewer (new tab) — search, level chips, time range, tail size, auto-refresh, clear, delete. |
| `DELETE` | `/api/system/services/{name}/logs` | Truncate the container's json-log file(s) to zero bytes (non-disruptive clear). |

## `GET /services` response

```json
{
  "status": "success",
  "data": [
    {
      "name": "stock-prediction-backend",
      "label": "Backend",
      "icon": "dns",
      "state": "running",
      "status": "Up 7 minutes (healthy)",
      "health": "healthy",
      "running": true,
      "cpu_pct": 8.9,
      "mem_used_mb": 174.7,
      "log_severity": "ok"
    }
  ],
  "totals": { "running": 29, "count": 29, "cpu_pct": 56.3, "mem_used_mb": 4531.9 }
}
```

- `log_severity`: `"error"` (red) if the recent log tail contains `ERROR`/`CRITICAL`/`FATAL`, else `"warning"` (amber) for `WARN`/`WARNING`, else `"ok"` (green).
- **Stale-while-revalidate:** once warm, the endpoint serves a cached snapshot instantly and refreshes in the background — the ~6s Docker stats sweep never blocks the UI. `?fresh=true` forces a synchronous recompute (manual Refresh). `?stats=false` returns the container list only (fast, no enrichment).

## docker-compose requirements (`backend` service)

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock              # Engine API: list/logs/control/stats
  - /var/lib/docker/containers:/var/lib/docker/containers   # truncate json-logs (Delete logs)
```

## Security

These routes are **unauthenticated** (same posture as `/api/agent/services/health`)
and the Docker socket is root-equivalent on the host. Intended for the operator's own
local / personal deployment, not a shared public instance.
