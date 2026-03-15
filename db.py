"""Connexion PostgreSQL via psycopg2 (compatible Python 3.14)."""
import psycopg2
import psycopg2.extras
import os
from contextlib import contextmanager

DATABASE_URL = os.getenv("DATABASE_URL", "")

def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

class Database:
    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor()

    def fetchrow(self, q, *a):
        self._cur.execute(q, a)
        return self._cur.fetchone()

    def fetchval(self, q, *a):
        self._cur.execute(q, a)
        row = self._cur.fetchone()
        if row is None: return None
        return list(row.values())[0]

    def fetch(self, q, *a):
        self._cur.execute(q, a)
        return self._cur.fetchall()

    def execute(self, q, *a):
        self._cur.execute(q, a)
        self._conn.commit()

    def close(self):
        self._cur.close()
        self._conn.close()

async def get_db():
    conn = get_connection()
    db = Database(conn)
    try:
        yield db
    finally:
        db.close()

async def get_db_direct():
    conn = get_connection()
    return Database(conn)

async def init_db():
    conn = get_connection()
    db = Database(conn)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            is_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            filters JSONB DEFAULT '{}',
            active BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id SERIAL PRIMARY KEY,
            profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
            lbc_id TEXT NOT NULL,
            title TEXT,
            price NUMERIC,
            location TEXT,
            url TEXT,
            attrs JSONB DEFAULT '{}',
            found_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(profile_id, lbc_id)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_listings_profile ON listings(profile_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_profiles_active ON profiles(active) WHERE active=true")
    db.close()
