"""Use the official MCP client against a running DataMind API and a real SQLite source."""

import asyncio
import json
import uuid
from pathlib import Path

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    url = "http://127.0.0.1:18000"
    with httpx.Client(base_url=url, trust_env=False) as web:
        web.post(
            "/api/auth/signup",
            json={
                "email": f"qa-mcp-{uuid.uuid4().hex[:10]}@example.com",
                "name": "MCP QA",
                "password": uuid.uuid4().hex,
            },
        ).raise_for_status()
        me = web.get("/api/me").json()
        web.headers.update({"X-CSRF-Token": me["csrf"], "X-Workspace-ID": me["workspace_id"]})
        added = web.post(
            "/api/connections", json={"name": "MCP SQLite", "kind": "sqlite", "config": {"path": "aster.db"}}
        )
        added.raise_for_status()
        source_id = added.json()["id"]
        web.post(f"/api/connections/{source_id}/test").raise_for_status()
        web.patch(
            f"/api/sources/{source_id}/catalog", json={"resources": ["orders"], "catalog": {}}
        ).raise_for_status()
        token = web.post("/api/me/mcp-token").json()["token"]
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + token, "X-Workspace-ID": me["workspace_id"]}
    ) as http:
        async with streamable_http_client(url + "/mcp/", http_client=http) as (read, write):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                tools = await client.list_tools()
                result = await client.call_tool(
                    "execute_readonly_sql",
                    {"source_id": source_id, "sql": "SELECT COUNT(*) AS count FROM orders"},
                )
                assert not result.is_error
                data = json.loads(result.content[0].text)
                assert data["rows"] == [{"count": 120}], data
                summary = {
                    "sdk": "official mcp 2.3.0",
                    "negotiated_protocol": initialized.protocol_version,
                    "tools_discovered": len(tools.tools),
                    "actual_sql_rows_counted": 120,
                    "status": "passed",
                }
                Path("docs/mcp-verification.json").write_text(json.dumps(summary, indent=2))
                print(json.dumps(summary))


if __name__ == "__main__":
    asyncio.run(main())
