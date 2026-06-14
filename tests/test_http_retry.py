"""E7 — shared retry/backoff + Retry-After in sources/http.py.

The schedule fetch loops terms with NO retry, so a single transient 5xx nukes the
whole fetch. These tests pin the shared retry helper get_json_retrying: it retries
ONLY transient failures (429/5xx + transport), honors Retry-After when present,
else uses jittered exponential backoff, and never retries a hard 4xx or a
JSON/schema-drift (SourceDataError). sleep + rand are injected so the tests are
fast and deterministic.
"""
import httpx
import pytest

from sources import http as H
from sources.http import SourceDataError, SourceHTTPError, get_json_retrying


class _SeqClient:
    """A fake httpx client whose .get() replays a sequence of responses/raises."""
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def get(self, url, params=None, headers=None):
        item = self.seq[self.calls]
        self.calls += 1
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        pass


def _resp(payload=None, *, status=200):
    from tests.conftest import FakeResponse
    return FakeResponse(payload, status_code=status, text=("" if status >= 400 else None)
                        if payload is None else None)


def _sleeps():
    rec = []
    return rec, (lambda s: rec.append(s))


# ------------------------------------------------------------- retry happy path
def test_retries_transient_5xx_then_succeeds():
    client = _SeqClient([_resp(status=503), _resp(status=502),
                         _resp({"ok": True})])
    rec, sleep = _sleeps()
    out = get_json_retrying("u", client=client, source="S", sleep=sleep,
                            rand=lambda: 0.0)
    assert out == {"ok": True}
    assert client.calls == 3        # 2 failures + 1 success
    assert len(rec) == 2            # slept before each retry


def test_gives_up_after_max_retries_and_raises_clean():
    client = _SeqClient([_resp(status=503)] * 10)
    rec, sleep = _sleeps()
    with pytest.raises(SourceHTTPError):
        get_json_retrying("u", client=client, source="S", max_retries=3,
                          sleep=sleep, rand=lambda: 0.0)
    assert client.calls == 4        # initial + 3 retries
    assert len(rec) == 3


# ------------------------------------------------------------- non-retryable
def test_does_not_retry_hard_4xx():
    client = _SeqClient([_resp(status=404), _resp({"ok": True})])
    rec, sleep = _sleeps()
    with pytest.raises(SourceHTTPError):
        get_json_retrying("u", client=client, source="S", sleep=sleep)
    assert client.calls == 1 and rec == []   # 404 is a hard client error


def test_does_not_retry_schema_drift():
    # 200 but a non-JSON body -> SourceDataError -> never retried (retrying can't
    # fix bad data).
    client = _SeqClient([_resp(status=200)])   # empty body, status 200
    with pytest.raises(SourceDataError):
        get_json_retrying("u", client=client, source="S", sleep=lambda s: None)
    assert client.calls == 1


# ------------------------------------------------------------- Retry-After
def _http_status_error(status, *, retry_after=None):
    req = httpx.Request("GET", "https://x/")
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    resp = httpx.Response(status, headers=headers, request=req)
    return httpx.HTTPStatusError(f"{status}", request=req, response=resp)


def test_retry_after_integer_seconds_is_honored():
    exc = SourceHTTPError("429")
    exc.__cause__ = _http_status_error(429, retry_after="7")
    assert H._retry_after_seconds(exc) == 7.0


def test_retry_after_absent_returns_none():
    exc = SourceHTTPError("503")
    exc.__cause__ = _http_status_error(503)
    assert H._retry_after_seconds(exc) is None


def test_retry_after_is_clamped_to_a_ceiling_in_the_loop():
    # A hostile/buggy server sending an enormous Retry-After must NOT make the
    # client sleep for days — the loop clamps it to RETRY_AFTER_MAX_SECONDS.
    class _OneThenOk:
        def __init__(self):
            self.calls = 0

        def get(self, url, params=None, headers=None):
            from tests.conftest import FakeResponse
            self.calls += 1
            if self.calls == 1:
                return FakeResponse(None, status_code=503, text="", url="https://x/")
            return FakeResponse({"ok": True})

    import pytest as _pytest
    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(H, "_retry_after_seconds", lambda e: 999999.0)
    rec, sleep = _sleeps()
    get_json_retrying("u", client=_OneThenOk(), source="S", sleep=sleep,
                      rand=lambda: 0.0)
    monkeypatch.undo()
    assert rec == [H.RETRY_AFTER_MAX_SECONDS]   # clamped, not 999999


def test_retry_after_overrides_backoff_in_the_loop(monkeypatch):
    # A 503 carrying Retry-After: 5 must sleep ~5s, not the backoff value.
    err = SourceHTTPError("503: rate limited")
    err.__cause__ = _http_status_error(503, retry_after="5")

    class _OneThenOk:
        def __init__(self):
            self.calls = 0

        def get(self, url, params=None, headers=None):
            self.calls += 1
            if self.calls == 1:
                from tests.conftest import FakeResponse
                return FakeResponse(None, status_code=503, text="",
                                    url="https://x/")
            from tests.conftest import FakeResponse
            return FakeResponse({"ok": True})

    # Make the 503 carry a Retry-After header by patching _retry_after_seconds via
    # the real header path: build the response with the header.
    rec, sleep = _sleeps()
    monkeypatch.setattr(H, "_retry_after_seconds", lambda e: 5.0)
    out = get_json_retrying("u", client=_OneThenOk(), source="S", sleep=sleep,
                            rand=lambda: 0.0)
    assert out == {"ok": True}
    assert rec == [5.0]


# ------------------------------------------------------------- backoff + jitter
def test_backoff_grows_exponentially_with_bounded_jitter():
    # rand=0 -> base backoff exactly; rand=1 -> +50% jitter ceiling.
    b0 = H._backoff_seconds(0, rand=lambda: 0.0)
    b1 = H._backoff_seconds(1, rand=lambda: 0.0)
    b2 = H._backoff_seconds(2, rand=lambda: 0.0)
    assert b1 > b0 and b2 > b1            # exponential growth
    jittered = H._backoff_seconds(0, rand=lambda: 1.0)
    assert b0 < jittered <= b0 * 1.5 + 1e-9   # jitter adds up to +50%
