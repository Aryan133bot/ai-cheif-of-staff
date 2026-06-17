import os
import sqlite3
import re
import logging

logger = logging.getLogger(__name__)

def is_postgres():
    return bool(os.environ.get("DATABASE_URL"))

_pg_pool = None

def get_connection(db_path: str):
    """Returns a universal connection object (SQLite or PostgreSQL)"""
    url = os.environ.get("DATABASE_URL")
    if url:
        global _pg_pool
        if _pg_pool is None:
            from psycopg2.pool import SimpleConnectionPool
            _pg_pool = SimpleConnectionPool(1, 20, url)
        conn = _pg_pool.getconn()
        return PgConnection(conn, pool=_pg_pool)
    else:
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return SqliteConnection(conn)


class SqliteConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)
        
    def commit(self):
        self.conn.commit()
        
    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        self.close()


class PgConnection:
    def __init__(self, conn, pool=None):
        self.conn = conn
        self.pool = pool
        
    def execute(self, sql, params=()):
        # Convert ? to %s safely ignoring quotes
        parts = sql.split("'")
        for i in range(0, len(parts), 2):
            parts[i] = parts[i].replace("?", "%s")
        pg_sql = "'".join(parts)
        
        # Fix AUTOINCREMENT syntax for PostgreSQL
        pg_sql = pg_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        
        from psycopg2.extras import DictCursor
        cursor = self.conn.cursor(cursor_factory=DictCursor)
        cursor.execute(pg_sql, params)
        return PgCursor(cursor)
        
    def commit(self):
        self.conn.commit()
        
    def close(self):
        if self.pool:
            self.pool.putconn(self.conn)
        else:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        else:
            self.conn.rollback()
        self.close()


class PgCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        
    def __iter__(self):
        return iter(self.cursor)
        
    def fetchone(self):
        if self.cursor.description:
            return self.cursor.fetchone()
        return None
            
    def fetchall(self):
        if self.cursor.description:
            return self.cursor.fetchall()
        return []

    @property
    def rowcount(self):
        return self.cursor.rowcount
