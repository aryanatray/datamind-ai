import pytest
from conftest import register
from datamind.db import engine
from datamind.limits import consume
from datamind.orchestrator import validate_chart, validate_plan
from datamind.retrieval import fuse
from datamind.schemas import Chart, Plan, ToolCall
from fastapi import HTTPException
from sqlalchemy.orm import Session


def test_reciprocal_rank_fusion():
    result = fuse([["a", "b", "c"], ["b", "c", "d"]])
    assert result[0][0] == "b"
    assert result[0][1] == pytest.approx(1 / 62 + 1 / 61)


def test_atomic_budget(environment):
    with Session(engine()) as db:
        consume(db, "test", 8, 10)
        db.commit()
        with pytest.raises(HTTPException):
            consume(db, "test", 3, 10)
        consume(db, "test", 2, 10)
        db.commit()


@pytest.mark.parametrize(
    "plan",
    [
        Plan(category="sql", steps=[ToolCall(tool="sql", source_id="forbidden", sql="SELECT 1")]),
        Plan(category="hybrid", steps=[ToolCall(tool="sql", source_id="ok", depends_on=[0])]),
        Plan(category="web", steps=[ToolCall(tool="web", query="private customer data")]),
    ],
)
def test_invalid_plans(plan):
    with pytest.raises(HTTPException):
        validate_plan(plan, {"ok"}, "public weather")


def test_chart_validation():
    results = [{"columns": ["customer", "revenue"], "rows": [{"customer": "A", "revenue": 20}]}]
    assert validate_chart(Chart(kind="bar", x="customer", y="revenue"), results)
    with pytest.raises(HTTPException):
        validate_chart(Chart(kind="bar", x="customer", y="invented"), results)
    with pytest.raises(HTTPException):
        validate_chart(Chart(kind="scatter", x="customer", y="revenue"), results)


def test_missing_gemini_is_explicit(client):
    register(client)
    chat_id = client.post("/api/conversations").json()["id"]
    response = client.post(f"/api/conversations/{chat_id}/chat", json={"question": "Hello"})
    assert response.status_code == 200
    assert "no-transform" in response.headers["cache-control"]
    assert "GEMINI_API_KEY" in response.text
    history = client.get(f"/api/conversations/{chat_id}").json()
    assert not any(item["role"] == "assistant" for item in history)
    from datamind.db import Budget
    from sqlalchemy import select

    with Session(engine()) as db:
        assert not list(db.scalars(select(Budget).where(Budget.key.like("tokens:%"))))


async def test_invalid_gemini_key_is_actionable_without_retry_or_error_leak():
    from types import SimpleNamespace

    from datamind.llm import create_with_retry

    class InvalidKey(Exception):
        status_code = 400

    calls = []

    async def rejected(**kwargs):
        calls.append(kwargs)
        raise InvalidKey("API_KEY_INVALID with confidential provider diagnostics")

    api = SimpleNamespace(interactions=SimpleNamespace(create=rejected))
    with pytest.raises(HTTPException) as error:
        await create_with_retry(api, [], input="fictional Aster demo")
    assert error.value.status_code == 503
    assert "GEMINI_API_KEY as invalid" in error.value.detail
    assert "confidential" not in error.value.detail
    assert len(calls) == 1


def test_native_plan_schema_rejects_unselected_identifiers():
    from datamind.llm import plan_schema
    from jsonschema import ValidationError, validate

    allowed = {"category": "sql", "steps": [{"tool": "sql", "source_id": "selected"}]}
    validate(allowed, plan_schema(["selected"]))
    with pytest.raises(ValidationError):
        validate(allowed, plan_schema(["different-workspace"]))
    for identifier in ["", None]:
        step = {"tool": "sql", "sql": "SELECT 1"}
        if identifier is not None:
            step["source_id"] = identifier
        with pytest.raises(ValidationError):
            validate({"category": "sql", "steps": [step]}, plan_schema(["selected"]))
    validate(
        {"category": "rag", "steps": [{"tool": "search_documents", "source_id": "selected"}]},
        plan_schema(["selected"]),
    )
    assert Plan(category="rag", steps=[ToolCall(tool="search_documents")]).steps[0].source_id == ""


async def test_provider_rejection_preserves_safe_reason_without_retry(environment):
    import json
    from types import SimpleNamespace

    import httpx
    from datamind.config import settings
    from datamind.llm import create_with_retry
    from google.genai._gaos.errors.genaierror import GenAiError

    settings().gemini_api_key = "synthetic-secret-key"
    body = {
        "error": {
            "status": "INVALID_ARGUMENT",
            "message": "Invalid schema synthetic-secret-key password=secret Authorization: Bearer audit-bearer-secret",
        },
        "request": {"input": "private prompt"},
    }
    calls = []

    async def rejected(**kwargs):
        calls.append(kwargs)
        raise GenAiError("Bad request", httpx.Response(400, json=body))

    trace = []
    with pytest.raises(HTTPException) as error:
        await create_with_retry(SimpleNamespace(interactions=SimpleNamespace(create=rejected)), trace)
    assert len(calls) == 1
    assert "configuration" not in error.value.detail
    assert trace[0]["code"] == "INVALID_ARGUMENT"
    assert "Invalid schema" in trace[0]["message"]
    saved = json.dumps(trace)
    for secret in ["synthetic-secret-key", "password=secret", "audit-bearer-secret", "private prompt"]:
        assert secret not in saved


async def test_provider_transient_retry_is_bounded(environment, monkeypatch):
    from types import SimpleNamespace

    from datamind import llm

    class Unavailable(Exception):
        status_code = 503

    calls, waits, trace = [], [], []

    async def rejected(**kwargs):
        calls.append(kwargs)
        raise Unavailable("not retained")

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    with pytest.raises(HTTPException):
        await llm.create_with_retry(SimpleNamespace(interactions=SimpleNamespace(create=rejected)), trace)
    assert len(calls) == 3
    assert len(waits) == 2
    assert [entry["stage"] for entry in trace] == ["gemini_retry", "gemini_retry", "gemini_error"]


async def test_native_planner_request_and_response_offline(environment, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from datamind import llm

    captured = []

    async def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            usage=None,
            steps=[
                SimpleNamespace(
                    type="function_call",
                    name="propose_plan",
                    arguments={
                        "category": "sql",
                        "steps": [{"tool": "sql", "source_id": "selected", "sql": "SELECT 1"}],
                    },
                )
            ],
        )

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(interactions=SimpleNamespace(create=create))

    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setattr(llm, "reserve_tokens", lambda *args: [])
    monkeypatch.setattr(llm, "record", lambda *args: {"total_tokens": 0})
    trace = []
    plan = await llm.plan(None, None, "synthetic question", trace, ["selected"])
    assert plan.steps[0].source_id == "selected"
    request = captured[0]
    assert request["store"] is False
    assert request["generation_config"] == {"max_output_tokens": 4096, "tool_choice": "any"}
    assert request["tools"][0]["parameters"] == llm.plan_schema(["selected"])
    assert trace[0]["stage"] == "gemini_plan"
