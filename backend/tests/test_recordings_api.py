"""Integration tests for the Recordings API — the full create → detail → chart →
update → backtest-guard → delete lifecycle, plus auth/validation guards.

SAFETY: every recording created here is registered with the `created_ids` fixture
and deleted on teardown. These tests never list-and-delete; they only touch ids
they created themselves. (Note: the API returns snake_case — the camelCase you see
in the frontend is the axios interceptor, not the server.)
"""
import uuid

import pytest


def _name():
    return f"pytest-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
async def test_full_lifecycle(client, auth_headers, require_backend, created_ids):
    # create — symbols are deduped (case-insensitive) and uppercased
    r = await client.post("/api/recordings", headers=auth_headers,
                          json={"name": _name(), "symbols": ["reliance", "TCS", "tcs", "INFY"]})
    assert r.status_code == 200
    d = r.json()["data"]
    created_ids.append(d["id"])
    assert d["symbols"] == ["RELIANCE", "TCS", "INFY"]
    assert d["symbol_count"] == 3
    assert d["status"] in ("scheduled", "recording")
    assert d["date"] >= d["created_at"][:10]        # target day is today or later (never a past gap)

    # detail — per-symbol coverage rows + aggregate summary
    r = await client.get(f"/api/recordings/{d['id']}")
    assert r.status_code == 200
    det = r.json()["data"]
    assert len(det["coverage"]) == 3
    assert det["coverage_summary"]["symbols"] == 3

    # chart — valid symbol returns the candle payload shape
    r = await client.get(f"/api/recordings/{d['id']}/chart/RELIANCE?bar_seconds=60")
    assert r.status_code == 200
    chart = r.json()["data"]
    assert chart["symbol"] == "RELIANCE" and "candles" in chart

    # chart — symbol not in the recording → 404
    r = await client.get(f"/api/recordings/{d['id']}/chart/ZZZNOTHERE")
    assert r.status_code == 404

    # update — replace the symbol set
    r = await client.put(f"/api/recordings/{d['id']}", headers=auth_headers,
                         json={"symbols": ["HDFCBANK", "hdfcbank"]})
    assert r.status_code == 200
    assert r.json()["data"]["symbols"] == ["HDFCBANK"]

    # backtest — guarded until the target day is completed
    r = await client.post(f"/api/recordings/{d['id']}/backtest", headers=auth_headers, json={})
    assert r.status_code == 400

    # it shows up in the list
    r = await client.get("/api/recordings")
    assert any(x["id"] == d["id"] for x in r.json()["data"])

    # delete
    r = await client.delete(f"/api/recordings/{d['id']}", headers=auth_headers)
    assert r.status_code == 200
    created_ids.remove(d["id"])                     # already gone; nothing to clean up

    # gone from the list
    r = await client.get("/api/recordings")
    assert all(x["id"] != d["id"] for x in r.json()["data"])


@pytest.mark.integration
async def test_create_requires_symbols(client, auth_headers, require_backend):
    r = await client.post("/api/recordings", headers=auth_headers, json={"name": _name(), "symbols": []})
    assert r.status_code == 400


@pytest.mark.integration
async def test_create_requires_auth(client, require_backend):
    r = await client.post("/api/recordings", json={"symbols": ["RELIANCE"]})
    assert r.status_code == 401


@pytest.mark.integration
async def test_get_missing_recording_404(client, require_backend):
    r = await client.get("/api/recordings/does-not-exist-xyz")
    assert r.status_code == 404


@pytest.mark.integration
async def test_delete_requires_auth(client, require_backend):
    r = await client.delete("/api/recordings/whatever-id")
    assert r.status_code == 401
