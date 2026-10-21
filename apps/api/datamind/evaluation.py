"""Deterministic golden evaluation and real retrieval ablations; never synthetic LLM scores."""

import argparse
import json
import math
import os
import platform
import statistics
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[3]


def metrics(retrieved: list[str], relevant: list[str], k: int = 6) -> dict:
    hits = [int(item in relevant) for item in retrieved[:k]]
    recall = len(set(retrieved[:k]).intersection(relevant)) / len(relevant) if relevant else 0
    mrr = next((1 / (i + 1) for i, hit in enumerate(hits) if hit), 0)
    dcg = sum(hit / math.log2(i + 2) for i, hit in enumerate(hits))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return {"recall_at_k": recall, "mrr": mrr, "ndcg": dcg / ideal if ideal else 0}


def hardware():
    return {
        "os": platform.platform(),
        "logical_cpu": os.cpu_count(),
        "ram_gib": round(psutil.virtual_memory().total / 1024**3, 2),
        "inference": "CPU / ONNX Runtime, 2 threads",
    }


def golden(questions):
    from datamind.connectors import execute_sql, validate_mongo
    from datamind.schemas import MongoQuery

    results = []
    for item in questions:
        tick = time.perf_counter()
        if item["category"] == "sql":
            answer = execute_sql(
                "sqlite", {"path": "aster.db"}, item["sql"], ["orders", "customers", "invoices"]
            )
            actual = answer["rows"][0]["revenue"]
            results.append(
                {
                    "id": item["id"],
                    "reference_result_correct": actual == item["expected"],
                    "latency_ms": (time.perf_counter() - tick) * 1000,
                }
            )
        elif item["category"] == "mongo":
            validate_mongo(MongoQuery.model_validate(item["mongo"]), ["tickets"])
            results.append(
                {
                    "id": item["id"],
                    "reference_query_valid": True,
                    "execution": "not run in offline golden evaluation",
                }
            )
    return {
        "mode": "golden_reference_queries_not_model_generation",
        "cases": results,
        "passed_sql": sum(r.get("reference_result_correct", False) for r in results),
        "sql_cases": sum("reference_result_correct" in r for r in results),
        "validated_mongo_specs": sum("reference_query_valid" in r for r in results),
    }


def retrieval_benchmark(questions):
    if os.getenv("EVAL_DEBUG") == "1":
        import faulthandler

        faulthandler.dump_traceback_later(45, repeat=True)
    # This temporary evaluation workspace exercises the real application index/search code.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from datamind import retrieval
    from datamind.db import Base, Document, Membership, Source, User, Workspace, uid
    from datamind.ingestion import chunk
    from datamind.security import Principal

    # A fresh collection keeps BM25 IDF independent of application uploads and previous benchmark runs.
    original_collection = retrieval.collection
    evaluation_collection = "eval_" + uid().replace("-", "")
    retrieval.collection = lambda: evaluation_collection

    corpus = json.loads((ROOT / "datasets/generated/retrieval-corpus.json").read_text())
    database = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(database)
    lookup, source_ids = {}, []
    process = psutil.Process()
    model_tick = time.perf_counter()
    retrieval.dense_model()
    retrieval.sparse_model()
    retrieval.reranker()
    model_load_ms = (time.perf_counter() - model_tick) * 1000
    print(f"Loaded actual retrieval models in {model_load_ms:.0f} ms", flush=True)
    with Session(database, expire_on_commit=False) as db:
        user = User(email="evaluation@invalid.test", name="Evaluation", password="not-a-login")
        workspace = Workspace(name="Ephemeral evaluation", mode="personal", capacity=1)
        db.add_all([user, workspace])
        db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
        actor = Principal(user.id, workspace.id, "admin", user.email)
        try:
            index_tick = time.perf_counter()
            for entry in corpus:
                source = Source(
                    id=uid(),
                    workspace_id=workspace.id,
                    name=entry["id"],
                    kind="document",
                    status="ready",
                    version=1,
                )
                db.add(source)
                db.flush()
                document = Document(
                    id=source.id,
                    filename=entry["id"] + ".md",
                    extension=".md",
                    path="evaluation-only",
                    sha256="0" * 64,
                    size=len(entry["text"]),
                )
                db.add(document)
                db.commit()
                source_ids.append(source.id)
                lookup[source.id] = entry["id"]
                retrieval.index_chunks(
                    source, document, chunk([{"text": entry["text"], "page": None, "heading": entry["id"]}])
                )
            index_ms = (time.perf_counter() - index_tick) * 1000
            print(f"Indexed {len(source_ids)} synthetic documents in {index_ms:.0f} ms", flush=True)
            output = []
            cases = [q for q in questions if q["category"] == "rag"]
            for strategy in ("dense", "sparse", "hybrid", "hybrid_rerank"):
                print(f"Evaluating {strategy}", flush=True)
                case_results = []
                for question in cases:
                    tick = time.perf_counter()
                    result = retrieval.search(db, actor, question["question"], source_ids, strategy)
                    retrieved = list(dict.fromkeys(lookup[item["source_id"]] for item in result["evidence"]))
                    case_results.append(
                        {
                            "id": question["id"],
                            **metrics(retrieved, question["relevant_docs"]),
                            "latency_ms": (time.perf_counter() - tick) * 1000,
                        }
                    )
                    if len(case_results) % 5 == 0:
                        print(f"  {len(case_results)}/{len(cases)} queries", flush=True)
                output.append(
                    {
                        "strategy": strategy,
                        "cases": len(case_results),
                        **{
                            key: statistics.mean(case[key] for case in case_results)
                            for key in ("recall_at_k", "mrr", "ndcg", "latency_ms")
                        },
                        "p95_latency_ms": sorted(case["latency_ms"] for case in case_results)[
                            math.ceil(0.95 * len(case_results)) - 1
                        ],
                        "per_case": case_results,
                    }
                )
            # Verify the same real index yields nothing to another workspace.
            other = Workspace(name="Unauthorized evaluation", mode="personal", capacity=1)
            db.add(other)
            db.flush()
            db.add(Membership(user_id=user.id, workspace_id=other.id, role="member"))
            db.commit()
            outsider = Principal(user.id, other.id, "member", user.email)
            denied = retrieval.search(db, outsider, "C001 renewal")
            assert denied["evidence"] == []
            from datamind.config import settings

            cfg = settings()
            size = sum(p.stat().st_size for p in cfg.model_cache.rglob("*") if p.is_file())
            return {
                "model": cfg.embedding_model,
                "reranker": cfg.reranker_model,
                "sparse": "Qdrant/bm25",
                "corpus_isolation": "Fresh Qdrant collection per run; no application or earlier-run IDF statistics",
                "model_load_ms": model_load_ms,
                "index_ms": index_ms,
                "process_rss_mib": process.memory_info().rss / 1024**2,
                "model_cache_mib": size / 1024**2,
                "tenant_filter_test": "passed",
                "ablations": output,
                "limitations": "20 template-based synthetic queries, not held-out market or answer-quality validation. CPU only. Index size not measured independently of shared Qdrant storage.",
            }
        finally:
            if retrieval.vector_client().collection_exists(evaluation_collection):
                retrieval.vector_client().delete_collection(evaluation_collection)
            retrieval.collection = original_collection
            db.close()
            database.dispose()


def parser_benchmark():
    from datamind.ingestion import parse

    results = []
    for filename, extension in [
        ("contracts.pdf", ".pdf"),
        ("renewal-review.docx", ".docx"),
        ("meeting-notes.txt", ".txt"),
    ]:
        tick = time.perf_counter()
        blocks = parse(ROOT / "datasets/generated" / filename, extension)
        text = "\n".join(b["text"] for b in blocks)
        expected = (
            [f"C{i:03}" for i in range(1, 21)]
            if extension in {".pdf", ".docx"}
            else ["2026-07-01", "booked orders"]
        )
        results.append(
            {
                "file": filename,
                "blocks": len(blocks),
                "expected_anchors": len(expected),
                "retained_anchors": sum(anchor in text for anchor in expected),
                "latency_ms": (time.perf_counter() - tick) * 1000,
            }
        )
    return {
        "baseline": results,
        "docling": "Not executed; optional comparison requires installing the research extra and a representative held-out corpus",
        "ocr": "Not implemented; scanned PDFs fail explicitly rather than silently indexing empty content",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--retrieval", action="store_true", help="Download real model weights and execute Qdrant ablations"
    )
    parser.add_argument("--output", default="docs/benchmark-results.json")
    parser.add_argument("--embedding", help="An explicitly selected FastEmbed-supported HF model")
    parser.add_argument("--reranker", help="An explicitly selected FastEmbed cross-encoder")
    args = parser.parse_args()
    if args.embedding:
        os.environ["EMBEDDING_MODEL"] = args.embedding
    if args.reranker:
        os.environ["RERANKER_MODEL"] = args.reranker
    questions = json.loads((ROOT / "datasets/benchmark.json").read_text())
    output = {
        "hardware": hardware(),
        "dataset_cases": len(questions),
        "dataset_provenance": "Deterministic synthetic templates; training/test independence not established",
        "golden": golden(questions),
        "parsing": parser_benchmark(),
        "llm_generation": "Not executed; no Gemini calls made",
        "retrieval": "Not executed",
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(output, indent=2))
    if args.retrieval:
        output["retrieval"] = retrieval_benchmark(questions)
        Path(args.output).write_text(json.dumps(output, indent=2))
    print(f"Recorded real evaluation results in {args.output}")


if __name__ == "__main__":
    main()
