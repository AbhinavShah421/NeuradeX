"""The boot-time DNS race in the NSE universe fetch.

About one cold start in three, the scanner asked for the equity list before the
container's resolver was up, fell back to the ~304-name directory, and swept a
sixth of the market for 10-30 minutes. The fix waits out a network failure — and
must NOT wait when a server actually answered, because then the network is fine
and a delay only postpones the fallback.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import scanner as S

CSV = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\n"
    "RELIANCE,Reliance Industries Limited,EQ,29-NOV-1995\n"
    "SBIN,State Bank of India,EQ,01-MAR-1995\n"
    "ODDCO,Odd Company Limited,BE,01-JAN-2020\n"
)
URLS = ["https://host-a.test/EQUITY_L.csv", "https://host-b.test/EQUITY_L.csv"]


def dns_error():
    return S.httpx.ConnectError("[Errno -2] Name or service not known")


@pytest.fixture
def harness(monkeypatch):
    """Scripted HTTP client + recorded sleeps. `script(n, url)` decides call n."""
    monkeypatch.setattr(S, "NSE_EQUITY_LIST_URLS", URLS)
    monkeypatch.setattr(S, "UNIVERSE_NET_RETRIES", 3)
    monkeypatch.setattr(S, "UNIVERSE_NET_BACKOFF", 5.0)

    sleeps, calls = [], []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(S, "_universe_sleep", fake_sleep)

    def install(script):
        class _Resp:
            def __init__(self, status, text):
                self.status_code, self.text = status, text

            def raise_for_status(self):
                if self.status_code >= 400:
                    req = S.httpx.Request("GET", "https://host.test")
                    raise S.httpx.HTTPStatusError(
                        "server error", request=req,
                        response=S.httpx.Response(self.status_code, request=req))

        class _Client:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, headers=None):
                calls.append(url)
                outcome = script(len(calls), url)
                if isinstance(outcome, Exception):
                    raise outcome
                return _Resp(*outcome)

        monkeypatch.setattr(S.httpx, "AsyncClient", _Client)

    return install, sleeps, calls


def run():
    return asyncio.run(S._fetch_nse_equity_universe())


def test_dns_race_at_boot_is_ridden_out_instead_of_falling_back(harness):
    install, sleeps, calls = harness
    # Two full rounds of DNS failure on both hosts, then the resolver is up.
    install(lambda n, url: dns_error() if n <= 4 else (200, CSV))

    uni = run()

    assert set(uni) == {"RELIANCE", "SBIN"}, "the full universe, not the fallback"
    assert sleeps == [5.0, 10.0], "backed off twice, doubling"


def test_a_server_that_answered_is_not_waited_on(harness):
    """An HTTP error means DNS and the network work. Waiting would only delay
    the directory fallback for nothing."""
    install, sleeps, calls = harness
    install(lambda n, url: (503, ""))

    with pytest.raises(S.httpx.HTTPStatusError):
        run()

    assert sleeps == []
    assert len(calls) == 2, "one round across both hosts, no retry"


def test_gives_up_after_the_retry_budget_so_the_fallback_still_runs(harness):
    """A resolver that never comes back must not hang the sweep — the caller's
    directory fallback and short degraded TTL are still the backstop."""
    install, sleeps, calls = harness
    install(lambda n, url: dns_error())

    with pytest.raises(S.httpx.ConnectError):
        run()

    assert sleeps == [5.0, 10.0, 20.0]
    assert len(calls) == 2 * (3 + 1)


def test_second_host_rescues_the_round_without_any_wait(harness):
    install, sleeps, calls = harness
    install(lambda n, url: dns_error() if url == URLS[0] else (200, CSV))

    uni = run()

    assert set(uni) == {"RELIANCE", "SBIN"}
    assert sleeps == []


def test_an_empty_200_is_not_mistaken_for_a_network_failure(harness):
    """A host that answered with nothing reached a server. Treating it like DNS
    would stall the sweep for 35s on a response that retrying will not change."""
    install, sleeps, calls = harness
    install(lambda n, url: (200, ""))

    assert run() == {}
    assert sleeps == []


def test_only_eq_series_is_kept(harness):
    """Unchanged behaviour: BE/other series are not intraday-eligible."""
    install, sleeps, calls = harness
    install(lambda n, url: (200, CSV))

    uni = run()

    assert "ODDCO" not in uni
    assert uni["RELIANCE"] == "Reliance Industries Limited"
