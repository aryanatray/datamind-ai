import sqlite3

import pytest
from cryptography.fernet import Fernet
from datamind.config import settings
from datamind.db import Base, engine
from fastapi.testclient import TestClient


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    monkeypatch.setenv("SECRET_KEY", "test-only-" * 5)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "files"))
    monkeypatch.setenv("SQLITE_ROOT", str(tmp_path / "sources"))
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "operator@example.com")
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("USER_REQUESTS_PER_MINUTE", "200")
    monkeypatch.setenv("WORKSPACE_REQUESTS_PER_MINUTE", "500")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    settings.cache_clear()
    engine.cache_clear()
    cfg = settings()
    cfg.storage_root.mkdir()
    cfg.sqlite_root.mkdir()
    with sqlite3.connect(cfg.sqlite_root / "aster.db") as db:
        db.executescript(
            "CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT); CREATE TABLE orders(id INTEGER PRIMARY KEY,customer_id INTEGER, revenue REAL); INSERT INTO customers VALUES(1,'Aster'),(2,'Birch'); INSERT INTO orders VALUES(1,1,120),(2,2,80),(3,1,60); CREATE TABLE secret(value TEXT); INSERT INTO secret VALUES('hidden');"
        )
    Base.metadata.create_all(engine())
    yield tmp_path
    engine().dispose()
    engine.cache_clear()
    settings.cache_clear()
    from datamind.retrieval import vector_client

    if vector_client.cache_info().currsize:
        vector_client().close()
        vector_client.cache_clear()


@pytest.fixture
def client(environment):
    from datamind.api import create_app

    with TestClient(create_app(), base_url="http://localhost:8000") as instance:
        yield instance


def register(client, email="owner@example.com", mode="personal", capacity=5):
    response = client.post(
        "/api/auth/signup",
        json={
            "email": email,
            "name": email.split("@")[0],
            "password": "correct-horse-test-password",
            "mode": mode,
            "capacity": capacity,
        },
    )
    assert response.status_code == 201, response.text
    me = client.get("/api/me").json()
    client.headers.update({"X-CSRF-Token": me["csrf"], "X-Workspace-ID": me["workspace_id"]})
    return me


def add_sqlite(client, resources=None):
    response = client.post(
        "/api/connections", json={"name": "Aster SQLite", "kind": "sqlite", "config": {"path": "aster.db"}}
    )
    assert response.status_code == 201, response.text
    source_id = response.json()["id"]
    tested = client.post(f"/api/connections/{source_id}/test")
    assert tested.status_code == 200, tested.text
    saved = client.patch(
        f"/api/sources/{source_id}/catalog",
        json={
            "resources": resources or ["customers", "orders"],
            "catalog": {"description": "Aster commerce", "metrics": {"revenue": "sum orders.revenue"}},
        },
    )
    assert saved.status_code == 200, saved.text
    return source_id
