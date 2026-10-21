"""Synthetic LLM fixtures test control flow; these are NOT live Gemini integration results."""

import json

from conftest import add_sqlite, register
from datamind import llm
from datamind.db import Workspace, engine
from datamind.schemas import Plan, QueryInput, ToolCall
from sqlalchemy import update
from sqlalchemy.orm import Session


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def fixture_plan(source_id, sql="SELECT SUM(revenue) AS revenue FROM orders"):
    async def result(*args):
        return Plan(category="sql", steps=[ToolCall(tool="sql", source_id=source_id, sql=sql)])

    return result


async def fixture_stream(*args):
    yield {"text": "Calculated revenue is 260 [R:0]."}
    yield {"usage": {"input_tokens": 0, "output_tokens": 0, "fixture": True}}


def test_fixture_plan_real_sql_result_and_saved_dashboard(client, monkeypatch):
    register(client)
    source_id = add_sqlite(client)
    monkeypatch.setattr(llm, "plan", fixture_plan(source_id))

    async def synthesis_with_catalog(db, actor, prompt, trace):
        assert "sum orders.revenue" in prompt
        async for part in fixture_stream(db, actor, prompt, trace):
            yield part

    monkeypatch.setattr(llm, "stream", synthesis_with_catalog)
    chat_id = client.post("/api/conversations").json()["id"]
    result = client.post(
        f"/api/conversations/{chat_id}/chat", json={"question": "Booked revenue?", "source_ids": [source_id]}
    )
    done = events(result)[-1]
    assert done["type"] == "done", done
    assert done["artifact"]["results"][0]["rows"] == [{"revenue": 260}]
    assert done["artifact"]["source_versions"][source_id] >= 1
    dashboard = client.post(
        "/api/dashboards",
        json={
            "name": "Revenue",
            "widgets": [{"message_id": done["id"], "chart": {"kind": "kpi", "y": "revenue"}}],
        },
    )
    assert dashboard.status_code == 201, dashboard.text
    assert client.post(f"/api/dashboards/{dashboard.json()['id']}/refresh").status_code == 200
    assert "260" in client.get(f"/api/messages/{done['id']}/export").text
    # Narrowing the table allowlist invalidates existing artifacts even while source-level access remains.
    client.patch(
        f"/api/sources/{source_id}/catalog", json={"resources": ["customers"], "catalog": {}}
    ).raise_for_status()
    assert client.get(f"/api/conversations/{chat_id}").json()[-1]["restricted"]
    assert client.get(f"/api/messages/{done['id']}/export").status_code == 403
    assert client.get("/api/dashboards").json()[0]["widgets"][0]["restricted"]


def test_fixture_invalid_citation_never_persists_answer(client, monkeypatch):
    register(client)
    source_id = add_sqlite(client)
    monkeypatch.setattr(llm, "plan", fixture_plan(source_id))

    async def bad(*args):
        yield {"text": "An unsupported citation [D:invented]."}

    monkeypatch.setattr(llm, "stream", bad)
    chat_id = client.post("/api/conversations").json()["id"]
    result = client.post(
        f"/api/conversations/{chat_id}/chat", json={"question": "Revenue?", "source_ids": [source_id]}
    )
    assert events(result)[-1]["type"] == "error"
    assert all(m["role"] != "assistant" for m in client.get(f"/api/conversations/{chat_id}").json())


def test_denied_plan_retains_reported_usage_and_observable_plan(client, monkeypatch):
    register(client)
    source_id = add_sqlite(client)

    async def rejected_plan(db, actor, prompt, trace, selected):
        assert selected == [source_id]
        trace.append({"stage": "gemini_plan", "usage": {"total_tokens": 111, "usage_reported": True}})
        return Plan(category="sql", steps=[ToolCall(tool="sql", source_id="unselected", sql="SELECT 1")])

    monkeypatch.setattr(llm, "plan", rejected_plan)
    chat_id = client.post("/api/conversations").json()["id"]
    response = client.post(
        f"/api/conversations/{chat_id}/chat", json={"question": "Revenue?", "source_ids": [source_id]}
    )
    failure = events(response)[-1]
    assert failure["type"] == "error"
    assert failure["error"] == "Plan requested an unselected source"
    assert failure["artifact"]["usage"]["total_tokens"] == 111
    assert failure["artifact"]["usage"]["partial"] is True
    assert failure["artifact"]["trace"][-1]["plan"]["steps"][0]["source_id"] == "unselected"
    history = client.get(f"/api/conversations/{chat_id}").json()
    assert history[-1]["artifact"]["usage"]["total_tokens"] == 111
    assert not any(item["role"] == "assistant" for item in history)


def test_fixture_repair_is_bounded_and_executes_real_sql(client, monkeypatch):
    register(client)
    source_id = add_sqlite(client)
    monkeypatch.setattr(llm, "plan", fixture_plan(source_id, "SELECT SUM(unknown) AS revenue FROM orders"))
    calls = []

    async def repair(*args):
        calls.append(True)
        return QueryInput(sql="SELECT SUM(revenue) AS revenue FROM orders")

    monkeypatch.setattr(llm, "structured", repair)
    monkeypatch.setattr(llm, "stream", fixture_stream)
    chat_id = client.post("/api/conversations").json()["id"]
    result = client.post(
        f"/api/conversations/{chat_id}/chat", json={"question": "Revenue?", "source_ids": [source_id]}
    )
    assert events(result)[-1]["type"] == "done", result.text
    assert len(calls) == 1


def test_fixture_permission_change_stops_stream_and_does_not_save(client, monkeypatch):
    me = register(client)
    source_id = add_sqlite(client)
    monkeypatch.setattr(llm, "plan", fixture_plan(source_id))

    async def changed(*args):
        yield {"text": "Starting"}
        with Session(engine()) as db:
            db.execute(
                update(Workspace)
                .where(Workspace.id == me["workspace_id"])
                .values(policy_version=Workspace.policy_version + 1)
            )
            db.commit()
        yield {"text": "must not be delivered"}

    monkeypatch.setattr(llm, "stream", changed)
    chat_id = client.post("/api/conversations").json()["id"]
    result = client.post(
        f"/api/conversations/{chat_id}/chat", json={"question": "Revenue?", "source_ids": [source_id]}
    )
    assert events(result)[-1]["type"] == "error"
    assert "must not be delivered" not in result.text


def test_disconnected_source_can_be_reconnected(client):
    register(client)
    source_id = add_sqlite(client)
    client.post(f"/api/sources/{source_id}/disconnect").raise_for_status()
    assert client.get("/api/sources").json()[0]["status"] == "disconnected"
    assert (
        client.post(f"/api/sources/{source_id}/sql", json={"sql": "SELECT * FROM orders"}).status_code == 400
    )
    assert client.post(f"/api/connections/{source_id}/test").status_code == 200


def test_independent_read_steps_use_real_queries_and_keep_result_order(client, monkeypatch):
    register(client)
    source_id = add_sqlite(client)

    async def planner(*args):
        return Plan(
            category="sql",
            steps=[
                ToolCall(tool="sql", source_id=source_id, sql="SELECT SUM(revenue) AS revenue FROM orders"),
                ToolCall(tool="sql", source_id=source_id, sql="SELECT COUNT(*) AS customers FROM customers"),
            ],
        )

    monkeypatch.setattr(llm, "plan", planner)
    monkeypatch.setattr(llm, "stream", fixture_stream)
    chat_id = client.post("/api/conversations").json()["id"]
    response = client.post(
        f"/api/conversations/{chat_id}/chat",
        json={"question": "Revenue and customers?", "source_ids": [source_id]},
    )
    done = events(response)[-1]
    assert done["type"] == "done", response.text
    assert done["artifact"]["results"][0]["rows"] == [{"revenue": 260}]
    assert done["artifact"]["results"][1]["rows"] == [{"customers": 2}]
    assert all(item["parallel"] for item in done["artifact"]["trace"] if item["stage"] == "sql")
