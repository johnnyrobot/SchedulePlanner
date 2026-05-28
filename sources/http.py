"""Tiny HTTP helper with the injectable-client pattern.

Every source client accepts an optional httpx.Client so callers can reuse one
connection across many requests (and tests can inject a fake). When no client
is passed, get_json creates and closes its own. Note: the timeout argument
applies only to a self-created client; an injected client uses its own
configured timeout. A browser User-Agent is always
sent because the Program Mapper API rejects script UAs with HTTP 403.
"""
from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = 30.0
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def get_json(url: str, *, params=None, headers=None, client=None, timeout=DEFAULT_TIMEOUT):
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        merged = {**DEFAULT_HEADERS, **(headers or {})}
        resp = client.get(url, params=params, headers=merged)
        resp.raise_for_status()
        return resp.json()
    finally:
        if owns_client:
            client.close()
