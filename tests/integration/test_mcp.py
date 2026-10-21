from conftest import add_sqlite, register


def request(client, token, method, params=None, identifier=1):
    return client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}},
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        },
    )


def test_actual_protocol_initialize_discover_execute(client):
    register(client)
    source_id = add_sqlite(client)
    token = client.post("/api/me/mcp-token").json()["token"]
    initialized = request(
        client,
        token,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    )
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["result"]["serverInfo"]["name"] == "DataMind.AI"
    listed = request(client, token, "tools/list")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["result"]["tools"]) == 10
    result = request(
        client,
        token,
        "tools/call",
        {
            "name": "execute_readonly_sql",
            "arguments": {"source_id": source_id, "sql": "SELECT SUM(revenue) AS revenue FROM orders"},
        },
    )
    assert result.status_code == 200, result.text
    assert "260" in result.text, result.text
    denied = request(
        client,
        token,
        "tools/call",
        {"name": "execute_readonly_sql", "arguments": {"source_id": source_id, "sql": "DELETE FROM orders"}},
    )
    assert denied.json()["result"]["isError"], denied.text


def test_mcp_requires_bearer_even_with_session_cookie(client):
    register(client)
    assert client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
