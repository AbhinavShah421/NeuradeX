"""Shared fixtures for the NeuradeX backend test suite.

Two kinds of tests live here:
  • unit tests — import app modules and exercise pure logic (no network, no stack).
  • integration tests — hit the running backend over HTTP. They depend on the
    `require_backend` fixture, which auto-skips them when the API is unreachable,
    so the unit tests still run on a bare checkout.

Run everything against the live stack with `scripts/run-tests.ps1` (copies this
dir into the backend container and runs pytest there, where the app + its deps
already live).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import httpx
import jwt
import pytest
import pytest_asyncio

BASE_URL = os.getenv("NEURADEX_API", "http://localhost:8000")


def _make_token() -> str:
    """Mint a valid JWT the same way the auth layer does, signed with the app's
    own secret (imported from settings — works inside the backend container)."""
    from app.config import settings
    payload = {
        "sub": "test-suite", "email": "tests@neuradex.local", "broker": "groww",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {_make_token()}"}


@pytest.fixture(scope="session")
def backend_up() -> bool:
    try:
        return httpx.get(BASE_URL + "/health", timeout=5).status_code < 500
    except Exception:
        return False


@pytest.fixture
def require_backend(backend_up):
    if not backend_up:
        pytest.skip(f"backend not reachable at {BASE_URL}")


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        yield c


@pytest_asyncio.fixture
async def created_ids(client, auth_headers):
    """Collect recording ids created during a test and delete ONLY those on
    teardown. Tests must append every id they create here so nothing leaks — and
    so the suite never touches recordings it didn't create."""
    ids: list[str] = []
    yield ids
    for rid in ids:
        try:
            await client.delete(f"/api/recordings/{rid}", headers=auth_headers)
        except Exception:
            pass
