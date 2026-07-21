-- ─────────────────────────────────────────────────────────────────────────────
-- init.sql — least-privilege setup
--
-- Entrypoint execution order (postgres:16-alpine):
--   1. initdb
--   2. CREATE ROLE appuser SUPERUSER LOGIN  ← POSTGRES_USER=appuser
--      SET PASSWORD <from POSTGRES_PASSWORD K8s Secret>
--   3. CREATE DATABASE appdb OWNER appuser  ← POSTGRES_DB=appdb
--   4. Run /docker-entrypoint-initdb.d/01-init.sql  ← this file, via
--      `psql --set ON_ERROR_STOP=1` — any failing statement aborts every
--      statement after it, silently, for the rest of this script.
--
-- Both appuser and appdb already exist when this script runs. Schema/table
-- setup runs FIRST, below, while appuser still holds the entrypoint's
-- original SUPERUSER grant — not after stripping it. The original version
-- of this script stripped superuser (Step 1) before creating app_data
-- (Step 4), on the assumption that ownership-based privileges pick up
-- immediately and unconditionally after ALTER USER in the same session;
-- when that assumption doesn't hold, ON_ERROR_STOP=1 aborts the script and
-- app_data never gets created — first-boot data loss, and every query
-- against a table that was never there. Reordering removes the dependency
-- on that assumption entirely: nothing below needs any privilege beyond
-- what the entrypoint already granted.
-- ─────────────────────────────────────────────────────────────────────────────

-- Step 1: appdb already exists (entrypoint created it, owned by appuser).
--         Connect to configure schema and table.
\c appdb

-- Step 2: Application table.
CREATE TABLE IF NOT EXISTS app_data (
    id         SERIAL PRIMARY KEY,
    key        VARCHAR(255) NOT NULL UNIQUE,
    value      TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Step 3: Lock down the public schema.
--         PostgreSQL 15+ owns the public schema via pg_database_owner,
--         which resolves to appuser (the DB owner) — REVOKE succeeds
--         without superuser privilege, but do it here anyway, before the
--         privilege strip, rather than depend on that holding true after.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT  USAGE  ON SCHEMA public TO appuser;

-- Minimum grants — NO DELETE by design
GRANT SELECT, INSERT, UPDATE ON app_data                TO appuser;
GRANT USAGE,  SELECT         ON SEQUENCE app_data_id_seq TO appuser;

-- Step 4: Strip every elevated privilege the entrypoint granted, now that
--         schema/table setup no longer needs it. Add CONNECTION LIMIT here
--         too — the entrypoint doesn't set one.
ALTER USER appuser WITH
    NOSUPERUSER         -- remove bypass-access-checks (entrypoint default)
    NOCREATEDB          -- cannot create additional databases
    NOCREATEROLE        -- cannot create other roles
    NOINHERIT           -- does not inherit role privileges
    LOGIN
    CONNECTION LIMIT 20;
