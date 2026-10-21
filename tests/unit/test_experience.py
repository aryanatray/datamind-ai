import asyncio
import json

import pytest
from conftest import add_sqlite, register
from datamind import llm, retrieval, tools
from datamind.api import create_app
from datamind.db import Message, Source, engine
from datamind.web_policy import automatic_query
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_scope_pins_preferences_and_private_ownership(client):
    register(client)
    ref = add_sqlite(client)
    chat = client.post("/api/conversations").json()["id"]
    assert (
        client.patch(
            f"/api/conversations/{chat}", json={"pinned": True, "source_ids": [ref], "web_enabled": False}
        ).status_code
        == 200
    )
    saved = client.get("/api/conversations").json()[0]
    assert saved["pinned"] is True
    assert saved["scope"] == {"source_ids": [ref], "web_enabled": False}
    assert client.patch(f"/api/conversations/{chat}", json={"source_ids": ["forbidden"]}).status_code == 403
    assert (
        client.patch(
            "/api/me",
            json={
                "name": "Analyst",
                "custom_instructions": "Use USD",
                "about_me": "Finance",
                "personalization_enabled": False,
            },
        ).status_code
        == 200
    )
    assert client.get("/api/me").json()["preferences"]["custom_instructions"] == "Use USD"
    with TestClient(create_app()) as other:
        register(other, "other@example.com")
        assert other.patch(f"/api/conversations/{chat}", json={"pinned": False}).status_code == 404
        assert other.get("/api/conversations").json() == []
        assert other.get("/api/me").json()["preferences"] == {}


def test_document_replace_restore_history_and_access(client):
    from datamind.worker import claim, process

    register(client)
    ref = client.post("/api/documents", files={"file": ("sales.csv", b"amount\n120.25\n")}).json()["id"]

    def next_job():
        with Session(engine()) as db:
            job = claim(db)
        process(job)

    next_job()
    chat = client.post("/api/conversations").json()["id"]
    with Session(engine()) as db:
        db.add(
            Message(
                conversation_id=chat,
                role="assistant",
                text="Old finding",
                source_ids=[ref],
                artifact={"source_versions": {ref: 1}},
            )
        )
        db.commit()
    assert (
        client.post(
            f"/api/documents/{ref}/replace", files={"file": ("sales.csv", b"amount\n120.25\n")}
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/documents/{ref}/replace", files={"file": ("bad.exe", b"bad")}).status_code == 400
    )
    response = client.post(
        f"/api/documents/{ref}/replace", files={"file": ("revised.csv", b"amount\n80.75\n")}
    )
    assert response.json()["version"] == 2
    assert client.get(f"/api/conversations/{chat}").json()[0]["restricted"] is True
    next_job()
    query = {"sql": "SELECT SUM(amount) AS total FROM data"}
    assert client.post(f"/api/sources/{ref}/sql", json=query).json()["rows"] == [{"total": 80.75}]
    assert client.get(f"/api/documents/{ref}/versions/1/download").content == b"amount\n120.25\n"
    assert client.post(f"/api/documents/{ref}/versions/1/restore").json()["version"] == 3
    next_job()
    assert client.post(f"/api/sources/{ref}/sql", json=query).json()["rows"] == [{"total": 120.25}]
    assert len(client.get(f"/api/documents/{ref}").json()["versions"]) == 3
    with TestClient(create_app()) as other:
        register(other, "other@example.com")
        assert other.get(f"/api/documents/{ref}/versions/1/download").status_code == 404
        assert other.post(f"/api/documents/{ref}/versions/1/restore").status_code == 404


def test_superseded_job_cannot_publish_old_content(client):
    from datamind.worker import claim, process

    register(client)
    ref = client.post("/api/documents", files={"file": ("first.md", b"Old")}).json()["id"]
    with Session(engine()) as db:
        old = claim(db)
    assert (
        client.post(f"/api/documents/{ref}/replace", files={"file": ("table.csv", b"amount\n1")}).status_code
        == 400
    )
    assert (
        client.post(f"/api/documents/{ref}/replace", files={"file": ("second.md", b"New")}).status_code == 200
    )
    process(old)
    result = client.get(f"/api/documents/{ref}").json()
    assert result["status"] == "queued"
    assert any(j["state"] == "superseded" for j in result["jobs"])


@pytest.mark.parametrize(
    "question",
    [
        "Latest database market news",
        "What is the capital of France?",
        "Competitive intelligence about Microsoft",
    ],
)
def test_public_search_route(question):
    assert automatic_query(question, []) == question


@pytest.mark.parametrize(
    "question",
    [
        "Latest news about C001",
        "Latest about c001",
        "What is our revenue?",
        "Latest about person@example.com",
        "Latest password token details",
        "Latest Aster contract",
        "Compare our competitors",
    ],
)
def test_private_context_stays_local(question):
    assert automatic_query(question, [{"name": "Aster contract"}]) == ""


def test_web_routing_and_disabled_search_are_offline(client, monkeypatch):
    register(client)
    sent = []

    def search(query):
        sent.append(query)
        return [{"title": "Public", "url": "https://example.com", "text": "Public information"}]

    async def stream(*args):
        yield {"text": "Public information [W:0]" if sent else "Hello"}
        yield {"usage": {"total_tokens": 12, "input_tokens": 8, "output_tokens": 4, "usage_reported": True}}

    monkeypatch.setattr(tools, "web_search", search)
    monkeypatch.setattr(llm, "stream", stream)
    chat = client.post("/api/conversations").json()["id"]
    client.post(f"/api/conversations/{chat}/chat", json={"question": "Latest database market news"})
    assert sent == ["Latest database market news"]
    artifact = client.get(f"/api/conversations/{chat}").json()[-1]["artifact"]
    assert artifact["observability"]["public_web_used"] is True
    assert artifact["usage"]["total_tokens"] == 12
    sent.clear()
    client.post(
        f"/api/conversations/{chat}/chat",
        json={"question": "Latest database market news", "web_enabled": False},
    )
    assert sent == []


def test_personalization_and_scope_history_boundaries(client, monkeypatch):
    register(client)
    ref = add_sqlite(client)
    prompts = []

    async def stream(db, actor, prompt, trace):
        prompts.append(prompt)
        yield {"text": "Hello"}
        yield {"usage": {"total_tokens": 1, "usage_reported": True}}

    monkeypatch.setattr(llm, "stream", stream)
    for enabled in (True, False):
        client.patch(
            "/api/me",
            json={"name": "Analyst", "custom_instructions": "Prefer EUR", "personalization_enabled": enabled},
        )
        chat = client.post("/api/conversations").json()["id"]
        with Session(engine()) as db:
            db.add(
                Message(
                    conversation_id=chat,
                    role="assistant",
                    text="PRIVATE EARLIER FINDING",
                    source_ids=[ref],
                    artifact={"source_versions": {ref: db.get(Source, ref).version}},
                )
            )
            db.commit()
        client.post(
            f"/api/conversations/{chat}/chat",
            json={"question": "Hello", "source_ids": [], "web_enabled": False},
        )
        assert ("Prefer EUR" in prompts[-1]) is enabled
        assert "PRIVATE EARLIER FINDING" not in prompts[-1]


def test_setup_status_and_provider_preflight(client):
    from datamind.llm import plan, plan_schema
    from jsonschema import Draft202012Validator

    register(client)
    assert client.get("/api/setup").json()["connections_ready"] == 0
    add_sqlite(client)
    assert client.get("/api/setup").json()["connections_ready"] == 1
    schema = plan_schema(["selected"])
    Draft202012Validator.check_schema(schema)
    assert "$ref" not in json.dumps(schema)
    with pytest.raises(HTTPException) as error:
        asyncio.run(plan(None, None, "x" * 100001, [], ["selected"]))
    assert error.value.status_code == 400


def test_migration_backfills_existing_uploads(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from datamind.config import settings
    from sqlalchemy import text

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    settings.cache_clear()
    engine.cache_clear()
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "0001")
        with engine().begin() as db:
            db.execute(
                text(
                    "INSERT INTO workspaces (id,name,mode,capacity,policy_version) VALUES ('w','Test','personal',1,1)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO sources (id,workspace_id,name,kind,secret,catalog,resources,status,version,created) VALUES ('s','w','Doc','document','','{}','[]','ready',4,CURRENT_TIMESTAMP)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO documents (id,filename,extension,path,sha256,collection,size,extracted,profile) VALUES ('s','doc.md','.md','doc.md','abc','General',3,'[]','{}')"
                )
            )
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with engine().connect() as db:
            assert db.execute(text("SELECT version,filename FROM document_versions")).all() == [(4, "doc.md")]
    finally:
        engine().dispose()
        engine.cache_clear()
        settings.cache_clear()


def test_empty_local_retrieval_public_fallback_and_web_failure(client, monkeypatch):
    register(client)
    ref = client.post("/api/documents", files={"file": ("glossary.md", b"A glossary")}).json()["id"]
    with Session(engine()) as db:
        db.get(Source, ref).status = "ready"
        db.commit()
    sent = []
    monkeypatch.setattr(retrieval, "search", lambda *args: {"evidence": [], "stages": []})

    def web(query):
        sent.append(query)
        return [{"title": "Public", "text": "Public explanation", "url": "https://example.com"}]

    async def stream(*args):
        yield {"text": "Public explanation [W:0]"}
        yield {"usage": {"total_tokens": 1, "usage_reported": True}}

    monkeypatch.setattr(tools, "web_search", web)
    monkeypatch.setattr(llm, "stream", stream)
    chat = client.post("/api/conversations").json()["id"]
    result = client.post(
        f"/api/conversations/{chat}/chat", json={"question": "What is PostgreSQL?", "source_ids": [ref]}
    )
    assert '"type": "done"' in result.text
    assert sent == ["What is PostgreSQL?"]

    def fail(query):
        raise TimeoutError("not exposed")

    monkeypatch.setattr(tools, "web_search", fail)
    chat = client.post("/api/conversations").json()["id"]
    response = client.post(f"/api/conversations/{chat}/chat", json={"question": "Latest database news"})
    assert "Public search is unavailable" in response.text
    assert "not exposed" not in response.text
    assert not any(m["role"] == "assistant" for m in client.get(f"/api/conversations/{chat}").json())
