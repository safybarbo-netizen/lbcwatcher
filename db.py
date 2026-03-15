"""Connexion PostgreSQL via asyncpg."""
import asyncpg, os
from functools import lru_cache

_pool = None

class Database:
    def __init__(self, conn):
        self._conn = conn
    async def fetchrow(self, q, *a): return await self._conn.fetchrow(q, *a)
    async def fetchval(self, q, *a): return await self._conn.fetchval(q, *a)
    async def fetch(self, q, *a):    return await self._conn.fetch(q, *a)
    async def execute(self, q, *a):  return await self._conn.execute(q, *a)
    async def close(self):           await self._conn.close()

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.getenv("DATABASE_URL","postgresql://lbcwatcher:password@localhost/lbcwatcher"))
    return _pool

async def get_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield Database(conn)

async def get_db_direct():
    conn = await asyncpg.connect(os.getenv("DATABASE_URL","postgresql://lbcwatcher:password@localhost/lbcwatcher"))
    return Database(conn)

async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            is_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS profiles (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            filters JSONB DEFAULT '{}',
            active BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
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
        );
        CREATE INDEX IF NOT EXISTS idx_listings_profile ON listings(profile_id);
        CREATE INDEX IF NOT EXISTS idx_profiles_active ON profiles(active) WHERE active=true;
        """)
