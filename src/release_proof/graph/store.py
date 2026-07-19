from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from release_proof.domain.models import AnalysisRun


class RunNotFoundError(KeyError):
    pass


class SqliteRunStore:
    """Business run store; intentionally separate from LangGraph checkpoints."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    run_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    run_json TEXT NOT NULL,
                    state_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_analysis_runs_status ON analysis_runs(status)"
            )

    def save(self, run: AnalysisRun, state: dict[str, Any] | None = None) -> None:
        payload = run.model_dump_json()
        state_json = json.dumps(state, ensure_ascii=False, default=str) if state is not None else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_runs (
                    run_id, thread_id, status, run_json, state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    run_json=excluded.run_json,
                    state_json=COALESCE(excluded.state_json, analysis_runs.state_json),
                    updated_at=excluded.updated_at
                """,
                (
                    run.run_id,
                    run.thread_id,
                    run.status.value,
                    payload,
                    state_json,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )

    def get(self, run_id: str) -> AnalysisRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_json FROM analysis_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return AnalysisRun.model_validate_json(row["run_json"])

    def get_state(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM analysis_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return json.loads(row["state_json"]) if row["state_json"] else None

    def list(self, limit: int = 50) -> list[AnalysisRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_json FROM analysis_runs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [AnalysisRun.model_validate_json(row["run_json"]) for row in rows]

    def health(self) -> dict[str, str | int]:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM analysis_runs").fetchone()
        return {
            "status": "ok",
            "runs": int(row["count"] if row else 0),
            "database": self.database_path.name,
        }


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)

