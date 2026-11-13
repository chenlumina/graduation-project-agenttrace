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
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_attempt_seq ON events(attempt_id, seq);
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
        self._ensure_unique_seq_index()
        self._conn.commit()

    def _ensure_unique_seq_index(self) -> None:
        """旧库迁移（R-07）：早期版本的 idx_events_attempt_seq 是普通索引，
        CREATE UNIQUE INDEX IF NOT EXISTS 不会替换它。这里显式检测并重建，
        保证 (attempt_id, seq) 唯一约束对历史数据库同样生效。"""
        rows = self._conn.execute("PRAGMA index_list('events')").fetchall()
        for row in rows:
            # row: (seq, name, unique, origin, partial)
            if row[1] == "idx_events_attempt_seq":
                if row[2]:
                    return  # 已是唯一索引
                self._conn.execute("DROP INDEX idx_events_attempt_seq")
                break
        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_attempt_seq ON events(attempt_id, seq)"
            )
        except sqlite3.IntegrityError as e:
            raise TraceValidationError(
                f"历史库存在重复的 (attempt_id, seq)，无法建立唯一索引，请先清理数据: {e}"
            ) from e

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

    def list_attempts(
        self, limit: Optional[int] = None, offset: int = 0, task_id: Optional[str] = None,
    ) -> list[AttemptMeta]:
        """R-19：支持分页与按任务过滤（limit=None 时返回全部，兼容旧调用）。"""
        sql = f"SELECT {_ATTEMPT_COLS} FROM attempts"
        args: list = []
        if task_id:
            sql += " WHERE task_id = ?"
            args.append(task_id)
        sql += " ORDER BY started_at"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args += [int(limit), int(offset)]
        rows = self._conn.execute(sql, args).fetchall()
        return [_row_to_meta(r) for r in rows]

    def count_attempts(self, task_id: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM attempts"
        args: list = []
        if task_id:
            sql += " WHERE task_id = ?"
            args.append(task_id)
        return int(self._conn.execute(sql, args).fetchone()[0])

    def update_attempt_meta(self, attempt_id: str, patch: dict[str, Any]) -> None:
        """R-19：合并更新 attempt.meta（用于指标预计算等派生数据落库）。"""
        meta = self.get_attempt(attempt_id)
        if meta is None:
            raise KeyError(f"尝试不存在: {attempt_id}")
        merged = {**meta.meta, **patch}
        self._conn.execute(
            "UPDATE attempts SET meta = ? WHERE attempt_id = ?",
            (json.dumps(merged, ensure_ascii=False), attempt_id),
        )
        self._conn.commit()

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

    def count_events(self, attempt_id: str) -> int:
        return int(self._conn.execute(
            "SELECT COUNT(*) FROM events WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()[0])

    def export(self, attempt_id: str) -> dict[str, Any]:
        """导出为可回放的轨迹文档（schema_version + 尝试元信息 + 事件流）。

        派生字段（_metrics / _classification 等以 _ 开头的键）不随导出下发。
        """
        meta = self.get_attempt(attempt_id)
        if meta is None:
            raise KeyError(f"尝试不存在: {attempt_id}")
        attempt = meta.to_dict()
        attempt["meta"] = {k: v for k, v in meta.meta.items() if not k.startswith("_")}
        return {
            "schema_version": SCHEMA_VERSION,
            "attempt": attempt,
            "events": [ev.to_dict() for ev in self.events(attempt_id)],
        }

    def close(self) -> None:
        self._conn.close()
