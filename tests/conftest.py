"""Shared test harness: an injectable fake HTTP client.

FakeClient mimics the slice of httpx.Client our get_json() helper uses:
get(url, params, headers) -> response with .raise_for_status() and .json().
Routes map a URL substring to a JSON payload.

A route value may also be a FakeResponse (or one built via the error_response /
json_response helpers) to simulate HTTP status errors or non-JSON / empty
payloads, so the error paths in sources.http can be exercised without a network.
"""
import json as _json

import httpx
import pytest


class FakeResponse:
    """Stands in for httpx.Response across the slice get_json() touches.

    status_code drives raise_for_status() (which raises a real
    httpx.HTTPStatusError, exactly like httpx). json() mirrors httpx by
    raising json.JSONDecodeError when the body is not valid JSON.
    """

    def __init__(self, payload=None, *, status_code=200, text=None, url="https://fake/"):
        self._payload = payload
        self.status_code = status_code
        self.url = url
        if text is not None:
            self.text = text
        else:
            self.text = "" if payload is None else _json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", self.url)
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code} error", request=request, response=response
            )
        return None

    def json(self):
        # Mirror httpx: parse the body text, raising on invalid JSON.
        return _json.loads(self.text)


def json_response(payload, **kwargs):
    return FakeResponse(payload, **kwargs)


def error_response(status_code, *, url="https://fake/"):
    return FakeResponse(None, status_code=status_code, text="", url=url)


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers or {}})
        matches = [(frag, payload) for frag, payload in self.routes.items() if frag in url]
        if not matches:
            raise AssertionError(f"FakeClient: no route matches {url}")
        fragment, payload = max(matches, key=lambda item: len(item[0]))
        if isinstance(payload, FakeResponse):
            return payload
        return FakeResponse(payload, url=url)

    def close(self):
        return None


@pytest.fixture
def make_client():
    return lambda routes: FakeClient(routes)


@pytest.fixture
def fake_response():
    """The FakeResponse class itself, so tests build error/edge responses
    using the SAME class identity FakeClient checks with isinstance()."""
    return FakeResponse


@pytest.fixture
def error_resp():
    return error_response
