"""Shared test harness: an injectable fake HTTP client.

FakeClient mimics the slice of httpx.Client our get_json() helper uses:
get(url, params, headers) -> response with .raise_for_status() and .json().
Routes map a URL substring to a JSON payload.
"""
import pytest


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers or {}})
        for fragment, payload in self.routes.items():
            if fragment in url:
                return FakeResponse(payload)
        raise AssertionError(f"FakeClient: no route matches {url}")

    def close(self):
        return None


@pytest.fixture
def make_client():
    return lambda routes: FakeClient(routes)
