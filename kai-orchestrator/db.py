import sqlite3, json, uuid, datetime
from pathlib import Path

DB_PATH = Path("/data/orchestrator/orchestrator.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    inputs          TEXT NOT NULL,
    status          TEXT NOT NULL,
    current_step    TEXT,
    approval_policy TEXT NOT NULL DEFAULT 'auto',
    artifacts       TEXT,
    error_summary   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES jobs(id),
    name         TEXT NOT NULL,
    capability   TEXT,
    input        TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    started_at   TEXT,
    completed_at TEXT,
    result       TEXT,
    verification TEXT,
    retry_count  INTEGER DEFAULT 0,
    error        TEXT,
    created_at   TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    job_id     TEXT REFERENCES jobs(id),
    step_id    TEXT REFERENCES steps(id),
    type       TEXT NOT NULL,
    payload    TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_metrics (
    id                    TEXT PRIMARY KEY,
    job_id                TEXT NOT NULL REFERENCES jobs(id),
    step_name             TEXT NOT NULL,
    capability            TEXT,
    transport_used        TEXT,
    tokens_used           INTEGER DEFAULT 0,
    latency_ms            INTEGER,
    verified_first_try    INTEGER DEFAULT 0,
    retry_count           INTEGER DEFAULT 0,
    provider              TEXT,
    model                 TEXT,
    cost_usd              REAL DEFAULT 0.0,
    cache_read_tokens     INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gates (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES jobs(id),
    step_id      TEXT NOT NULL REFERENCES steps(id),
    gate_type    TEXT NOT NULL DEFAULT 'dev',
    brief        TEXT NOT NULL,
    callback_url TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    resolution   TEXT,
    opened_at    TEXT NOT NULL,
    resolved_at  TEXT
);

CREATE TABLE IF NOT EXISTS overrides (
    id         TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL REFERENCES jobs(id),
    step_id    TEXT NOT NULL REFERENCES steps(id),
    step_name  TEXT NOT NULL,
    reason     TEXT NOT NULL,
    operator   TEXT NOT NULL DEFAULT 'leo',
    -- notify_ack: 1 iff the override notification was acknowledged/delivered.
    -- Written from main.py as notify_ack=notify_ok, where notify_ok comes from the
    -- notify() gateway (Telegram/dashboard). Renamed from the legacy column name
    -- (AR-2/KAI-1252, 2026-08-27): last literal vestige identifier in the orchestrator
    -- schema. Existing rows migrated in init_db() via ALTER TABLE ... RENAME COLUMN.
    notify_ack INTEGER DEFAULT 0,
    bug_filed  TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_counters (
    capability TEXT NOT NULL,
    called_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_counters ON rate_counters(capability, called_at);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_build
    ON jobs(json_extract(inputs,'$.site'), type)
    WHERE status IN ('queued','running','blocked','needs_approval','failed_recoverable');

-- HOSTOPS-(d) (KAI-820, seq915): durable audit record for every executed host-op
-- mutation, read by the Layer-2 reconciler (hostops_audit.py). No FK to jobs —
-- a bypass/forged execution must still be recordable. L18: identity + intent +
-- gate_id + outcome only, never secret material.
CREATE TABLE IF NOT EXISTS hostops_audit (
    id         TEXT PRIMARY KEY,
    ts         TEXT NOT NULL,
    job_id     TEXT,
    step_id    TEXT,
    actor      TEXT,
    operation  TEXT NOT NULL,
    site       TEXT,
    gate_id    TEXT,
    outcome    TEXT NOT NULL
);

-- Memory Service Phase 1 (CONTEXT_SPEC.md §4/§5/§8/§13) — conversation store,
-- Tier 1 verbatim turns, Tier 2 rolling summary, assembly log.
CREATE TABLE IF NOT EXISTS conversations (
    id                     TEXT PRIMARY KEY,
    key_tuple              TEXT NOT NULL,
    advisor                TEXT NOT NULL,
    device                 TEXT NOT NULL,
    place                  TEXT,
    thread                 TEXT,
    turns_since_compaction INTEGER NOT NULL DEFAULT 0,
    summary                TEXT NOT NULL DEFAULT '',
    last_compaction_ts     TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_key ON conversations(key_tuple);

CREATE TABLE IF NOT EXISTS turns (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    package_id      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS assembly_log (
    package_id      TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    key_tuple       TEXT NOT NULL,
    tiers           TEXT NOT NULL,
    budget          TEXT
);
CREATE INDEX IF NOT EXISTS idx_assembly_log_conversation ON assembly_log(conversation_id, ts);
"""

def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(workflow_metrics)").fetchall()}
    for col, col_def in [
        ("provider",               "TEXT"),
        ("model",                  "TEXT"),
        ("cost_usd",               "REAL DEFAULT 0.0"),
        ("cache_read_tokens",      "INTEGER DEFAULT 0"),
        ("cache_creation_tokens",  "INTEGER DEFAULT 0"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE workflow_metrics ADD COLUMN {col} {col_def}")

    existing_al = {r[1] for r in conn.execute("PRAGMA table_info(assembly_log)").fetchall()}
    for col, col_def in [
        # CONTEXT_SPEC §7/§8 Phase 2 — cache shape per package.
        ("stable_prefix_hash",     "TEXT"),
        ("cache_breakpoint_after", "INTEGER"),
        ("cache_read_tokens",      "INTEGER DEFAULT 0"),
        ("cache_creation_tokens",  "INTEGER DEFAULT 0"),
        # CONTEXT_SPEC §8/§10 Phase 3 — promptware-defense scan record per package.
        ("threat_scan",            "TEXT"),
    ]:
        if col not in existing_al:
            conn.execute(f"ALTER TABLE assembly_log ADD COLUMN {col} {col_def}")

    # AR-2/KAI-1252: rename overrides.slack_ack -> notify_ack on existing DBs (idempotent).
    ov_cols = {r[1] for r in conn.execute("PRAGMA table_info(overrides)").fetchall()}
    if "slack_ack" in ov_cols and "notify_ack" not in ov_cols:
        conn.execute("ALTER TABLE overrides RENAME COLUMN slack_ack TO notify_ack")

    conn.commit()
    conn.close()

def new_id() -> str:
    return str(uuid.uuid4())

def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"
