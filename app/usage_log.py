"""Token / cost per-request log in SQLite (implementation plan, Stage 1).

Cost computation requires per-model prices in config/providers.yaml (`prices`);
without them tokens are logged and cost stays NULL.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,          -- UTC ISO-8601
    alias             TEXT,                   -- public model alias (Open WebUI selection)
    provider          TEXT,
    model             TEXT,                   -- real provider model id
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    cost_usd          REAL,
    stream            INTEGER,
    duration_ms       INTEGER,
    status            TEXT,                   -- ok | error
    error             TEXT
);
"""


class UsageLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def log(
        self,
        *,
        alias: str,
        provider: str,
        model: str,
        usage: dict | None,
        stream: bool,
        duration_ms: int,
        status: str = "ok",
        error: str | None = None,
        cost_usd: float | None = None,
    ) -> None:
        usage = usage or {}
        self.conn.execute(
            "INSERT INTO chat_usage (ts, alias, provider, model, prompt_tokens, "
            "completion_tokens, total_tokens, cost_usd, stream, duration_ms, status, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                alias,
                provider,
                model,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
                cost_usd,
                int(stream),
                duration_ms,
                status,
                error,
            ),
        )
        self.conn.commit()

    def cost(self, model: str, usage: dict | None) -> float | None:
        """Compute cost when per-1M prices are configured for the model."""
        if not usage:
            return None
        return None  # wired when per-model prices land in providers.yaml
