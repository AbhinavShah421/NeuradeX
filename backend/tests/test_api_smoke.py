"""Integration smoke tests: one representative read endpoint per router should
answer without a server error. Auto-skips if the backend isn't running.
"""
import pytest

READ_ENDPOINTS = [
    "/health",
    "/api/auth/me",
    "/api/auth/groww/status",
    "/api/stocks/",
    "/api/stocks/directory/list?q=REL&limit=5",   # the stock-picker's data source
    "/api/predictions/accuracy/stats",
    "/api/portfolio/",
    "/api/portfolio/health",
    "/api/risk/var",
    "/api/orders/",
    "/api/agent/stocks",
    "/api/agent/services/health",
    "/api/backtest/strategies",
    "/api/backtest/providers",
    "/api/paper-trading/status",
    "/api/ai-engine/scan-status",
    "/api/ai-engine/trade-gate",
    "/api/ai-engine/watchlist",
    "/api/ai-engine/regime-detail",
    "/api/ai-engine/llm-status",
    "/api/ai-engine/memory/stats",
    "/api/sessions",
    "/api/sessions/statuses",
    "/api/sessions/paper-config",
    "/api/settings/providers",
    "/api/mutual-funds/all",
    "/api/delivery-paper/portfolios",
    "/api/live-trading/status",
    "/api/system/candles/coverage",
    "/api/recordings",
]


@pytest.mark.integration
@pytest.mark.parametrize("path", READ_ENDPOINTS)
async def test_read_endpoint_ok(client, auth_headers, require_backend, path):
    r = await client.get(path, headers=auth_headers)
    assert r.status_code < 400, f"{path} -> {r.status_code}: {r.text[:200]}"


@pytest.mark.integration
async def test_directory_list_returns_items(client, auth_headers, require_backend):
    r = await client.get("/api/stocks/directory/list?q=RELIANCE&limit=5", headers=auth_headers)
    assert r.status_code == 200
    data = r.json().get("data", [])
    assert isinstance(data, list) and len(data) >= 1
    assert "symbol" in data[0]


@pytest.mark.integration
async def test_candles_coverage_shape(client, auth_headers, require_backend):
    r = await client.get("/api/system/candles/coverage", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "data" in body and "summary" in body
    for key in ("symbols", "days", "total_ticks", "total_bytes"):
        assert key in body["summary"]
