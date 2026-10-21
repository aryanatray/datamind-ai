import json
import socket
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal

import certifi
import duckdb
import pymongo
import sqlglot
from fastapi import HTTPException
from sqlalchemy import URL, create_engine, inspect, text
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from datamind.config import settings
from datamind.schemas import MongoQuery
from datamind.security import confined

DIALECTS = {"postgres": "postgres", "mysql": "mysql", "sqlite": "sqlite", "table": "duckdb"}
SAFE_FUNCTIONS = {
    "AND",
    "OR",
    "NOT",
    "CASE",
    "EXISTS",
    "ABS",
    "AVG",
    "COUNT",
    "SUM",
    "MIN",
    "MAX",
    "ROUND",
    "COALESCE",
    "NULLIF",
    "LOWER",
    "UPPER",
    "LENGTH",
    "CHAR_LENGTH",
    "SUBSTRING",
    "TRIM",
    "CONCAT",
    "CONCAT_WS",
    "REPLACE",
    "CAST",
    "TRY_CAST",
    "DATE",
    "DATETIME",
    "STRFTIME",
    "DATE_TRUNC",
    "TIMESTAMP_TRUNC",
    "EXTRACT",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
    "DATE_ADD",
    "DATE_SUB",
    "DATEDIFF",
    "DATE_DIFF",
    "TIMESTAMPDIFF",
    "IF",
    "ROW_NUMBER",
    "RANK",
    "DENSE_RANK",
    "LAG",
    "LEAD",
    "FIRST_VALUE",
    "LAST_VALUE",
    "NTILE",
    "STDDEV",
    "STDDEV_POP",
    "VARIANCE",
    "MEDIAN",
    "PERCENTILE_CONT",
    "PERCENTILE_DISC",
    "GREATEST",
    "LEAST",
    "STR_POSITION",
    "TIME_TO_STR",
    "TS_OR_DS_TO_DATE",
    "YEAR",
    "MONTH",
    "DAY",
    "CEIL",
    "FLOOR",
    "POWER",
    "SQRT",
    "MOD",
    "IFNULL",
    "IIF",
    "JULIANDAY",
    "DATE_STR_TO_DATE",
    "TS_OR_DS_ADD",
    "TS_OR_DS_DIFF",
}


def validate_sql(sql: str, kind: str, resources: list[str], row_limit: int | None = None) -> str:
    if kind not in DIALECTS:
        raise HTTPException(400, "This source does not support SQL")
    try:
        statements = sqlglot.parse(sql, read=DIALECTS[kind])
    except sqlglot.errors.ParseError as exc:
        raise HTTPException(400, "Invalid SQL syntax") from exc
    if len(statements) != 1 or not isinstance(
        statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)
    ):
        raise HTTPException(400, "Only a single read-only SELECT is allowed")
    tree = statements[0]
    forbidden = {
        "Insert",
        "Update",
        "Delete",
        "Create",
        "Drop",
        "Alter",
        "Command",
        "Copy",
        "Into",
        "Lock",
        "Attach",
        "Detach",
        "Pragma",
        "Transaction",
        "Set",
        "Use",
        "Merge",
        "LoadData",
        "Execute",
        "Parameter",
        "SessionParameter",
        "Hint",
        "TableSample",
    }
    for node in tree.walk():
        if type(node).__name__ in forbidden:
            raise HTTPException(400, "Forbidden SQL operation")
        if isinstance(node, exp.Func):
            fn = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            if fn not in SAFE_FUNCTIONS:
                raise HTTPException(400, f"Function {fn} is not allowed")
    if tree.args.get("with_") and tree.args["with_"].args.get("recursive"):
        raise HTTPException(400, "Recursive queries are disabled")
    allowed = set(resources)
    for scope in traverse_scope(tree):
        for _, (_, target) in scope.selected_sources.items():
            if isinstance(target, exp.Table):
                if not isinstance(target.this, exp.Identifier) or target.catalog:
                    raise HTTPException(400, "External tables are disabled")
                name = f"{target.db}.{target.name}" if target.db else target.name
                if name not in allowed:
                    raise HTTPException(403, f"Resource {name} is not authorized")
    limit = row_limit or settings().max_query_rows
    # An outer limit also bounds UNION and CTE results, without altering aggregate inputs.
    limited = exp.select("*").from_(tree.subquery("dm_result")).limit(limit)
    return limited.sql(dialect=DIALECTS[kind])


def host(config: dict) -> str:
    value = config["host"].lower().rstrip(".")
    allowed = {x.strip().lower() for x in settings().db_allowed_hosts.split(",")}
    if value not in allowed or any(x in value for x in "/@\\?#"):
        raise HTTPException(400, "Database host must be explicitly allowed by the platform operator")
    addresses = socket.getaddrinfo(value, config["port"], type=socket.SOCK_STREAM)
    if not addresses:
        raise HTTPException(400, "Database host could not be resolved")
    # Pin the address used for this connection; disallow URL-style driver options entirely.
    return addresses[0][4][0]


def sql_engine(kind: str, config: dict):
    address = host(config)
    args = {"connect_timeout": 5}
    if kind == "postgres":
        args["sslmode"] = "verify-full" if config.get("tls") else "disable"
        if config.get("tls"):
            args.update(hostaddr=address, sslrootcert=settings().db_ca_file or certifi.where())
    elif config.get("tls"):
        args.update(
            ssl_ca=settings().db_ca_file or certifi.where(), ssl_verify_cert=True, ssl_verify_identity=True
        )
    url = URL.create(
        "postgresql+psycopg" if kind == "postgres" else "mysql+pymysql",
        username=config["username"],
        password=config["password"],
        host=config["host"] if config.get("tls") else address,
        port=config["port"],
        database=config["database"],
    )
    return create_engine(url, connect_args=args, pool_pre_ping=True, pool_size=1, max_overflow=0)


@contextmanager
def mongo_database(config: dict):
    address = host(config)
    client = pymongo.MongoClient(
        host=config["host"] if config.get("tls") else address,
        port=config["port"],
        username=config["username"] or None,
        password=config["password"] or None,
        authSource=config.get("auth_database", "admin"),
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=settings().query_timeout_seconds * 1000,
        tls=config.get("tls", False),
        **({"tlsCAFile": settings().db_ca_file or certifi.where()} if config.get("tls") else {}),
        maxPoolSize=2,
    )
    try:
        yield client[config["database"]]
    finally:
        client.close()


def serial(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return "[binary]"
    return str(value)


def clean(value):
    return json.loads(json.dumps(value, default=serial, allow_nan=False))


def bounded_rows(iterator) -> list[dict]:
    rows, total = [], 0
    for row in iterator:
        item = clean(row)
        total += len(json.dumps(item, ensure_ascii=False).encode())
        if total > settings().max_result_bytes:
            raise HTTPException(
                400, "Result exceeds the byte budget. Select fewer columns, filter or aggregate."
            )
        rows.append(item)
        if len(rows) >= settings().max_query_rows:
            break
    return rows


def discover(kind: str, config: dict) -> dict:
    if kind == "mongo":
        with mongo_database(config) as db:
            result = {}
            for collection in db.list_collection_names()[:100]:
                fields: dict[str, set] = {}
                for doc in db[collection].find().limit(20).max_time_ms(3000):
                    for key, value in doc.items():
                        fields.setdefault(key, set()).add(type(value).__name__)
                result[collection] = {
                    "columns": [{"name": k, "type": "|".join(sorted(v))} for k, v in fields.items()],
                    "inferred_from": "At most 20 documents; not a full schema",
                }
            return result
    if kind == "sqlite":
        path = confined(settings().sqlite_root, config["path"])
        engine = create_engine(f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true")
    else:
        engine = sql_engine(kind, config)
    try:
        result = {}
        with engine.connect() as connection:
            inspector = inspect(connection)
            schema_names = [None]
            if kind == "postgres":
                schema_names = [
                    name
                    for name in inspector.get_schema_names()
                    if name != "information_schema"
                    and not name.startswith("pg_")
                    and connection.scalar(text("SELECT has_schema_privilege(:name, 'USAGE')"), {"name": name})
                ]
            for schema in schema_names:
                for table in inspector.get_table_names(schema=schema)[:100]:
                    if len(result) >= 100:
                        break
                    if kind == "postgres" and not connection.scalar(
                        text(
                            "SELECT has_table_privilege("
                            "format('%I.%I', CAST(:schema AS text), CAST(:table AS text)), 'SELECT')"
                        ),
                        {"schema": schema, "table": table},
                    ):
                        continue
                    name = f"{schema}.{table}" if schema else table
                    result[name] = {
                        "columns": [
                            {"name": c["name"], "type": str(c["type"]), "nullable": c["nullable"]}
                            for c in inspector.get_columns(table, schema=schema)
                        ],
                        "relationships": inspector.get_foreign_keys(table, schema=schema),
                    }
        return clean(result)
    finally:
        engine.dispose()


def execute_sql(kind: str, config: dict, query: str, resources: list[str]) -> dict:
    sql = validate_sql(query, kind, resources)
    started = time.perf_counter()
    timeout = settings().query_timeout_seconds
    if kind == "sqlite":
        path = confined(settings().sqlite_root, config["path"])
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=timeout) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.enable_load_extension(False)

            def authorize(action, arg1, arg2, database, trigger):
                if action == sqlite3.SQLITE_READ:
                    return (
                        sqlite3.SQLITE_OK
                        if arg1 in resources and (database == "main" or (database is None and arg2 == ""))
                        else sqlite3.SQLITE_DENY
                    )
                return (
                    sqlite3.SQLITE_OK
                    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION}
                    else sqlite3.SQLITE_DENY
                )

            conn.set_authorizer(authorize)
            conn.set_progress_handler(lambda: int(time.perf_counter() - started > timeout), 1000)
            cursor = conn.execute(sql)
            columns = [x[0] for x in cursor.description]
            rows = bounded_rows(dict(zip(columns, row, strict=True)) for row in cursor)
    elif kind == "table":
        path = confined(settings().storage_root, config["path"])
        with duckdb.connect(
            str(path),
            read_only=True,
            config={"enable_external_access": "false", "threads": "2", "memory_limit": "256MB"},
        ) as conn:
            import threading

            timer = threading.Timer(timeout, conn.interrupt)
            timer.start()
            try:
                cursor = conn.execute(sql)
                columns = [x[0] for x in cursor.description]
                rows = bounded_rows(
                    dict(zip(columns, row, strict=True))
                    for row in cursor.fetchmany(settings().max_query_rows)
                )
            finally:
                timer.cancel()
    else:
        engine = sql_engine(kind, config)
        try:
            with engine.connect() as conn, conn.begin():
                if kind == "postgres":
                    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                    conn.exec_driver_sql(f"SET LOCAL statement_timeout = {timeout * 1000}")
                else:
                    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                    conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME={timeout * 1000}")
                result = conn.execution_options(stream_results=True).execute(text(sql))
                columns = list(result.keys())
                rows = bounded_rows(dict(row._mapping) for row in result)
                result.close()
        finally:
            engine.dispose()
    return {
        "columns": columns,
        "rows": clean(rows),
        "sql": sql,
        "truncated": len(rows) == settings().max_query_rows,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


MONGO_KEYS = {
    "$match",
    "$project",
    "$sort",
    "$group",
    "$limit",
    "$skip",
    "$count",
    "$unwind",
    "$eq",
    "$ne",
    "$gt",
    "$gte",
    "$lt",
    "$lte",
    "$in",
    "$nin",
    "$and",
    "$or",
    "$not",
    "$nor",
    "$exists",
    "$type",
    "$sum",
    "$avg",
    "$min",
    "$max",
    "$first",
    "$last",
    "$push",
    "$addToSet",
    "$cond",
    "$ifNull",
    "$subtract",
    "$add",
    "$multiply",
    "$divide",
    "$year",
    "$month",
    "$dayOfMonth",
    "$dateToString",
    "$toString",
    "$literal",
    "$expr",
    "$size",
    "$elemMatch",
}
MONGO_STAGES = {"$match", "$project", "$sort", "$group", "$limit", "$skip", "$count", "$unwind"}


def validate_mongo(query: MongoQuery, resources: list[str]) -> MongoQuery:
    if query.collection not in resources:
        raise HTTPException(403, "Collection access denied")
    raw = query.model_dump()
    if len(json.dumps(raw)) > 16000:
        raise HTTPException(400, "MongoDB query too large")

    def walk(value, depth=0):
        if depth > 12:
            raise HTTPException(400, "MongoDB query nesting limit exceeded")
        if isinstance(value, dict):
            for key, item in value.items():
                if key.startswith("$") and key not in MONGO_KEYS:
                    raise HTTPException(400, f"MongoDB operator {key} is disabled")
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    walk(raw)
    for stage in query.pipeline:
        if len(stage) != 1 or next(iter(stage)) not in MONGO_STAGES:
            raise HTTPException(400, "Unsafe MongoDB stage")
        if "$limit" in stage and (not isinstance(stage["$limit"], int) or not 1 <= stage["$limit"] <= 500):
            raise HTTPException(400, "Invalid aggregation limit")
    return query


def execute_mongo(config: dict, query: MongoQuery, resources: list[str]) -> dict:
    query = validate_mongo(query, resources)
    started = time.perf_counter()
    with mongo_database(config) as db:
        collection = db[query.collection]
        if query.pipeline:
            cursor = collection.aggregate(
                [*query.pipeline, {"$limit": min(query.limit, settings().max_query_rows)}],
                maxTimeMS=settings().query_timeout_seconds * 1000,
                allowDiskUse=False,
            )
        else:
            cursor = (
                collection.find(query.filter, query.projection or None)
                .skip(query.skip)
                .limit(min(query.limit, settings().max_query_rows))
                .max_time_ms(settings().query_timeout_seconds * 1000)
            )
            if query.sort:
                cursor = cursor.sort(list(query.sort.items()))
        rows = bounded_rows(cursor)
        cursor.close()
    return {
        "rows": rows,
        "columns": sorted({key for row in rows for key in row}),
        "mongo": query.model_dump(),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
