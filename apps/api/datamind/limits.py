from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from datamind.config import settings
from datamind.db import Budget, SystemSetting
from datamind.security import Principal


def consume(db: Session, key: str, amount: int, limit: int):
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    db.execute(insert(Budget).values(key=key, used=0).on_conflict_do_nothing(index_elements=[Budget.key]))
    changed = db.execute(
        update(Budget)
        .where(Budget.key == key, Budget.used + amount <= limit)
        .values(used=Budget.used + amount)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(429, "Usage quota exceeded. Try again after the current window resets.")


def rate_limit(db: Session, actor: Principal):
    minute = datetime.now(UTC).strftime("%Y%m%d%H%M")
    cfg = settings()
    consume(db, f"minute:user:{actor.user_id}:{minute}", 1, cfg.user_requests_per_minute)
    consume(db, f"minute:workspace:{actor.workspace_id}:{minute}", 1, cfg.workspace_requests_per_minute)
    db.commit()


def reserve_tokens(db: Session, actor: Principal, upper_bound: int) -> list[str]:
    day = datetime.now(UTC).strftime("%Y%m%d")
    keys = [f"tokens:global:{day}", f"tokens:user:{actor.user_id}:{day}"]
    cfg = settings()
    global_setting = db.get(SystemSetting, "global_daily_token_limit")
    user_setting = db.get(SystemSetting, "user_daily_token_limit")
    consume(
        db, keys[0], upper_bound, global_setting.value if global_setting else cfg.global_daily_token_limit
    )
    consume(db, keys[1], upper_bound, user_setting.value if user_setting else cfg.user_daily_token_limit)
    db.commit()
    return keys


def settle_tokens(db: Session, keys: list[str], reserved: int, actual: int):
    # Failure/cancellation keeps the reservation because the provider may still charge it.
    for key in keys:
        db.execute(update(Budget).where(Budget.key == key).values(used=Budget.used + actual - reserved))
    db.commit()
