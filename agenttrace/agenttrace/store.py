"""SQLite 轨迹存储（开发期存储；生产可替换为 PostgreSQL 实现）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .model import SCHEMA_VERSION, AttemptMeta, TraceEvent, TraceValidationError, now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id  TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    agent       TEXT NOT NULL,
    model       TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    ts         TEXT NOT NULL,
    type       TEXT NOT NULL,
    parent_id  TEXT,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_attempt_seq ON events(attempt_id, seq);
"""

_ATTEMPT_COLS = "attempt_id, task_id, agent, model, status, started_at, finished_at, meta"


def _row_to_meta(row: tuple) -> AttemptMeta:
    return AttemptMeta(
        attempt_id=row[0],
        task_id=row[1],
        agent=row[2],
        model=row[3],
        status=row[4],
        started_at=row[5],
        finished_at=row[6],
        meta=json.loads(row[7]),
    )


class TraceStore:
    """轨迹存储：尝试元信息 + 有序事件流，支持整段导出为可回放 JSON 文档。"""

    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def begin_attempt(self, meta: AttemptMeta) -> str:
        self._conn.execute(
            "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meta.attempt_id,
                meta.task_id,
                meta.agent,
                meta.model,
                meta.status,
                meta.started_at,
                meta.finished_at,
                json.dumps(meta.meta, ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return meta.attempt_id

    def append_event(self, ev: TraceEvent) -> None:
        ev.validate()
        try:
            self._conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ev.id,
                    ev.attempt_id,
                    ev.seq,
                    ev.ts,
                    ev.type,
                    ev.parent_id,
                    json.dumps(ev.data, ensure_ascii=False),
                ),
            )
        except sqlite3.IntegrityError as e:
            raise TraceValidationError(f"事件写入失败（id 或 (attempt_id, seq) 重复）: {e}") from e
        self._conn.commit()

    def finish_attempt(self, attempt_id: str, status: str, finished_at: Optional[str] = None) -> None:
        cur = self._conn.execute(
            "UPDATE attempts SET status = ?, finished_at = ? WHERE attempt_id = ?",
            (status, finished_at or now_iso(), attempt_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"尝试不存在: {attempt_id}")

    def get_attempt(self, attempt_id: str) -> Optional[AttemptMeta]:
        row = self._conn.execute(
            f"SELECT {_ATTEMPT_COLS} FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        return _row_to_meta(row) if row is not None else None

    def list_attempts(self) -> list[AttemptMeta]:
        rows = self._conn.execute(
            f"SELECT {_ATTEMPT_COLS} FROM attempts ORDER BY started_at"
        ).fetchall()
        return [_row_to_meta(r) for r in rows]

    def events(self, attempt_id: str) -> list[TraceEvent]:
        rows = self._conn.execute(
            "SELECT id, attempt_id, seq, ts, type, parent_id, data "
            "FROM events WHERE attempt_id = ? ORDER BY seq",
            (attempt_id,),
        ).fetchall()
        return [
            TraceEvent(
                id=r[0],
                attempt_id=r[1],
                seq=r[2],
                ts=r[3],
                type=r[4],
                parent_id=r[5],
                data=json.loads(r[6]),
            )
            for r in rows
        ]

    def export(self, attempt_id: str) -> dict[str, Any]:
        """导出为可回放的轨迹文档（schema_version + 尝试元信息 + 事件流）。"""
        meta = self.get_attempt(attempt_id)
        if meta is None:
            raise KeyError(f"尝试不存在: {attempt_id}")
        return {
            "schema_version": SCHEMA_VERSION,
            "attempt": meta.to_dict(),
            "events": [ev.to_dict() for ev in self.events(attempt_id)],
        }

    def close(self) -> None:
        self._conn.close()
