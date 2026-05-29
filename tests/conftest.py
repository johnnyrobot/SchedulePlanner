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
        matches = [(frag, payload) for frag, payload in self.routes.items() if frag in url]
        if not matches:
            raise AssertionError(f"FakeClient: no route matches {url}")
        fragment, payload = max(matches, key=lambda item: len(item[0]))
        return FakeResponse(payload)

    def close(self):
        return None


@pytest.fixture
def make_client():
    return lambda routes: FakeClient(routes)
