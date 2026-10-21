import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from datamind.config import settings
from datamind.db import Audit, Grant, LoginSession, Membership, Source, User, Workspace, now, session

hasher = PasswordHasher()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def encrypt(value: dict) -> str:
    return Fernet(settings().encryption_key.encode()).encrypt(json.dumps(value).encode()).decode()


def decrypt(value: str) -> dict:
    return json.loads(Fernet(settings().encryption_key.encode()).decrypt(value.encode()))


def verify_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def create_session(db: Session, user_id: str) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_hex(32)
    db.add(
        LoginSession(token_hash=digest(token), user_id=user_id, csrf=csrf, expires=now() + timedelta(days=7))
    )
    db.commit()
    return token, csrf


@dataclass(frozen=True)
class Principal:
    user_id: str
    workspace_id: str
    role: str
    email: str


def resolve(db: Session, token: str, workspace_id: str | None = None) -> tuple[Principal, LoginSession]:
    sess = db.get(LoginSession, digest(token))
    if not sess or sess.expires < now():
        raise HTTPException(401, "Sign in to continue")
    user = db.get(User, sess.user_id)
    stmt = select(Membership).where(Membership.user_id == sess.user_id)
    if workspace_id:
        stmt = stmt.where(Membership.workspace_id == workspace_id)
    member = db.scalar(stmt.order_by(Membership.workspace_id))
    if not member:
        raise HTTPException(403, "Workspace access denied")
    return Principal(user.id, member.workspace_id, member.role, user.email), sess


def principal(request: Request, db: Session = Depends(session)) -> Principal:
    authorization = request.headers.get("authorization", "")
    bearer = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    token = bearer or request.cookies.get("dm_session", "")
    actor, sess = resolve(db, token, request.headers.get("x-workspace-id"))
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not bearer:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), sess.csrf):
            raise HTTPException(403, "Invalid security token; reload and try again")
    return actor


def admin(actor: Principal) -> None:
    if actor.role != "admin":
        raise HTTPException(403, "Administrator access required")


def platform_admin(actor: Principal) -> None:
    if (
        not settings().platform_admin_user_id
        or actor.user_id != settings().platform_admin_user_id
        or actor.email != settings().platform_admin_email.lower()
    ):
        raise HTTPException(403, "Platform administrator access required")


def allowed_ids(db: Session, actor: Principal) -> set[str]:
    stmt = select(Source.id).where(Source.workspace_id == actor.workspace_id, Source.status != "deleted")
    if actor.role != "admin":
        stmt = stmt.join(Grant, Grant.source_id == Source.id).where(Grant.user_id == actor.user_id)
    return set(db.scalars(stmt))


def source(db: Session, actor: Principal, source_id: str) -> Source:
    obj = db.get(Source, source_id)
    if not obj or obj.workspace_id != actor.workspace_id or obj.status == "deleted":
        raise HTTPException(404, "Source not found")
    if actor.role != "admin" and not db.get(Grant, (source_id, actor.user_id)):
        raise HTTPException(403, "Source access denied")
    return obj


def authorize_refs(db: Session, actor: Principal, refs: list[str], versions: dict | None = None) -> None:
    if not set(refs).issubset(allowed_ids(db, actor)):
        raise HTTPException(403, "Source permissions changed; this result is no longer available")
    for ref in set(refs):
        obj = db.get(Source, ref)
        if obj.status == "disconnected" or (versions is not None and versions.get(ref) != obj.version):
            raise HTTPException(403, "Source configuration changed; this result is no longer available")


def policy(db: Session, actor: Principal) -> int:
    db.expire_all()
    if not db.get(Membership, (actor.user_id, actor.workspace_id)):
        raise HTTPException(403, "Workspace access revoked")
    return db.get(Workspace, actor.workspace_id).policy_version


def audit(db: Session, actor: Principal, action: str, **details) -> None:
    db.add(Audit(user_id=actor.user_id, workspace_id=actor.workspace_id, action=action, details=details))
    db.commit()


def confined(root: Path, name: str, must_exist: bool = True) -> Path:
    root = root.resolve()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise HTTPException(400, "Path is outside the configured source directory")
    if must_exist and not candidate.is_file():
        raise HTTPException(400, "Source file not found")
    return candidate
