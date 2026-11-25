"""A failed Docker sweep must read as UNKNOWN, never as 26 containers down.

2026-11-24: one timed-out sweep right after a backend restart made the System
Map report every running container critical "not running (None)".
"""
from app.api.monitor import _faults


def _node(running, state="running"):
    return {"id": "x", "label": "X", "layer": "core", "container": "stock-prediction-x",
            "running": running, "state": state, "probe_ok": None, "log_severity": "ok"}


def test_unknown_container_state_is_not_a_down_fault():
    faults = _faults([_node(None, "unknown")], [], [])
    assert not [f for f in faults if f.get("kind") == "container"]


def test_stopped_container_is_still_critical():
    faults = _faults([_node(False, "exited")], [], [])
    down = [f for f in faults if f.get("kind") == "container"]
    assert len(down) == 1 and down[0]["severity"] == "critical"
    assert "not running (exited)" in down[0]["message"]


def test_container_missing_from_a_good_sweep_is_critical():
    faults = _faults([_node(False, "missing")], [], [])
    assert any(f.get("kind") == "container" for f in faults)


def test_running_container_has_no_fault():
    assert not [f for f in _faults([_node(True)], [], []) if f.get("kind") == "container"]


def test_http_probe_retries_once_before_failing(monkeypatch):
    import asyncio
    import httpx
    from app.api import monitor
    calls = {"n": 0}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("slow")
            return type("R", (), {"status_code": 200})()

    async def no_sleep(_): return None
    monkeypatch.setattr(monitor.httpx, "AsyncClient", Client)
    monkeypatch.setattr(monitor.asyncio, "sleep", no_sleep)
    out = asyncio.run(monitor._probe_http("http://x/health"))
    assert out["ok"] and calls["n"] == 2


def test_http_probe_fails_when_down_twice(monkeypatch):
    import asyncio
    import httpx
    from app.api import monitor

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): raise httpx.ConnectError("down")

    async def no_sleep(_): return None
    monkeypatch.setattr(monitor.httpx, "AsyncClient", Client)
    monkeypatch.setattr(monitor.asyncio, "sleep", no_sleep)
    out = asyncio.run(monitor._probe_http("http://x/health"))
    assert not out["ok"] and "twice" in out["detail"]
