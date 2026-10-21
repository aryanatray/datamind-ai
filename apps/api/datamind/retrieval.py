import hashlib
import time
import uuid
from functools import lru_cache

from fastembed import SparseTextEmbedding, TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder
from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from datamind.config import settings
from datamind.db import Document, Source
from datamind.security import Principal, allowed_ids, authorize_refs


@lru_cache
def vector_client():
    cfg = settings()
    return (
        QdrantClient(":memory:")
        if cfg.qdrant_url == ":memory:"
        else QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key or None, timeout=20)
    )


@lru_cache
def dense_model():
    return TextEmbedding(
        settings().embedding_model,
        cache_dir=str(settings().model_cache),
        threads=2,
        providers=["CPUExecutionProvider"],
    )


@lru_cache
def sparse_model():
    return SparseTextEmbedding("Qdrant/bm25", cache_dir=str(settings().model_cache))


@lru_cache
def reranker():
    return TextCrossEncoder(
        settings().reranker_model,
        cache_dir=str(settings().model_cache),
        threads=2,
        providers=["CPUExecutionProvider"],
    )


def collection() -> str:
    # Different embedding models never share a vector space.
    return "dm_" + hashlib.sha256(settings().embedding_model.encode()).hexdigest()[:12]


def ensure_index(dimension: int) -> None:
    client = vector_client()
    if not client.collection_exists(collection()):
        try:
            client.create_collection(
                collection(),
                vectors_config={
                    "dense": models.VectorParams(size=dimension, distance=models.Distance.COSINE)
                },
                sparse_vectors_config={"sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
        except Exception:
            if not client.collection_exists(collection()):
                raise
        for key in ("workspace_id", "source_id", "version"):
            client.create_payload_index(
                collection(),
                key,
                models.PayloadSchemaType.KEYWORD if key != "version" else models.PayloadSchemaType.INTEGER,
            )


def index_chunks(source: Source, document: Document, chunks: list[dict]) -> int:
    texts = [chunk["text"] for chunk in chunks]
    dense = list(dense_model().passage_embed(texts, batch_size=16))
    sparse = list(sparse_model().passage_embed(texts, batch_size=16))
    ensure_index(len(dense[0]))
    client = vector_client()
    points = []
    for i, (chunk, dv, sv) in enumerate(zip(chunks, dense, sparse, strict=True)):
        chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source.id}/{source.version}/{i}"))
        payload = {
            **chunk,
            "chunk_id": chunk_id,
            "workspace_id": source.workspace_id,
            "source_id": source.id,
            "filename": document.filename,
            "file_type": document.extension,
            "version": source.version,
            "embedding_model": settings().embedding_model,
            "index_version": 1,
            "uploaded_at": source.created.isoformat(),
            "permission_policy": "live source ACL; never trust copied grants",
            "metadata_origin": "parser",
        }
        points.append(
            models.PointStruct(
                id=chunk_id,
                vector={
                    "dense": dv.tolist(),
                    "sparse": models.SparseVector(indices=sv.indices.tolist(), values=sv.values.tolist()),
                },
                payload=payload,
            )
        )
    for offset in range(0, len(points), 64):
        client.upsert(collection(), points[offset : offset + 64], wait=True)
    condition = models.Filter(
        must=[
            models.FieldCondition(key="source_id", match=models.MatchValue(value=source.id)),
            models.FieldCondition(key="version", match=models.MatchValue(value=source.version)),
        ]
    )
    count = client.count(collection(), count_filter=condition, exact=True).count
    if count != len(chunks):
        raise RuntimeError("Vector index verification failed")
    return count


def delete_vectors(source_id: str, version: int | None = None) -> None:
    client = vector_client()
    if client.collection_exists(collection()):
        client.delete(
            collection(),
            models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)),
                        *(
                            [models.FieldCondition(key="version", match=models.MatchValue(value=version))]
                            if version is not None
                            else []
                        ),
                    ]
                )
            ),
            wait=True,
        )


def fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(dict.fromkeys(ranking), 1):
            scores[item] = scores.get(item, 0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def search(
    db: Session,
    actor: Principal,
    query: str,
    selected: list[str] | None = None,
    strategy: str = "hybrid_rerank",
    limit: int = 6,
) -> dict:
    started = time.perf_counter()
    ids = allowed_ids(db, actor)
    if selected is not None:
        authorize_refs(db, actor, selected)
        ids.intersection_update(selected)
    sources = list(
        db.scalars(
            select(Source).where(Source.id.in_(ids), Source.kind == "document", Source.status == "ready")
        )
    )
    if not sources or not vector_client().collection_exists(collection()):
        return {"evidence": [], "stages": [], "latency_ms": 0}
    versions = {item.id: item.version for item in sources}
    conditions = [
        models.Filter(
            must=[
                models.FieldCondition(key="source_id", match=models.MatchValue(value=s.id)),
                models.FieldCondition(key="version", match=models.MatchValue(value=s.version)),
            ]
        )
        for s in sources
    ]
    filters = models.Filter(
        must=[models.FieldCondition(key="workspace_id", match=models.MatchValue(value=actor.workspace_id))],
        should=conditions,
    )
    stages = []
    results = {}
    rankings = []
    for mode in ("dense", "sparse"):
        if strategy in {"dense", "sparse"} and strategy != mode:
            continue
        tick = time.perf_counter()
        if mode == "dense":
            vector = next(dense_model().query_embed(query)).tolist()
        else:
            embedded = next(sparse_model().query_embed(query))
            vector = models.SparseVector(indices=embedded.indices.tolist(), values=embedded.values.tolist())
        hits = (
            vector_client()
            .query_points(
                collection(), query=vector, using=mode, query_filter=filters, limit=24, with_payload=True
            )
            .points
        )
        rankings.append([str(hit.id) for hit in hits])
        results.update({str(hit.id): {**hit.payload, "retrieval_score": hit.score} for hit in hits})
        stages.append(
            {"stage": mode, "candidates": len(hits), "latency_ms": round((time.perf_counter() - tick) * 1000)}
        )
    fused = fuse(rankings)
    candidates = [{**results[key], "fusion_score": score} for key, score in fused[:24]]
    if strategy == "hybrid_rerank" and candidates:
        tick = time.perf_counter()
        scores = list(reranker().rerank(query, [item["text"] for item in candidates]))
        for item, score in zip(candidates, scores, strict=True):
            item["rerank_score"] = float(score)
        candidates.sort(key=lambda item: item["rerank_score"], reverse=True)
        stages.append(
            {
                "stage": "cross_encoder",
                "candidates": len(candidates),
                "latency_ms": round((time.perf_counter() - tick) * 1000),
            }
        )
    # Recheck permissions after expensive inference, before any evidence leaves this boundary.
    db.expire_all()
    authorize_refs(db, actor, [item["source_id"] for item in candidates], versions)
    return {
        "evidence": candidates[:limit],
        "stages": stages,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
