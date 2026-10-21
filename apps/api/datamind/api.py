import csv
import io
import json
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from mcp.server.transport_security import RequestBodyLimitMiddleware
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from datamind import connectors, ingestion, orchestrator, retrieval, tools
from datamind.config import settings
from datamind.db import (
    Audit,
    Conversation,
    Dashboard,
    Document,
    DocumentVersion,
    Grant,
    Invitation,
    Job,
    LoginSession,
    Membership,
    Message,
    Source,
    SystemSetting,
    Usage,
    User,
    Workspace,
    now,
    session,
    uid,
)
from datamind.limits import consume, rate_limit
from datamind.schemas import (
    AcceptInvite,
    CatalogInput,
    ChatInput,
    ConnectionInput,
    ConversationInput,
    DashboardInput,
    GrantInput,
    InviteInput,
    Login,
    MongoQuery,
    ProfileInput,
    QueryInput,
    RoleInput,
    Signup,
    SystemInput,
    WorkspaceInput,
)
from datamind.security import (
    Principal,
    admin,
    allowed_ids,
    audit,
    authorize_refs,
    create_session,
    decrypt,
    digest,
    encrypt,
    hasher,
    platform_admin,
    principal,
    resolve,
    source,
    verify_password,
)


@asynccontextmanager
async def lifespan(app):
    cfg = settings()
    if len(cfg.secret_key) < 32 or not cfg.encryption_key:
        raise RuntimeError("Run infrastructure/scripts/setup.py before starting DataMind")
    cfg.storage_root.mkdir(parents=True, exist_ok=True)
    cfg.sqlite_root.mkdir(parents=True, exist_ok=True)
    # Schema changes are applied explicitly with Alembic, never implicitly at startup.
    if hasattr(app.state, "mcp_app"):
        async with app.state.mcp_app.router.lifespan_context(app.state.mcp_app):
            yield
    else:
        yield


app = FastAPI(title="DataMind.AI", version="0.1.0")


class BoundaryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != settings().public_origin:
            return Response("Origin denied", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = (
            "no-store, no-transform"
            if response.headers.get("content-type", "").startswith("text/event-stream")
            else "no-store"
        )
        response.headers["Referrer-Policy"] = "same-origin"
        return response


app.add_middleware(BoundaryMiddleware)


def auth_throttle(db: Session, request: Request):
    # Do not trust forwarded IP headers from arbitrary clients.
    key = digest(request.client.host if request.client else "local")[:24]
    consume(db, "auth:" + key + ":" + now().strftime("%Y%m%d%H%M"), 1, 20)
    db.commit()


def cookies(response: Response, token: str, csrf: str):
    response.set_cookie(
        "dm_session",
        token,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="lax",
        max_age=604800,
        path="/",
    )
    return {"csrf": csrf}


@app.get("/api/health")
def health(db: Session = Depends(session)):
    db.execute(select(1))
    return {"status": "ok", "version": "0.1.0"}


@app.post("/api/auth/signup", status_code=201)
def signup(body: Signup, request: Request, response: Response, db: Session = Depends(session)):
    auth_throttle(db, request)
    user = User(email=str(body.email).lower(), name=body.name, password=hasher.hash(body.password))
    workspace = Workspace(
        name=body.workspace, mode=body.mode, capacity=body.capacity if body.mode == "enterprise" else 1
    )
    try:
        db.add_all([user, workspace])
        db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Unable to register this email") from exc
    token, csrf = create_session(db, user.id)
    return cookies(response, token, csrf)


@app.post("/api/auth/login")
def login(body: Login, request: Request, response: Response, db: Session = Depends(session)):
    auth_throttle(db, request)
    user = db.scalar(select(User).where(User.email == str(body.email).lower()))
    # Both branches perform Argon2 work to reduce account enumeration through timing.
    encoded = user.password if user else hasher.hash("not-a-real-account-password")
    if not verify_password(encoded, body.password) or not user:
        raise HTTPException(401, "Email or password is incorrect")
    token, csrf = create_session(db, user.id)
    return cookies(response, token, csrf)


@app.post("/api/auth/logout")
def logout(
    request: Request,
    response: Response,
    actor: Principal = Depends(principal),
    db: Session = Depends(session),
):
    authorization = request.headers.get("authorization", "")
    token = (
        authorization[7:] if authorization.startswith("Bearer ") else request.cookies.get("dm_session", "")
    )
    db.execute(delete(LoginSession).where(LoginSession.token_hash == digest(token)))
    db.commit()
    response.delete_cookie("dm_session", path="/")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request, actor: Principal = Depends(principal), db: Session = Depends(session)):
    user = db.get(User, actor.user_id)
    _, sess = resolve(
        db,
        request.cookies.get("dm_session", "")
        or request.headers.get("authorization", "").removeprefix("Bearer "),
    )
    workspaces = [
        {"id": w.id, "name": w.name, "mode": w.mode, "role": m.role, "capacity": w.capacity}
        for w, m in db.execute(
            select(Workspace, Membership).join(Membership).where(Membership.user_id == user.id)
        )
    ]
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "preferences": user.preferences,
        "workspace_id": actor.workspace_id,
        "role": actor.role,
        "workspaces": workspaces,
        "csrf": sess.csrf,
        "platform_admin": bool(settings().platform_admin_user_id)
        and user.id == settings().platform_admin_user_id
        and user.email == settings().platform_admin_email.lower(),
    }


@app.patch("/api/me")
def profile(body: ProfileInput, actor: Principal = Depends(principal), db: Session = Depends(session)):
    user = db.get(User, actor.user_id)
    user.name = body.name
    user.preferences = {key: value for key, value in body.model_dump().items() if key != "name"}
    db.commit()
    return {"ok": True}


@app.get("/api/setup")
def setup_status(actor: Principal = Depends(principal), db: Session = Depends(session)):
    available = tools.list_connections(db, actor)
    return {
        "provider_configured": bool(settings().gemini_api_key),
        "provider_verified": False,
        "connections_ready": sum(
            s["status"] == "ready" and s["kind"] not in {"document", "table"} for s in available
        ),
        "documents_ready": sum(
            s["status"] == "ready" and s["kind"] in {"document", "table"} for s in available
        ),
        "resources_selected": any(s["resources"] for s in available),
        "personalized": bool(db.get(User, actor.user_id).preferences.get("custom_instructions")),
    }


@app.post("/api/me/mcp-token")
def mcp_token(actor: Principal = Depends(principal), db: Session = Depends(session)):
    token, _ = create_session(db, actor.user_id)
    sess = db.get(LoginSession, digest(token))
    sess.expires = now() + timedelta(hours=1)
    db.commit()
    return {"token": token, "expires": sess.expires.isoformat(), "workspace_id": actor.workspace_id}


@app.get("/api/workspace")
def workspace(actor: Principal = Depends(principal), db: Session = Depends(session)):
    obj = db.get(Workspace, actor.workspace_id)
    members = [
        {"id": user.id, "name": user.name, "email": user.email, "role": member.role}
        for user, member in db.execute(
            select(User, Membership).join(Membership).where(Membership.workspace_id == obj.id)
        )
    ]
    invitations = (
        [
            {"email": item.email, "expires": item.expires.isoformat()}
            for item in db.scalars(
                select(Invitation).where(
                    Invitation.workspace_id == obj.id,
                    Invitation.accepted.is_(False),
                    Invitation.expires > now(),
                )
            )
        ]
        if actor.role == "admin"
        else []
    )
    return {
        "name": obj.name,
        "mode": obj.mode,
        "capacity": obj.capacity,
        "members": members,
        "invitations": invitations,
    }


def lock_workspace(db: Session, workspace_id: str):
    # Also serializes seat/last-admin checks on SQLite, whose SELECT FOR UPDATE is a no-op.
    db.execute(
        update(Workspace)
        .where(Workspace.id == workspace_id)
        .values(policy_version=Workspace.policy_version + 1)
    )


@app.patch("/api/workspace")
def edit_workspace(
    body: WorkspaceInput, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    admin(actor)
    lock_workspace(db, actor.workspace_id)
    obj = db.get(Workspace, actor.workspace_id)
    count = db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == obj.id))
    pending = db.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(Invitation.workspace_id == obj.id, Invitation.accepted.is_(False), Invitation.expires > now())
    )
    if body.capacity < count + pending or (obj.mode == "personal" and body.capacity != 1):
        raise HTTPException(400, "Capacity cannot be lower than members plus pending invitations")
    obj.name, obj.capacity = body.name, body.capacity
    audit(db, actor, "workspace_updated")
    return {"ok": True}


@app.post("/api/workspace/invitations")
def invite(body: InviteInput, actor: Principal = Depends(principal), db: Session = Depends(session)):
    admin(actor)
    lock_workspace(db, actor.workspace_id)
    obj = db.get(Workspace, actor.workspace_id)
    if obj.mode != "enterprise":
        raise HTTPException(400, "Personal workspaces cannot invite members")
    email = str(body.email).lower()
    if db.scalar(
        select(Invitation).where(
            Invitation.workspace_id == obj.id,
            Invitation.email == email,
            Invitation.accepted.is_(False),
            Invitation.expires > now(),
        )
    ):
        raise HTTPException(409, "An active invitation already exists")
    existing = db.scalar(
        select(User).join(Membership).where(Membership.workspace_id == obj.id, User.email == email)
    )
    if existing:
        raise HTTPException(409, "This user is already a member")
    count = db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == obj.id))
    pending = db.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(Invitation.workspace_id == obj.id, Invitation.accepted.is_(False), Invitation.expires > now())
    )
    if count + pending >= obj.capacity:
        raise HTTPException(409, "Workspace member capacity reached")
    token = secrets.token_urlsafe(32)
    db.add(
        Invitation(
            token_hash=digest(token), workspace_id=obj.id, email=email, expires=now() + timedelta(days=3)
        )
    )
    audit(db, actor, "invitation_created")
    return {"link": settings().public_origin + "/?invite=" + token, "expires_in_days": 3}


@app.post("/api/workspace/accept")
def accept(body: AcceptInvite, actor: Principal = Depends(principal), db: Session = Depends(session)):
    invitation = db.get(Invitation, digest(body.token))
    if not invitation or invitation.accepted or invitation.expires < now() or invitation.email != actor.email:
        raise HTTPException(400, "Invitation is invalid, expired, or addressed to a different account")
    lock_workspace(db, invitation.workspace_id)
    db.refresh(invitation)
    if invitation.accepted:
        raise HTTPException(409, "Invitation already accepted")
    obj = db.get(Workspace, invitation.workspace_id)
    count = db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == obj.id))
    if count >= obj.capacity:
        raise HTTPException(409, "Workspace capacity reached")
    if not db.get(Membership, (actor.user_id, obj.id)):
        db.add(Membership(user_id=actor.user_id, workspace_id=obj.id, role="member"))
    invitation.accepted = True
    audit(db, actor, "invitation_accepted", joined_workspace_id=obj.id)
    return {"workspace_id": obj.id}


def protect_last_admin(db, actor, member):
    count = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.workspace_id == actor.workspace_id, Membership.role == "admin")
    )
    if member.role == "admin" and count <= 1:
        raise HTTPException(400, "The workspace must retain an administrator")


@app.patch("/api/workspace/members/{user_id}")
def set_role(
    user_id: str, body: RoleInput, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    admin(actor)
    lock_workspace(db, actor.workspace_id)
    member = db.get(Membership, (user_id, actor.workspace_id))
    if not member:
        raise HTTPException(404, "Member not found")
    if body.role != "admin":
        protect_last_admin(db, actor, member)
    member.role = body.role
    audit(db, actor, "member_role_changed", member_id=user_id, role=body.role)
    return {"ok": True}


@app.delete("/api/workspace/members/{user_id}")
def remove_member(user_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    admin(actor)
    lock_workspace(db, actor.workspace_id)
    member = db.get(Membership, (user_id, actor.workspace_id))
    if not member:
        raise HTTPException(404, "Member not found")
    protect_last_admin(db, actor, member)
    db.delete(member)
    ids = select(Source.id).where(Source.workspace_id == actor.workspace_id)
    db.execute(delete(Grant).where(Grant.user_id == user_id, Grant.source_id.in_(ids)))
    audit(db, actor, "member_removed", member_id=user_id)
    return {"ok": True}


@app.get("/api/sources")
def sources(actor: Principal = Depends(principal), db: Session = Depends(session)):
    return tools.list_connections(db, actor)


@app.post("/api/connections", status_code=201)
def add_connection(
    body: ConnectionInput, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    admin(actor)
    # Validate destinations at creation, even before first connection attempt.
    if body.kind == "sqlite":
        from datamind.security import confined

        confined(settings().sqlite_root, body.config.path)
    else:
        connectors.host(body.config.model_dump())
    obj = Source(
        workspace_id=actor.workspace_id,
        name=body.name,
        kind=body.kind,
        secret=encrypt(body.config.model_dump()),
        resources=body.resources,
        catalog=body.catalog,
    )
    db.add(obj)
    db.commit()
    audit(db, actor, "connection_added", source_id=obj.id)
    return tools.metadata(obj)


@app.put("/api/connections/{source_id}")
def edit_connection(
    source_id: str,
    body: ConnectionInput,
    actor: Principal = Depends(principal),
    db: Session = Depends(session),
):
    admin(actor)
    obj = source(db, actor, source_id)
    if obj.kind in {"document", "table"}:
        raise HTTPException(400, "Uploaded sources cannot become external connections")
    if body.kind == "sqlite":
        from datamind.security import confined

        confined(settings().sqlite_root, body.config.path)
    else:
        connectors.host(body.config.model_dump())
    obj.name, obj.kind = body.name, body.kind
    obj.secret, obj.resources, obj.catalog = encrypt(body.config.model_dump()), body.resources, body.catalog
    obj.status, obj.version = "configured", obj.version + 1
    lock_workspace(db, actor.workspace_id)
    audit(db, actor, "connection_updated", source_id=obj.id)
    return tools.metadata(obj)


@app.post("/api/connections/{source_id}/test")
def test_connection(source_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    admin(actor)
    obj = source(db, actor, source_id)
    if obj.kind not in {"postgres", "mysql", "sqlite", "mongo"}:
        raise HTTPException(400, "Not an external connection")
    try:
        schema = connectors.discover(obj.kind, decrypt(obj.secret))
    except Exception as exc:
        raise HTTPException(
            400,
            f"Connection failed ({type(exc).__name__}). Check host, credentials and database permissions.",
        ) from exc
    obj.catalog = {**obj.catalog, "schema": schema}
    obj.status = "ready"
    db.commit()
    return {"schema": schema, "resources": list(schema)}


@app.get("/api/sources/{source_id}")
def source_metadata(source_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    return tools.metadata(source(db, actor, source_id))


@app.patch("/api/sources/{source_id}/catalog")
def catalog(
    source_id: str, body: CatalogInput, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    admin(actor)
    obj = source(db, actor, source_id)
    schema = obj.catalog.get("schema", {})
    if not set(body.resources).issubset(schema):
        raise HTTPException(400, "Resources must be selected from the discovered schema")
    # Clients may annotate, but cannot replace observed schema with invented metadata.
    obj.resources = body.resources
    obj.catalog = {**body.catalog, "schema": schema}
    if obj.kind != "document":
        obj.version += 1
    lock_workspace(db, actor.workspace_id)
    audit(db, actor, "catalog_updated", source_id=obj.id)
    return tools.metadata(obj)


@app.get("/api/sources/{source_id}/permissions")
def permissions(source_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    admin(actor)
    source(db, actor, source_id)
    return list(db.scalars(select(Grant.user_id).where(Grant.source_id == source_id)))


@app.put("/api/sources/{source_id}/permissions")
def permission(
    source_id: str, body: GrantInput, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    admin(actor)
    source(db, actor, source_id)
    if not db.get(Membership, (body.user_id, actor.workspace_id)):
        raise HTTPException(400, "User is not a workspace member")
    existing = db.get(Grant, (source_id, body.user_id))
    if body.allowed and not existing:
        db.add(Grant(source_id=source_id, user_id=body.user_id))
    elif not body.allowed and existing:
        db.delete(existing)
    lock_workspace(db, actor.workspace_id)
    audit(
        db,
        actor,
        "source_permission_changed",
        source_id=source_id,
        member_id=body.user_id,
        allowed=body.allowed,
    )
    return {"ok": True}


@app.post("/api/sources/{source_id}/disconnect")
def disconnect(source_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    admin(actor)
    obj = source(db, actor, source_id)
    obj.status = "disconnected"
    lock_workspace(db, actor.workspace_id)
    audit(db, actor, "source_disconnected", source_id=source_id)
    return {"ok": True}


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    admin(actor)
    obj = source(db, actor, source_id)
    obj.status, obj.version = "deleted", obj.version + 1
    obj.secret, obj.catalog = "", {}
    db.execute(delete(Grant).where(Grant.source_id == source_id))
    lock_workspace(db, actor.workspace_id)
    doc = db.get(Document, source_id)
    if doc:
        doc.extracted, doc.profile = [], {}
        for path in settings().storage_root.glob(source_id + "*"):
            if path.is_file():
                try:
                    path.unlink()
                except PermissionError:
                    # A worker may hold the file on Windows; tombstone still denies all further access.
                    pass
    audit(db, actor, "source_deleted", source_id=source_id)
    try:
        retrieval.delete_vectors(source_id)
    except Exception:
        # Durable tombstone filters retrieval even if vector storage is temporarily unavailable.
        pass
    return {"ok": True}


@app.post("/api/sources/{source_id}/sql")
def sql_query(
    source_id: str, body: QueryInput, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    rate_limit(db, actor)
    return tools.execute_sql(db, actor, source_id, body.sql)


@app.post("/api/sources/{source_id}/mongo")
def mongo_query(
    source_id: str, body: MongoQuery, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    rate_limit(db, actor)
    return tools.execute_mongo(db, actor, source_id, body)


@app.post("/api/documents", status_code=201)
async def upload(
    file: UploadFile = File(...), actor: Principal = Depends(principal), db: Session = Depends(session)
):
    admin(actor)
    data = await file.read(settings().max_upload_mb * 1024 * 1024 + 1)
    name = Path((file.filename or "document").replace("\\", "/")).name[:255]
    extension = ingestion.validate_upload(name, data)
    identifier = uid()
    filename = identifier + extension
    settings().storage_root.mkdir(parents=True, exist_ok=True)
    path = settings().storage_root / filename
    path.write_bytes(data)
    obj = Source(id=identifier, workspace_id=actor.workspace_id, name=name, kind="document", status="queued")
    try:
        db.add(obj)
        db.flush()
        db.add(
            Document(
                id=identifier,
                filename=name,
                extension=extension,
                path=filename,
                size=len(data),
                sha256=ingestion.checksum(data),
            )
        )
        db.add(Job(source_id=identifier, version=1))
        db.add(
            DocumentVersion(
                source_id=identifier,
                version=1,
                filename=name,
                extension=extension,
                path=filename,
                sha256=ingestion.checksum(data),
                size=len(data),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return tools.metadata(obj)


@app.get("/api/documents/{source_id}")
def document_preview(source_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    obj = source(db, actor, source_id)
    doc = db.get(Document, source_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": obj.status,
        "collection": doc.collection,
        "version": obj.version,
        "versions": [
            {
                "version": v.version,
                "filename": v.filename,
                "size": v.size,
                "created": v.created.isoformat(),
                "sha256": v.sha256,
            }
            for v in db.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.source_id == source_id)
                .order_by(DocumentVersion.version.desc())
            )
        ],
        "extracted": doc.extracted[:100],
        "profile": doc.profile,
        "jobs": [
            {
                "id": job.id,
                "state": job.state,
                "stage": job.stage,
                "error": job.error,
                "attempts": job.attempts,
                "version": job.version,
            }
            for job in db.scalars(
                select(Job).where(Job.source_id == source_id).order_by(Job.created.desc()).limit(5)
            )
        ],
    }


@app.post("/api/documents/{source_id}/replace")
async def replace_document(
    source_id: str,
    file: UploadFile = File(...),
    actor: Principal = Depends(principal),
    db: Session = Depends(session),
):
    admin(actor)
    lock_workspace(db, actor.workspace_id)
    obj = source(db, actor, source_id)
    doc = db.get(Document, source_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    data = await file.read(settings().max_upload_mb * 1024 * 1024 + 1)
    name = Path((file.filename or "document").replace("\\", "/")).name[:255]
    extension = ingestion.validate_upload(name, data)
    if (extension in {".csv", ".xlsx"}) != (doc.extension in {".csv", ".xlsx"}):
        raise HTTPException(400, "Replace a document with a document, or a table with a table")
    if ingestion.checksum(data) == doc.sha256:
        raise HTTPException(409, "This file is identical to the current version")
    obj.version += 1
    name_on_disk = f"{source_id}-v{obj.version}{extension}"
    path = settings().storage_root / name_on_disk
    path.write_bytes(data)
    try:
        doc.filename, doc.extension, doc.path = name, extension, name_on_disk
        doc.sha256, doc.size, doc.extracted, doc.profile = ingestion.checksum(data), len(data), [], {}
        obj.name, obj.status = name, "queued"
        if obj.kind == "table":
            obj.resources, obj.catalog, obj.secret = [], {}, ""
        db.add(
            DocumentVersion(
                source_id=source_id,
                version=obj.version,
                filename=name,
                extension=extension,
                path=name_on_disk,
                sha256=doc.sha256,
                size=doc.size,
            )
        )
        db.add(Job(source_id=source_id, version=obj.version))
        audit(db, actor, "document_replaced", source_id=source_id, version=obj.version)
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return {"version": obj.version, "status": obj.status}


@app.get("/api/documents/{source_id}/versions/{version}/download")
def download_version(
    source_id: str, version: int, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    from datamind.security import confined

    source(db, actor, source_id)
    item = db.get(DocumentVersion, (source_id, version))
    if not item:
        raise HTTPException(404, "Version not found")
    return FileResponse(
        confined(settings().storage_root, item.path),
        filename=item.filename,
        media_type="application/octet-stream",
    )


@app.post("/api/documents/{source_id}/versions/{version}/restore")
def restore_version(
    source_id: str, version: int, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    from datamind.security import confined

    admin(actor)
    lock_workspace(db, actor.workspace_id)
    obj = source(db, actor, source_id)
    doc = db.get(Document, source_id)
    item = db.get(DocumentVersion, (source_id, version))
    if not doc or not item:
        raise HTTPException(404, "Version not found")
    original = confined(settings().storage_root, item.path)
    obj.version += 1
    target = f"{source_id}-v{obj.version}{item.extension}"
    shutil.copyfile(original, settings().storage_root / target)
    try:
        doc.filename, doc.extension, doc.path = item.filename, item.extension, target
        doc.sha256, doc.size, doc.extracted, doc.profile = item.sha256, item.size, [], {}
        obj.name, obj.status = item.filename, "queued"
        if obj.kind == "table":
            obj.resources, obj.catalog, obj.secret = [], {}, ""
        db.add(
            DocumentVersion(
                source_id=source_id,
                version=obj.version,
                filename=item.filename,
                extension=item.extension,
                path=target,
                sha256=item.sha256,
                size=item.size,
            )
        )
        db.add(Job(source_id=source_id, version=obj.version))
        audit(db, actor, "document_restored", source_id=source_id, version=obj.version, restored_from=version)
    except Exception:
        db.rollback()
        (settings().storage_root / target).unlink(missing_ok=True)
        raise
    return {"version": obj.version, "status": obj.status}


@app.post("/api/documents/{source_id}/reindex")
def reindex(source_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    admin(actor)
    obj = source(db, actor, source_id)
    if not db.get(Document, source_id):
        raise HTTPException(404, "Document not found")
    obj.version += 1
    obj.status = "queued"
    db.add(Job(source_id=source_id, version=obj.version))
    lock_workspace(db, actor.workspace_id)
    audit(db, actor, "document_reindex", source_id=source_id)
    return {"ok": True}


@app.patch("/api/documents/{source_id}/collection")
def collection(
    source_id: str,
    body: WorkspaceInput,
    actor: Principal = Depends(principal),
    db: Session = Depends(session),
):
    admin(actor)
    source(db, actor, source_id)
    doc = db.get(Document, source_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    doc.collection = body.name
    db.commit()
    return {"ok": True}


@app.get("/api/evidence/{chunk_id}")
def get_evidence(chunk_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    return tools.evidence(db, actor, chunk_id)


@app.get("/api/conversations")
def conversations(q: str = "", actor: Principal = Depends(principal), db: Session = Depends(session)):
    allowed = allowed_ids(db, actor)
    return [
        {
            "id": item.id,
            "title": item.title,
            "pinned": item.pinned,
            "scope": {
                **item.scope,
                "source_ids": [ref for ref in item.scope.get("source_ids", []) if ref in allowed],
            },
        }
        for item in db.scalars(
            select(Conversation)
            .where(
                Conversation.user_id == actor.user_id,
                Conversation.workspace_id == actor.workspace_id,
                Conversation.title.ilike("%" + q[:100] + "%"),
            )
            .order_by(Conversation.pinned.desc(), Conversation.created.desc())
            .limit(200)
        )
    ]


@app.post("/api/conversations", status_code=201)
def create_conversation(actor: Principal = Depends(principal), db: Session = Depends(session)):
    obj = Conversation(user_id=actor.user_id, workspace_id=actor.workspace_id)
    db.add(obj)
    db.commit()
    return {"id": obj.id, "title": obj.title}


def owned_conversation(db, actor, conversation_id):
    obj = db.get(Conversation, conversation_id)
    if not obj or obj.user_id != actor.user_id or obj.workspace_id != actor.workspace_id:
        raise HTTPException(404, "Conversation not found")
    return obj


@app.patch("/api/conversations/{conversation_id}")
def edit_conversation(
    conversation_id: str,
    body: ConversationInput,
    actor: Principal = Depends(principal),
    db: Session = Depends(session),
):
    obj = owned_conversation(db, actor, conversation_id)
    if body.pinned is not None:
        obj.pinned = body.pinned
    scope = dict(obj.scope)
    if body.source_ids is not None:
        authorize_refs(db, actor, body.source_ids)
        if any(source(db, actor, ref).status != "ready" for ref in body.source_ids):
            raise HTTPException(400, "Only ready sources can be selected")
        scope["source_ids"] = list(dict.fromkeys(body.source_ids))
    if body.web_enabled is not None:
        scope["web_enabled"] = body.web_enabled
    obj.scope = scope
    db.commit()
    return {"id": obj.id, "pinned": obj.pinned, "scope": obj.scope}


def message_view(db, actor, obj):
    try:
        authorize_refs(db, actor, obj.source_ids, obj.artifact.get("source_versions", {}))
    except HTTPException:
        return {
            "id": obj.id,
            "role": obj.role,
            "text": "This message is unavailable because source access changed.",
            "artifact": {},
            "restricted": True,
        }
    return {
        "id": obj.id,
        "role": obj.role,
        "text": obj.text,
        "artifact": obj.artifact,
        "source_ids": obj.source_ids,
    }


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)):
    owned_conversation(db, actor, conversation_id)
    return [
        message_view(db, actor, m)
        for m in db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created, Message.id)
        )
    ]


@app.delete("/api/conversations/{conversation_id}")
def remove_conversation(
    conversation_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    obj = owned_conversation(db, actor, conversation_id)
    db.execute(delete(Message).where(Message.conversation_id == obj.id))
    # Remove widgets so saved dashboards cannot hold deleted conversation data.
    for dashboard in db.scalars(select(Dashboard).where(Dashboard.user_id == actor.user_id)):
        dashboard.widgets = [w for w in dashboard.widgets if db.get(Message, w["message_id"]) is not None]
    db.delete(obj)
    db.commit()
    return {"ok": True}


@app.post("/api/conversations/{conversation_id}/chat")
async def chat(
    conversation_id: str,
    body: ChatInput,
    request: Request,
    actor: Principal = Depends(principal),
    db: Session = Depends(session),
):
    obj = owned_conversation(db, actor, conversation_id)
    authorize_refs(db, actor, body.source_ids)
    if any(source(db, actor, ref).status != "ready" for ref in body.source_ids):
        raise HTTPException(
            400, "Some selected sources are still processing. Wait for Ready or unselect them."
        )
    if body.regenerate:
        previous = db.scalar(
            select(Message)
            .where(Message.conversation_id == obj.id, Message.role == "user")
            .order_by(Message.created.desc())
            .limit(1)
        )
        if not previous or previous.text != body.question:
            raise HTTPException(400, "Regeneration requires the last user question")
    rate_limit(db, actor)

    async def events():
        async for event in orchestrator.run(db, actor, obj, body, request.is_disconnected):
            yield "data: " + json.dumps(event, default=str) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


def owned_message(db, actor, message_id):
    message = db.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Result not found")
    owned_conversation(db, actor, message.conversation_id)
    authorize_refs(db, actor, message.source_ids, message.artifact.get("source_versions", {}))
    return message


@app.get("/api/messages/{message_id}/export")
def export(
    message_id: str,
    result_index: int = 0,
    actor: Principal = Depends(principal),
    db: Session = Depends(session),
):
    obj = owned_message(db, actor, message_id)
    results = obj.artifact.get("results", [])
    if not 0 <= result_index < len(results):
        raise HTTPException(404, "Result not found")
    result = results[result_index]
    stream = io.StringIO()
    writer = csv.writer(stream)

    def safe(value):
        value = str(value) if value is not None else ""
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value

    writer.writerow([safe(c) for c in result["columns"]])
    for row in result["rows"]:
        writer.writerow([safe(row.get(c)) for c in result["columns"]])
    return Response(
        stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="datamind-result.csv"'},
    )


def dashboard_view(db, actor, dashboard):
    widgets = []
    for widget in dashboard.widgets:
        try:
            message = owned_message(db, actor, widget["message_id"])
            results = message.artifact.get("results", [])
            chart = widget["chart"]
            widgets.append({**widget, "result": results[widget["result_index"]], "chart": chart})
        except (HTTPException, IndexError):
            widgets.append({"restricted": True, "message_id": widget["message_id"]})
    return {"id": dashboard.id, "name": dashboard.name, "widgets": widgets}


@app.get("/api/dashboards")
def dashboards(actor: Principal = Depends(principal), db: Session = Depends(session)):
    return [
        dashboard_view(db, actor, d)
        for d in db.scalars(
            select(Dashboard).where(
                Dashboard.user_id == actor.user_id, Dashboard.workspace_id == actor.workspace_id
            )
        )
    ]


def check_widgets(db, actor, body):
    for widget in body.widgets:
        message = owned_message(db, actor, widget.message_id)
        results = message.artifact.get("results", [])
        widget.chart.result_index = widget.result_index
        orchestrator.validate_chart(widget.chart, results)


@app.post("/api/dashboards", status_code=201)
def add_dashboard(
    body: DashboardInput, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    check_widgets(db, actor, body)
    obj = Dashboard(
        user_id=actor.user_id,
        workspace_id=actor.workspace_id,
        name=body.name,
        widgets=[w.model_dump() for w in body.widgets],
    )
    db.add(obj)
    db.commit()
    return dashboard_view(db, actor, obj)


def own_dashboard(db, actor, dashboard_id):
    obj = db.get(Dashboard, dashboard_id)
    if not obj or obj.user_id != actor.user_id or obj.workspace_id != actor.workspace_id:
        raise HTTPException(404, "Dashboard not found")
    return obj


@app.put("/api/dashboards/{dashboard_id}")
def edit_dashboard(
    dashboard_id: str,
    body: DashboardInput,
    actor: Principal = Depends(principal),
    db: Session = Depends(session),
):
    obj = own_dashboard(db, actor, dashboard_id)
    check_widgets(db, actor, body)
    obj.name, obj.widgets = body.name, [w.model_dump() for w in body.widgets]
    db.commit()
    return dashboard_view(db, actor, obj)


@app.post("/api/dashboards/{dashboard_id}/refresh")
def refresh_dashboard(
    dashboard_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    rate_limit(db, actor)
    obj = own_dashboard(db, actor, dashboard_id)
    for widget in obj.widgets:
        message = owned_message(db, actor, widget["message_id"])
        artifact = dict(message.artifact)
        results = list(artifact["results"])
        result = results[widget["result_index"]]
        if "original_sql" in result:
            fresh = tools.execute_sql(db, actor, result["source_id"], result["original_sql"])
        else:
            fresh = tools.execute_mongo(
                db, actor, result["source_id"], MongoQuery.model_validate(result["mongo"])
            )
        results[widget["result_index"]] = fresh
        artifact["results"] = results
        # Refreshing analytical data never silently changes the original natural-language answer.
        artifact["refreshed_at"] = now().isoformat()
        message.artifact = artifact
    db.commit()
    return dashboard_view(db, actor, obj)


@app.delete("/api/dashboards/{dashboard_id}")
def delete_dashboard(
    dashboard_id: str, actor: Principal = Depends(principal), db: Session = Depends(session)
):
    db.delete(own_dashboard(db, actor, dashboard_id))
    db.commit()
    return {"ok": True}


@app.get("/api/admin/system")
def system(actor: Principal = Depends(principal), db: Session = Depends(session)):
    platform_admin(actor)
    cfg = settings()
    return {
        "gemini_configured": bool(cfg.gemini_api_key),
        "gemini_model": cfg.gemini_model,
        "embedding_model": cfg.embedding_model,
        "reranker_model": cfg.reranker_model,
        "global_daily_token_limit": (db.get(SystemSetting, "global_daily_token_limit") or cfg).value
        if db.get(SystemSetting, "global_daily_token_limit")
        else cfg.global_daily_token_limit,
        "user_daily_token_limit": db.get(SystemSetting, "user_daily_token_limit").value
        if db.get(SystemSetting, "user_daily_token_limit")
        else cfg.user_daily_token_limit,
        "usage": [
            {
                "user_id": u.user_id,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "latency_ms": u.latency_ms,
                "created": u.created.isoformat(),
            }
            for u in db.scalars(select(Usage).order_by(Usage.created.desc()).limit(200))
        ],
        "jobs": {
            state: count for state, count in db.execute(select(Job.state, func.count()).group_by(Job.state))
        },
    }


@app.patch("/api/admin/system")
def edit_system(body: SystemInput, actor: Principal = Depends(principal), db: Session = Depends(session)):
    platform_admin(actor)
    for key, value in body.model_dump().items():
        obj = db.get(SystemSetting, key)
        if obj:
            obj.value = value
        else:
            db.add(SystemSetting(key=key, value=value))
    audit(db, actor, "system_quota_updated")
    return {"ok": True}


@app.get("/api/workspace/audit")
def audit_log(actor: Principal = Depends(principal), db: Session = Depends(session)):
    admin(actor)
    return [
        {
            "action": row.action,
            "user_id": row.user_id,
            "details": row.details,
            "created": row.created.isoformat(),
        }
        for row in db.scalars(
            select(Audit)
            .where(Audit.workspace_id == actor.workspace_id)
            .order_by(Audit.created.desc())
            .limit(100)
        )
    ]


_api_router = app.router


def create_app():
    from datamind.mcp_server import build_protocol_app

    instance = FastAPI(title="DataMind.AI", version="0.1.0", lifespan=lifespan)
    instance.include_router(_api_router)
    instance.add_middleware(BoundaryMiddleware)
    instance.add_middleware(
        RequestBodyLimitMiddleware, max_body_size=settings().max_upload_mb * 1024 * 1024 + 1024 * 1024
    )
    instance.state.mcp_app = build_protocol_app()
    instance.mount("/mcp", instance.state.mcp_app)
    return instance


app = create_app()
