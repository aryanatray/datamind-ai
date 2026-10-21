import logging
import time
from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from datamind.config import settings
from datamind.db import Document, Job, Source, engine, now
from datamind.ingestion import chunk, parse, tabular
from datamind.retrieval import delete_vectors, index_chunks
from datamind.security import confined, encrypt

log = logging.getLogger("datamind.worker")


def claim(db: Session) -> str | None:
    exhausted = list(
        db.scalars(select(Job).where(Job.state == "running", Job.attempts >= 3, Job.lease_until < now()))
    )
    for job in exhausted:
        job.state, job.stage, job.error = (
            "failed",
            "failed",
            "Worker lease expired after three attempts; reindex to retry",
        )
        db.execute(
            update(Source)
            .where(Source.id == job.source_id, Source.version == job.version, Source.status != "deleted")
            .values(status="failed")
        )
    if exhausted:
        db.commit()
    eligible = and_(
        Job.attempts < 3, or_(Job.state == "queued", and_(Job.state == "running", Job.lease_until < now()))
    )
    job_id = db.scalar(select(Job.id).where(eligible).order_by(Job.created).limit(1))
    if not job_id:
        return None
    changed = db.execute(
        update(Job)
        .where(Job.id == job_id, eligible)
        .values(
            state="running",
            stage="parsing",
            attempts=Job.attempts + 1,
            lease_until=now() + timedelta(minutes=30),
        )
    )
    db.commit()
    return job_id if changed.rowcount == 1 else None


def process(job_id: str) -> None:
    with Session(engine(), expire_on_commit=False) as db:
        job = db.get(Job, job_id)
        source = db.get(Source, job.source_id)
        document = db.get(Document, job.source_id)
        if not source or not document or source.version != job.version:
            job.state = "superseded"
            db.commit()
            return
        try:
            job.stage = "extracting"
            db.commit()
            path = confined(settings().storage_root, document.path)
            changes = {"status": "ready"}
            if document.extension in {".csv", ".xlsx"}:
                target_name = f"{source.id}-{source.version}.duckdb"
                profile = tabular(path, document.extension, settings().storage_root / target_name)
                document.profile = profile
                changes["kind"] = "table"
                changes["resources"] = list(profile)
                changes["secret"] = encrypt({"path": target_name})
                changes["catalog"] = {
                    **source.catalog,
                    "schema": {key: {"columns": value["columns"]} for key, value in profile.items()},
                }
            else:
                blocks = parse(path, document.extension)
                if sum(len(b["text"]) for b in blocks) > 10_000_000:
                    raise ValueError("Extracted text exceeds 10 MB")
                document.extracted = blocks
                job.stage = "chunking"
                db.commit()
                chunks = chunk(blocks)
                job.stage = "embedding_and_indexing"
                db.commit()
                delete_vectors(source.id, version=job.version)
                count = index_chunks(source, document, chunks)
                document.profile = {"chunks": count, "parser": "pypdf/python-docx/UTF-8", "ocr": False}
            # A delete or reindex may have happened while models were running.
            changed = db.execute(
                update(Source)
                .where(Source.id == source.id, Source.version == job.version, Source.status != "deleted")
                .values(**changes),
                execution_options={"synchronize_session": False},
            )
            if changed.rowcount != 1:
                db.rollback()
                job = db.get(Job, job_id)
                job.state = "superseded"
            else:
                job.state = "succeeded"
                job.stage = "verified"
                job.error = ""
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            source = db.get(Source, job.source_id) if job else None
            if job:
                job.state = "failed"
                job.stage = "failed"
                # Parser failures are safe to show; driver/model errors are categorized without credentials.
                job.error = (
                    str(exc)[:400]
                    if isinstance(exc, ValueError)
                    else f"{type(exc).__name__}: ingestion failed; retry or inspect operator logs"
                )
            if source and source.version == job.version and source.status != "deleted":
                source.status = "failed"
            db.commit()
            log.warning("ingestion_failed job=%s category=%s", job_id, type(exc).__name__)


def run():
    logging.basicConfig(level=logging.INFO)
    while True:
        with Session(engine()) as db:
            job_id = claim(db)
        if job_id:
            process(job_id)
        else:
            time.sleep(1)


if __name__ == "__main__":
    run()
