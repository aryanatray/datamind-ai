import sqlite3

import pytest
from datamind.connectors import execute_sql, validate_mongo, validate_sql
from datamind.schemas import MongoQuery
from fastapi import HTTPException


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM customers",
        "DROP TABLE customers",
        "UPDATE orders SET revenue=0",
        "INSERT INTO customers VALUES(3,'x')",
        "SELECT * FROM customers; SELECT * FROM orders",
        "PRAGMA database_list",
        "ATTACH DATABASE '/tmp/x' AS x",
        "SELECT load_extension('x')",
        "SELECT readfile('/etc/passwd')",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_sleep(100)",
        "SELECT * INTO OUTFILE '/tmp/x' FROM customers",
        "SELECT * FROM read_csv('/etc/passwd')",
        "WITH x AS (DELETE FROM customers RETURNING *) SELECT * FROM x",
        "SELECT * FROM secret",
        "SELECT * FROM main.customers",
        "SELECT * FROM other.customers",
        "SELECT * FROM postgres.public.customers",
        "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x",
        "SELECT randomblob(1000000000)",
        "SELECT dblink('x','SELECT 1')",
        "COPY customers TO '/tmp/x'",
        "SELECT * FROM customers FOR UPDATE",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM information_schema.tables",
    ],
)
def test_reject_unsafe_sql(query):
    with pytest.raises((HTTPException, ValueError)):
        validate_sql(query, "sqlite", ["customers", "orders"])


@pytest.mark.parametrize(
    "query,expected",
    [
        ("SELECT SUM(revenue) AS revenue FROM orders", 260),
        (
            "WITH totals AS (SELECT customer_id, SUM(revenue) AS revenue FROM orders GROUP BY customer_id) SELECT SUM(revenue) AS revenue FROM totals",
            260,
        ),
        (
            "SELECT SUM(o.revenue) AS revenue FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.name='Aster'",
            180,
        ),
        ("SELECT MAX(revenue) AS revenue FROM (SELECT revenue FROM orders) x", 120),
        (
            "SELECT SUM(revenue) AS revenue FROM orders WHERE customer_id IN (SELECT id FROM customers WHERE id=2)",
            80,
        ),
    ],
)
def test_real_sqlite_queries(environment, query, expected):
    result = execute_sql("sqlite", {"path": "aster.db"}, query, ["customers", "orders"])
    assert result["rows"][0]["revenue"] == expected


@pytest.mark.parametrize(
    "query",
    [
        {"filter": {"$where": "return true"}},
        {"pipeline": [{"$out": "stolen"}]},
        {"pipeline": [{"$merge": "stolen"}]},
        {"pipeline": [{"$lookup": {"from": "secret"}}]},
        {"pipeline": [{"$project": {"x": {"$function": {"body": "evil"}}}}]},
        {"filter": {"name": {"$regex": "(a+)+"}}},
        {"pipeline": [{"$unionWith": "secret"}]},
        {"pipeline": [{"$currentOp": {}}]},
        {"pipeline": [{"$changeStream": {}}]},
        {"pipeline": [{"$group": {"_id": None, "x": {"$accumulator": {"init": "evil"}}}}]},
    ],
)
def test_reject_unsafe_mongo(query):
    with pytest.raises(HTTPException):
        validate_mongo(MongoQuery(collection="tickets", **query), ["tickets"])


def test_mongo_collection_denied():
    with pytest.raises(HTTPException):
        validate_mongo(MongoQuery(collection="secret"), ["tickets"])


def test_safe_mongo():
    query = MongoQuery(
        collection="tickets",
        pipeline=[{"$match": {"status": "open"}}, {"$group": {"_id": "$customer_id", "count": {"$sum": 1}}}],
    )
    assert validate_mongo(query, ["tickets"]) == query


def test_row_limit(environment):
    result = execute_sql("sqlite", {"path": "aster.db"}, "SELECT * FROM orders LIMIT 999999999", ["orders"])
    assert len(result["rows"]) == 3


def test_cannot_modify_database(environment):
    with pytest.raises(HTTPException):
        execute_sql("sqlite", {"path": "aster.db"}, "DELETE FROM orders", ["orders"])
    with sqlite3.connect(environment / "sources" / "aster.db") as db:
        assert db.execute("SELECT count(*) FROM orders").fetchone()[0] == 3


def test_result_byte_limit(environment, monkeypatch):
    from datamind.config import settings

    monkeypatch.setattr(settings(), "max_result_bytes", 20)
    with pytest.raises(HTTPException, match="byte budget"):
        execute_sql("sqlite", {"path": "aster.db"}, "SELECT * FROM customers", ["customers"])
