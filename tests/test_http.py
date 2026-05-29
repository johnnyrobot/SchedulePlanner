import httpx
import pytest

from sources.http import SourceDataError, SourceHTTPError, get_json


def test_get_json_uses_injected_client_and_returns_payload(make_client):
    client = make_client({"http://x/data": {"ok": True}})
    result = get_json("http://x/data", client=client)
    assert result == {"ok": True}


def test_get_json_sets_browser_user_agent(make_client):
    # Program Mapper 403s on non-browser UAs; the helper must always send one.
    client = make_client({"http://x/data": {"ok": True}})
    get_json("http://x/data", client=client)
    assert "Mozilla" in client.calls[0]["headers"].get("User-Agent", "")


def test_get_json_merges_caller_headers(make_client):
    client = make_client({"http://x/data": {"ok": True}})
    get_json("http://x/data", headers={"Origin": "https://example.org"}, client=client)
    sent = client.calls[0]["headers"]
    assert sent["Origin"] == "https://example.org"
    assert "User-Agent" in sent


# ----------------------------------------------------------- error paths

def test_get_json_wraps_403_with_ua_origin_hint(make_client, error_resp):
    client = make_client({"http://x/data": error_resp(403)})
    with pytest.raises(SourceHTTPError) as ei:
        get_json("http://x/data", client=client, source="Program Mapper")
    msg = str(ei.value)
    assert "403" in msg
    assert "Program Mapper" in msg
    # Names the likely cause so a maintainer is not left guessing.
    assert "User-Agent" in msg or "Origin" in msg


def test_get_json_wraps_500_with_source_and_url(make_client, error_resp):
    client = make_client({"http://x/data": error_resp(500)})
    with pytest.raises(SourceHTTPError) as ei:
        get_json("http://x/data", client=client, source="LACCD schedule")
    msg = str(ei.value)
    assert "500" in msg
    assert "LACCD schedule" in msg
    assert "http://x/data" in msg


def test_get_json_raises_on_empty_body(make_client, fake_response):
    client = make_client({"http://x/data": fake_response(text="", status_code=200)})
    with pytest.raises(SourceDataError) as ei:
        get_json("http://x/data", client=client, source="LACCD schedule")
    assert "empty" in str(ei.value).lower()


def test_get_json_raises_on_non_json_body(make_client, fake_response):
    client = make_client(
        {"http://x/data": fake_response(text="<html>503 backend</html>", status_code=200)}
    )
    with pytest.raises(SourceDataError) as ei:
        get_json("http://x/data", client=client, source="LACCD schedule")
    msg = str(ei.value)
    assert "non-JSON" in msg
    assert "LACCD schedule" in msg


def test_source_http_error_chains_original_httpx_error(make_client, error_resp):
    client = make_client({"http://x/data": error_resp(404)})
    with pytest.raises(SourceHTTPError) as ei:
        get_json("http://x/data", client=client)
    # The raw httpx error is preserved for debugging, not swallowed.
    assert isinstance(ei.value.__cause__, httpx.HTTPStatusError)
