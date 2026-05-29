from sources.http import get_json


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
