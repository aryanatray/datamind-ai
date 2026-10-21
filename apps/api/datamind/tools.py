import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from datamind import connectors, retrieval
from datamind.config import settings
from datamind.db import Source
from datamind.schemas import MongoQuery
from datamind.security import Principal, allowed_ids, audit, decrypt, source


def metadata(obj: Source) -> dict:
    schema = obj.catalog.get("schema", {})
    return {
        "id": obj.id,
        "name": obj.name,
        "kind": obj.kind,
        "status": obj.status,
        "resources": obj.resources,
        "catalog": {
            **obj.catalog,
            "schema": {key: value for key, value in schema.items() if key in obj.resources},
        },
    }


def list_connections(db: Session, actor: Principal) -> list[dict]:
    return [metadata(obj) for obj in db.scalars(select(Source).where(Source.id.in_(allowed_ids(db, actor))))]


def execute_sql(db: Session, actor: Principal, source_id: str, sql: str) -> dict:
    obj = source(db, actor, source_id)
    if obj.kind not in connectors.DIALECTS or obj.status != "ready":
        raise HTTPException(400, "This source is not ready for SQL")
    version = obj.version
    started = time.perf_counter()
    try:
        result = connectors.execute_sql(obj.kind, decrypt(obj.secret), sql, obj.resources)
    except HTTPException:
        audit(db, actor, "sql_denied", source_id=source_id)
        raise
    except Exception as exc:
        audit(db, actor, "sql_failed", source_id=source_id, category=type(exc).__name__)
        raise HTTPException(
            400, f"Query execution failed ({type(exc).__name__}); check syntax, types, and schema"
        ) from exc
    db.expire_all()
    if source(db, actor, source_id).version != version:
        raise HTTPException(403, "Source configuration changed during execution")
    audit(
        db,
        actor,
        "sql_executed",
        source_id=source_id,
        rows=len(result["rows"]),
        latency_ms=round((time.perf_counter() - started) * 1000),
    )
    return {**result, "source_id": source_id, "original_sql": sql}


def execute_mongo(db: Session, actor: Principal, source_id: str, query: MongoQuery) -> dict:
    obj = source(db, actor, source_id)
    if obj.kind != "mongo" or obj.status != "ready":
        raise HTTPException(400, "MongoDB source is not ready")
    version = obj.version
    try:
        result = connectors.execute_mongo(decrypt(obj.secret), query, obj.resources)
    except HTTPException:
        raise
    except Exception as exc:
        audit(db, actor, "mongo_failed", source_id=source_id, category=type(exc).__name__)
        raise HTTPException(400, f"MongoDB execution failed ({type(exc).__name__})") from exc
    db.expire_all()
    if source(db, actor, source_id).version != version:
        raise HTTPException(403, "Source configuration changed during execution")
    audit(db, actor, "mongo_executed", source_id=source_id, rows=len(result["rows"]))
    return {**result, "source_id": source_id}


def evidence(db: Session, actor: Principal, chunk_id: str) -> dict:
    client = retrieval.vector_client()
    if not client.collection_exists(retrieval.collection()):
        raise HTTPException(404, "Evidence not found")
    # Discovering a point by ID never returns its payload before source authorization.
    points = client.retrieve(retrieval.collection(), [chunk_id], with_payload=True, with_vectors=False)
    if not points:
        raise HTTPException(404, "Evidence not found")
    payload = points[0].payload
    obj = source(db, actor, payload["source_id"])
    if obj.version != payload["version"] or obj.status != "ready":
        raise HTTPException(404, "Evidence is from an outdated index")
    return payload


def web_search(query: str) -> list[dict]:
    # Only this explicitly confirmed public query is transmitted. No chat history or private tool output.
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
        response = client.get(
            settings().searxng_url.rstrip("/") + "/search", params={"q": query, "format": "json"}
        )
        response.raise_for_status()
        result = []
        for item in response.json().get("results", [])[:8]:
            url = item.get("url", "")
            if urlsplit(url).scheme not in {"http", "https"}:
                continue
            result.append(
                {
                    "title": item.get("title", "")[:300],
                    "url": url,
                    "text": item.get("content", "")[:2000],
                    "origin": "public_web",
                }
            )
        return result
