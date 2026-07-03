# NeuradeX backend tests

Automated tests for the FastAPI backend, run with `pytest`.

## Layout

| File | Kind | Needs the stack? |
|---|---|---|
| `test_recordings_unit.py` | unit — recording target-date / status / symbol-cleaning / view shape | no |
| `test_candle_store.py` | unit — tick-store append/read/resample + `day_coverage` (temp dir) | no |
| `test_api_smoke.py` | integration — one read endpoint per router answers `< 400` | yes |
| `test_recordings_api.py` | integration — recordings CRUD lifecycle + auth/validation guards | yes |

Unit tests import app modules and run pure logic. Integration tests hit the
running backend over HTTP; they **auto-skip** (via the `require_backend` fixture)
when the API is unreachable, so `-m "not integration"` isn't required on a bare
checkout.

Integration tests only ever delete recordings they created themselves (tracked by
the `created_ids` fixture) — they never list-and-delete, so they can't disturb
real recordings.

## Running

From the repo root, against the live Docker stack:

```powershell
.\scripts\run-tests.ps1                        # unit + integration
.\scripts\run-tests.ps1 -m "not integration"   # unit only
.\scripts\run-tests.ps1 -k recordings          # just the recordings tests
.\scripts\run-tests.ps1 -v                      # verbose
```

The script copies this folder into the backend container and runs pytest there
(the app and its deps — `pytest`, `pytest-asyncio`, `httpx`, `pyjwt` — already
live in that image). Extra args pass straight through to pytest.

To run by hand inside a shell that already has the app importable:

```bash
NEURADEX_API=http://localhost:8000 python -m pytest /app/tests
```

`NEURADEX_API` overrides the backend base URL (default `http://localhost:8000`).

## Config

`pytest.ini` sets `asyncio_mode = auto` (async tests need no per-function marker)
and registers the `integration` marker.
