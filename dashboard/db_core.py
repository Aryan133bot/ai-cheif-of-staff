import os
import sqlite3
import re
import logging

logger = logging.getLogger(__name__)

def is_postgres():
    return bool(os.environ.get("DATABASE_URL"))

def get_connection(db_path: str):
    """Returns a universal connection object (SQLite or PostgreSQL)"""
    url = os.environ.get("DATABASE_URL")
    if url:
        import psycopg2
        from psycopg2.extras import DictCursor
        conn = psycopg2.connect(url, cursor_factory=DictCursor)
        return PgConnection(conn)
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
    def __init__(self, conn):
        self.conn = conn
        
    def execute(self, sql, params=()):
        # Convert ? to %s
        pg_sql = sql.replace("?", "%s")
        # Fix AUTOINCREMENT syntax for PostgreSQL
        pg_sql = pg_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        
        cursor = self.conn.cursor()
        cursor.execute(pg_sql, params)
        return PgCursor(cursor)
        
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


class PgCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        
    def __iter__(self):
        return iter(self.cursor)
        
    def fetchone(self):
        try:
            return self.cursor.fetchone()
        except Exception:
            return None
            
    def fetchall(self):
        try:
            return self.cursor.fetchall()
        except Exception:
            return []

    @property
    def rowcount(self):
        return self.cursor.rowcount
