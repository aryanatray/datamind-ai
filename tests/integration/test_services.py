import os
from pathlib import Path

import pytest
from datamind import connectors
from datamind.schemas import MongoQuery
from dotenv import dotenv_values
from qdrant_client import QdrantClient, models

pytestmark = [
    pytest.mark.services,
    pytest.mark.skipif(
        os.getenv("RUN_SERVICE_TESTS") != "1",
        reason="Set RUN_SERVICE_TESTS=1 after loading the isolated demo services",
    ),
]
ROOT = Path(__file__).resolve().parents[2]


def configs():
    env = dotenv_values(ROOT / ".env")
    common = {
        "host": "127.0.0.1",
        "username": "aster_reader",
        "password": env["DEMO_DB_PASSWORD"],
        "database": "aster",
        "tls": False,
    }
    return {
        "postgres": {**common, "port": 15432},
        "mysql": {**common, "port": 13306},
        "mongo": {**common, "port": 17017, "auth_database": "aster"},
    }


@pytest.mark.parametrize("kind", ["postgres", "mysql", "mongo"])
def test_real_schema_discovery(kind):
    schema = connectors.discover(kind, configs()[kind])
    expected = {"postgres": "public.orders", "mysql": "invoices", "mongo": "tickets"}[kind]
    assert expected in schema
    assert schema[expected]["columns"]


def test_postgres_readonly_query():
    result = connectors.execute_sql(
        "postgres",
        configs()["postgres"],
        "SELECT SUM(revenue) AS revenue FROM public.orders WHERE customer_id='C001'",
        ["public.orders", "public.customers"],
    )
    assert result["rows"] == [{"revenue": 4620.0}]


def test_postgres_discovers_permitted_custom_schema_only():
    import uuid

    import psycopg
    from psycopg import sql

    env = dotenv_values(ROOT / ".env")
    visible = "dm_test_" + uuid.uuid4().hex
    hidden = visible + "_hidden"
    with psycopg.connect(
        host="127.0.0.1",
        port=15432,
        user="datamind",
        password=env["POSTGRES_PASSWORD"],
        dbname="aster",
        autocommit=True,
    ) as admin:
        try:
            for name in [visible, hidden]:
                admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
                admin.execute(
                    sql.SQL("CREATE TABLE {}.readable (id INTEGER PRIMARY KEY)").format(sql.Identifier(name))
                )
            admin.execute(
                sql.SQL("CREATE TABLE {}.private_table (id INTEGER)").format(sql.Identifier(visible))
            )
            admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO aster_reader").format(sql.Identifier(visible)))
            admin.execute(
                sql.SQL("GRANT SELECT ON {}.readable TO aster_reader").format(sql.Identifier(visible))
            )
            discovered = connectors.discover("postgres", configs()["postgres"])
            assert visible + ".readable" in discovered
            assert visible + ".private_table" not in discovered
            assert hidden + ".readable" not in discovered
            assert not any(
                name.startswith("pg_") or name.startswith("information_schema.") for name in discovered
            )
        finally:
            for name in [visible, hidden]:
                admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def test_mysql_readonly_query():
    result = connectors.execute_sql(
        "mysql",
        configs()["mysql"],
        "SELECT SUM(amount) AS overdue FROM invoices WHERE status='overdue'",
        ["invoices"],
    )
    assert result["rows"] == [{"overdue": 4890.0}]


def test_mongo_actual_aggregation():
    query = MongoQuery(
        collection="tickets",
        pipeline=[{"$match": {"customer_id": "C001", "status": "open"}}, {"$count": "count"}],
    )
    result = connectors.execute_mongo(configs()["mongo"], query, ["tickets"])
    assert result["rows"] == [{"count": 1}]


def test_postgres_credentials_cannot_write():
    import psycopg

    config = configs()["postgres"]
    with psycopg.connect(
        host=config["host"],
        port=config["port"],
        user=config["username"],
        password=config["password"],
        dbname="aster",
    ) as db:
        with pytest.raises((psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege)):
            db.execute("UPDATE orders SET revenue=0 WHERE false")


def test_mysql_credentials_cannot_write():
    import pymysql

    config = configs()["mysql"]
    db = pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["username"],
        password=config["password"],
        database="aster",
    )
    try:
        with db.cursor() as cursor, pytest.raises(pymysql.err.OperationalError):
            cursor.execute("UPDATE invoices SET amount=0 WHERE false")
    finally:
        db.close()


def test_mongo_credentials_cannot_write():
    import pymongo

    with connectors.mongo_database(configs()["mongo"]) as db, pytest.raises(pymongo.errors.OperationFailure):
        db.tickets.update_one({"_id": "DOES-NOT-EXIST"}, {"$set": {"status": "x"}})


def test_real_qdrant_dense_sparse_fusion():
    import uuid

    env = dotenv_values(ROOT / ".env")
    client = QdrantClient(
        url="http://localhost:16333", api_key=env["QDRANT_API_KEY"], check_compatibility=False
    )
    name = "test_" + uuid.uuid4().hex
    try:
        client.create_collection(
            name,
            vectors_config={"dense": models.VectorParams(size=3, distance=models.Distance.COSINE)},
            sparse_vectors_config={"sparse": models.SparseVectorParams()},
        )
        client.upsert(
            name,
            [
                models.PointStruct(
                    id=1,
                    vector={
                        "dense": [1.0, 0.0, 0.0],
                        "sparse": models.SparseVector(indices=[7], values=[1.0]),
                    },
                    payload={"workspace_id": "one"},
                ),
                models.PointStruct(
                    id=2,
                    vector={
                        "dense": [1.0, 0.0, 0.0],
                        "sparse": models.SparseVector(indices=[7], values=[1.0]),
                    },
                    payload={"workspace_id": "two"},
                ),
            ],
            wait=True,
        )
        filters = models.Filter(
            must=[models.FieldCondition(key="workspace_id", match=models.MatchValue(value="one"))]
        )
        hits = client.query_points(
            name,
            prefetch=[
                models.Prefetch(query=[1.0, 0.0, 0.0], using="dense", filter=filters, limit=5),
                models.Prefetch(
                    query=models.SparseVector(indices=[7], values=[1.0]),
                    using="sparse",
                    filter=filters,
                    limit=5,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=filters,
            limit=5,
        ).points
        assert [hit.id for hit in hits] == [1]
    finally:
        if client.collection_exists(name):
            client.delete_collection(name)
        client.close()
