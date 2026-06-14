"""Tiny HTTP helper with the injectable-client pattern.

Every source client accepts an optional httpx.Client so callers can reuse one
connection across many requests (and tests can inject a fake). When no client
is passed, get_json creates and closes its own. Note: the timeout argument
applies only to a self-created client; an injected client uses its own
configured timeout. A browser User-Agent is always
sent because the Program Mapper API rejects script UAs with HTTP 403.

Error handling: the LACCD APIs are the most fragile dependency in the system,
so get_json never lets a raw httpx traceback or a JSONDecodeError escape.
Instead it raises a SourceError subclass carrying the source name and URL:
  - SourceHTTPError on any 4xx/5xx (403 gets an explicit UA/Origin hint, since
    Program Mapper 403s browsers-only requests);
  - SourceDataError on an empty body or a non-JSON payload (HTML error page,
    truncated response, etc).
Callers (schedule.py, program_mapper.py) pass a human source label so the
message names the endpoint that drifted rather than a bare URL.
"""
from __future__ import annotations

import json as _json
import random as _random
import time as _time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

DEFAULT_TIMEOUT = 30.0
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


class SourceError(RuntimeError):
    """Base class for any failure talking to a live LACCD source."""


class SourceHTTPError(SourceError):
    """A live source returned a 4xx/5xx HTTP status."""


class SourceDataError(SourceError):
    """A live source returned an empty or non-JSON body where JSON was expected."""


def get_json(url, *, params=None, headers=None, client=None, timeout=DEFAULT_TIMEOUT,
             source="LACCD API"):
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        merged = {**DEFAULT_HEADERS, **(headers or {})}
        resp = client.get(url, params=params, headers=merged)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 403:
                raise SourceHTTPError(
                    f"{source}: HTTP 403 Forbidden from {url} — the API rejected the "
                    "request (likely a missing/blocked browser User-Agent or Origin "
                    "header). Program Mapper requires a browser UA and the campus Origin."
                ) from exc
            raise SourceHTTPError(
                f"{source}: HTTP {status} from {url}."
            ) from exc
        body = (resp.text or "").strip()
        if not body:
            raise SourceDataError(
                f"{source}: empty response body from {url} (expected JSON)."
            )
        try:
            return resp.json()
        except _json.JSONDecodeError as exc:
            preview = body[:120].replace("\n", " ")
            raise SourceDataError(
                f"{source}: non-JSON response from {url} "
                f"(first bytes: {preview!r})."
            ) from exc
    except httpx.HTTPError as exc:
        # Connection/timeout/transport errors (not status errors, handled above).
        raise SourceError(f"{source}: request to {url} failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()


# ---- E7: shared bounded retry/backoff over the transport ------------------
# The LACCD endpoints are flaky and schedule.fetch_sections loops terms with NO
# retry, so a single transient 5xx nuked the whole fetch. get_json_retrying wraps
# get_json with a bounded, polite retry: ONLY transient failures (429 + the
# transient 5xx 500/502/503/504, plus transport/timeout) are retried; a hard 4xx
# and any JSON/schema drift fail immediately (retrying cannot fix bad data). A
# server Retry-After is honored (clamped to a sane ceiling) when present,
# else jittered exponential backoff avoids a synchronized retry storm. sleep + rand
# are injectable so the suite stays fast and deterministic. Retry TIMING never
# touches engine.run or any parsed output, so determinism is unaffected.
# A curated allowlist of TRANSIENT statuses worth a retry — 429 plus the transient
# 5xx (500/502/503/504). Deliberately EXCLUDES 501 (Not Implemented) and other
# non-transient 5xx, which retrying cannot fix.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 8.0
BACKOFF_JITTER = 0.5          # up to +50% jitter on the base backoff
RETRY_AFTER_MAX_SECONDS = 60.0  # ceiling on a server-supplied Retry-After


def _status_of(exc):
    return getattr(getattr(exc.__cause__, "response", None), "status_code", None)


def _is_retryable(exc):
    """True iff ``exc`` is a TRANSIENT failure worth a bounded backoff retry.

    Retryable: a SourceHTTPError whose status is in RETRYABLE_STATUS (429 + the
    transient 5xx 500/502/503/504), or a transport/timeout failure (a bare
    SourceError). NOT retryable: SourceDataError (JSON/schema drift — retrying
    cannot fix bad data), 501, and every other 4xx (a hard client error)."""
    if isinstance(exc, SourceHTTPError):
        return _status_of(exc) in RETRYABLE_STATUS
    if isinstance(exc, SourceDataError):
        return False
    return isinstance(exc, SourceError)


def _retry_after_seconds(exc):
    """Parse a ``Retry-After`` header (delta-seconds OR an HTTP-date) off the
    failing response, in seconds. ``None`` when absent/unparseable."""
    resp = getattr(exc.__cause__, "response", None)
    try:
        raw = resp.headers.get("Retry-After") if resp is not None else None
    except (AttributeError, TypeError):
        return None
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.isdigit():
        return float(raw)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


def _backoff_seconds(attempt, *, rand=_random.random):
    base = min(BACKOFF_BASE_SECONDS * (2 ** attempt), BACKOFF_MAX_SECONDS)
    return base * (1.0 + BACKOFF_JITTER * rand())


def get_json_retrying(url, *, params=None, headers=None, client=None,
                      timeout=DEFAULT_TIMEOUT, source="LACCD API",
                      max_retries=DEFAULT_MAX_RETRIES, sleep=_time.sleep,
                      rand=_random.random):
    """``get_json`` with a bounded, polite retry on transient failures.

    Retries at most ``max_retries`` times, honoring ``Retry-After`` when the
    server sends it, else jittered exponential backoff. A non-retryable error
    (hard 4xx, JSON/shape drift) propagates immediately; after the final retry the
    LAST error propagates unchanged — always a clean SourceError, never a raw
    httpx traceback."""
    attempt = 0
    while True:
        try:
            return get_json(url, params=params, headers=headers, client=client,
                            timeout=timeout, source=source)
        except SourceError as exc:
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            wait = _retry_after_seconds(exc)
            if wait is None:
                wait = _backoff_seconds(attempt, rand=rand)
            else:
                # Honor the server's Retry-After, but never sleep for an absurd
                # span a hostile/buggy server might send (e.g. days).
                wait = min(wait, RETRY_AFTER_MAX_SECONDS)
            sleep(wait)
            attempt += 1
