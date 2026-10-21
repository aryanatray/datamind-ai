"""Load ONLY the disposable Aster demo database on DataMind's isolated Compose ports."""

import sys
from pathlib import Path

import psycopg
import pymongo
import pymysql
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).parent))
from generate import fixtures

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = dotenv_values(ROOT / ".env")
    password = env["DEMO_DB_PASSWORD"]
    customers, orders, invoices, tickets = fixtures()
    with psycopg.connect(
        host="127.0.0.1",
        port=15432,
        user="datamind",
        password=env["POSTGRES_PASSWORD"],
        dbname="datamind",
        autocommit=True,
    ) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='aster'").fetchone():
            conn.execute("CREATE DATABASE aster")
        from psycopg import sql

        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='aster_reader'").fetchone():
            conn.execute(sql.SQL("CREATE ROLE aster_reader LOGIN PASSWORD {}").format(sql.Literal(password)))
        else:
            conn.execute(sql.SQL("ALTER ROLE aster_reader PASSWORD {}").format(sql.Literal(password)))
    with psycopg.connect(
        host="127.0.0.1", port=15432, user="datamind", password=env["POSTGRES_PASSWORD"], dbname="aster"
    ) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY,name TEXT,segment TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,customer_id TEXT REFERENCES customers(id),month TEXT,revenue NUMERIC)"
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO customers VALUES(%(id)s,%(name)s,%(segment)s) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                customers,
            )
            cursor.executemany(
                "INSERT INTO orders VALUES(%(id)s,%(customer_id)s,%(month)s,%(revenue)s) ON CONFLICT(id) DO UPDATE SET revenue=excluded.revenue",
                orders,
            )
        conn.execute("GRANT CONNECT ON DATABASE aster TO aster_reader")
        conn.execute("GRANT USAGE ON SCHEMA public TO aster_reader")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO aster_reader")
        conn.execute("ALTER ROLE aster_reader SET default_transaction_read_only=on")
    conn = pymysql.connect(
        host="127.0.0.1", port=13306, user="root", password=env["MYSQL_ROOT_PASSWORD"], database="aster"
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS invoices(id VARCHAR(20) PRIMARY KEY,customer_id VARCHAR(20),amount DECIMAL(12,2),status VARCHAR(20),due_date DATE)"
            )
            cursor.executemany(
                "INSERT INTO invoices VALUES(%(id)s,%(customer_id)s,%(amount)s,%(status)s,%(due_date)s) ON DUPLICATE KEY UPDATE amount=VALUES(amount)",
                invoices,
            )
            cursor.execute("CREATE USER IF NOT EXISTS 'aster_reader'@'%%' IDENTIFIED BY %s", (password,))
            cursor.execute("ALTER USER 'aster_reader'@'%%' IDENTIFIED BY %s", (password,))
            cursor.execute("GRANT SELECT ON aster.* TO 'aster_reader'@'%'")
        conn.commit()
    finally:
        conn.close()
    with pymongo.MongoClient(
        host="127.0.0.1",
        port=17017,
        username="operator",
        password=env["MONGO_ROOT_PASSWORD"],
        authSource="admin",
    ) as client:
        database = client["aster"]
        for ticket in tickets:
            database.tickets.replace_one({"_id": ticket["_id"]}, ticket, upsert=True)
        if database.command("usersInfo", "aster_reader")["users"]:
            database.command(
                "updateUser", "aster_reader", pwd=password, roles=[{"role": "read", "db": "aster"}]
            )
        else:
            database.command(
                "createUser", "aster_reader", pwd=password, roles=[{"role": "read", "db": "aster"}]
            )
    print("Loaded PostgreSQL orders, MySQL invoices and MongoDB tickets with read-only reader accounts.")


if __name__ == "__main__":
    main()
