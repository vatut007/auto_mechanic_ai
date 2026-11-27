"""SQLite manifest: documents + runs. Dedupe by sha256, statuses, run reports."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id        TEXT PRIMARY KEY,          -- sha256[:16]
    sha256        TEXT NOT NULL UNIQUE,
    original_name TEXT NOT NULL,
    relpath       TEXT,                      -- path under data/ once accepted
    brand         TEXT, model TEXT, generation TEXT, doc_type TEXT, section TEXT,
    title         TEXT, market TEXT DEFAULT 'ru',
    status        TEXT NOT NULL,             -- indexed | needs_review | failed
    reason        TEXT,
    pages         INTEGER, chunks_count INTEGER, tables_count INTEGER, images_count INTEGER,
    out_dir       TEXT,
    parser_version TEXT,
    first_seen    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL, finished TEXT,
    scanned INTEGER, new INTEGER, duplicates INTEGER,
    indexed INTEGER, needs_review INTEGER, failed INTEGER,
    notes TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Manifest:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def get_by_sha(self, sha: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM documents WHERE sha256 = ?", (sha,)).fetchone()

    def get(self, doc_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()

    def upsert_document(self, **kw) -> None:
        cols = set(kw)
        required = {"doc_id", "sha256", "original_name", "status"}
        missing = required - cols
        if missing:
            raise ValueError(f"missing required fields: {missing}")
        kw.setdefault("first_seen", _now())
        kw.setdefault("updated_at", _now())
        existing = self.get(kw["doc_id"])
        if existing:
            kw.pop("first_seen", None)
            sets = ", ".join(f"{k} = ?" for k in kw)
            self.conn.execute(
                f"UPDATE documents SET {sets} WHERE doc_id = ?",
                (*kw.values(), kw["doc_id"]),
            )
        else:
            columns = ", ".join(kw)
            marks = ", ".join("?" for _ in kw)
            self.conn.execute(f"INSERT INTO documents ({columns}) VALUES ({marks})", tuple(kw.values()))
        self.conn.commit()

    def start_run(self) -> int:
        cur = self.conn.execute("INSERT INTO runs (started) VALUES (?)", (_now(),))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, **counts) -> None:
        sets = ", ".join(f"{k} = ?" for k in counts)
        self.conn.execute(
            f"UPDATE runs SET finished = ?, {sets} WHERE run_id = ?",
            (_now(), *counts.values(), run_id),
        )
        self.conn.commit()

    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM documents GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def needs_review(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT doc_id, original_name, reason, first_seen FROM documents "
            "WHERE status = 'needs_review' ORDER BY first_seen"
        ).fetchall()

    def last_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
