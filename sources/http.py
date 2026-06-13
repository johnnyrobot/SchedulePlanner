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
  - SourceHTTPError on any 4xx/5xx (403 gets a generic browser-User-Agent hint
    by default; a caller may pass ``forbidden_hint`` for a source-specific
    remedy — e.g. Program Mapper, which also needs the campus Origin header);
  - SourceDataError on an empty body or a non-JSON payload (HTML error page,
    truncated response, etc).
Callers (schedule.py, program_mapper.py) pass a human source label so the
message names the endpoint that drifted rather than a bare URL. The default
403 hint is deliberately source-agnostic because this transport is shared by
schedule, eLumen and ASSIST too — none of which use the Origin header.
"""
from __future__ import annotations

import json as _json

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
             source="LACCD API", forbidden_hint=None):
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
                # Default remedy is source-agnostic: this transport is shared by
                # schedule, eLumen and ASSIST, which send no Origin header, so do
                # NOT name Program Mapper's campus-Origin remedy here. A caller
                # that knows its own accurate cause threads ``forbidden_hint``.
                hint = forbidden_hint or (
                    "This usually means the request is missing or sending a "
                    "blocked browser User-Agent header.")
                raise SourceHTTPError(
                    f"{source}: HTTP 403 Forbidden from {url} — the API rejected "
                    f"the request. {hint}"
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
