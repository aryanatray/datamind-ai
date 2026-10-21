from conftest import add_sqlite, register
from datamind.api import create_app
from datamind.db import Message, engine
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_authentication_and_csrf(client):
    assert client.get("/api/sources").status_code == 401
    register(client)
    assert client.post("/api/conversations", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/conversations", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/conversations").status_code == 201
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_cross_tenant_isolation(client):
    register(client)
    source_id = add_sqlite(client)
    chat = client.post("/api/conversations").json()["id"]
    with TestClient(create_app()) as outsider:
        register(outsider, "outsider@example.com")
        assert outsider.get("/api/sources").json() == []
        assert outsider.get(f"/api/sources/{source_id}").status_code == 404
        assert (
            outsider.post(
                f"/api/sources/{source_id}/sql", json={"sql": "SELECT * FROM customers"}
            ).status_code
            == 404
        )
        assert outsider.get(f"/api/conversations/{chat}").status_code == 404
        assert outsider.delete(f"/api/sources/{source_id}").status_code == 404


def test_bearer_logout_revokes_authenticated_token_only(client):
    register(client)
    browser_token = client.cookies.get("dm_session")
    token = client.post("/api/me/mcp-token").json()["token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/me", headers=headers).status_code == 401
    # Logging out the bearer does not revoke a distinct browser session.
    client.cookies.set("dm_session", browser_token)
    assert client.get("/api/me").status_code == 200


def test_permissions_revocation_covers_history_dashboards_exports(client):
    owner = register(client, mode="enterprise", capacity=2)
    source_id = add_sqlite(client)
    invitation = (
        client.post("/api/workspace/invitations", json={"email": "member@example.com"})
        .json()["link"]
        .split("invite=")[1]
    )
    with TestClient(create_app()) as member:
        user = register(member, "member@example.com")
        assert member.post("/api/workspace/accept", json={"token": invitation}).status_code == 200
        member.headers["X-Workspace-ID"] = owner["workspace_id"]
        assert member.get("/api/sources").json() == []
        assert (
            member.post(f"/api/sources/{source_id}/sql", json={"sql": "SELECT * FROM customers"}).status_code
            == 403
        )
        assert (
            member.post(
                "/api/connections", json={"name": "x", "kind": "sqlite", "config": {"path": "aster.db"}}
            ).status_code
            == 403
        )
        assert member.get("/api/admin/system").status_code == 403
        client.put(
            f"/api/sources/{source_id}/permissions", json={"user_id": user["id"], "allowed": True}
        ).raise_for_status()
        result = member.post(
            f"/api/sources/{source_id}/sql", json={"sql": "SELECT SUM(revenue) AS revenue FROM orders"}
        ).json()
        assert result["rows"] == [{"revenue": 260.0}]
        chat_id = member.post("/api/conversations").json()["id"]
        with Session(engine()) as db:
            from datamind.db import Source

            message = Message(
                conversation_id=chat_id,
                role="assistant",
                text="Revenue is 260",
                source_ids=[source_id],
                artifact={
                    "results": [result],
                    "source_versions": {source_id: db.get(Source, source_id).version},
                },
            )
            db.add(message)
            db.commit()
            message_id = message.id
        dashboard = member.post(
            "/api/dashboards",
            json={
                "name": "Sales",
                "widgets": [{"message_id": message_id, "chart": {"kind": "kpi", "y": "revenue"}}],
            },
        )
        assert dashboard.status_code == 201, dashboard.text
        assert member.get(f"/api/messages/{message_id}/export").status_code == 200
        client.put(
            f"/api/sources/{source_id}/permissions", json={"user_id": user["id"], "allowed": False}
        ).raise_for_status()
        assert (
            member.post(f"/api/sources/{source_id}/sql", json={"sql": "SELECT * FROM orders"}).status_code
            == 403
        )
        assert member.get(f"/api/conversations/{chat_id}").json()[0]["restricted"]
        assert member.get("/api/dashboards").json()[0]["widgets"][0]["restricted"]
        assert member.get(f"/api/messages/{message_id}/export").status_code == 403
        assert member.post(f"/api/dashboards/{dashboard.json()['id']}/refresh").status_code == 403
        client.delete(f"/api/workspace/members/{user['id']}").raise_for_status()
        assert member.get("/api/sources").status_code == 403


def test_capacity_and_last_admin(client):
    owner = register(client, mode="enterprise", capacity=2)
    assert client.post("/api/workspace/invitations", json={"email": "one@example.com"}).status_code == 200
    assert client.post("/api/workspace/invitations", json={"email": "two@example.com"}).status_code == 409
    assert client.patch("/api/workspace", json={"name": "Too small", "capacity": 1}).status_code == 400
    assert client.delete(f"/api/workspace/members/{owner['id']}").status_code == 400
    assert client.patch(f"/api/workspace/members/{owner['id']}", json={"role": "member"}).status_code == 400


def test_credentials_never_returned(client):
    register(client)
    source_id = add_sqlite(client)
    assert "secret" not in client.get(f"/api/sources/{source_id}").json()
    assert "password" not in client.get("/api/sources").text


def test_unverified_email_cannot_claim_platform_admin(client):
    register(client, "operator@example.com")
    assert client.get("/api/admin/system").status_code == 403
    assert not client.get("/api/me").json()["platform_admin"]


def test_path_and_ssrf_denied(client):
    register(client)
    assert (
        client.post(
            "/api/connections", json={"name": "unsafe", "kind": "sqlite", "config": {"path": "../../.env"}}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/connections",
            json={"name": "unsafe", "kind": "postgres", "config": {"host": "169.254.169.254"}},
        ).status_code
        == 400
    )


def test_deleting_source_redacts_artifacts(client):
    register(client)
    source_id = add_sqlite(client)
    chat_id = client.post("/api/conversations").json()["id"]
    with Session(engine()) as db:
        db.add(Message(conversation_id=chat_id, role="assistant", text="sensitive", source_ids=[source_id]))
        db.commit()
    assert client.delete(f"/api/sources/{source_id}").status_code == 200
    assert client.get("/api/sources").json() == []
    assert client.get(f"/api/conversations/{chat_id}").json()[0]["restricted"]


def test_login_persistence_and_wrong_password(client):
    register(client)
    client.post("/api/auth/logout")
    assert (
        client.post(
            "/api/auth/login", json={"email": "owner@example.com", "password": "incorrect"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": "owner@example.com", "password": "correct-horse-test-password"}
        ).status_code
        == 200
    )
    assert client.get("/api/me").status_code == 200
