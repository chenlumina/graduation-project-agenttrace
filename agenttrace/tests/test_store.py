import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.collector import TraceCollector
from agenttrace.model import AttemptMeta, TraceValidationError
from agenttrace.store import TraceStore


class TestTraceStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        meta = AttemptMeta(attempt_id="a1", task_id="t1", agent="mini-agent", model="scripted-mini")
        self.store.begin_attempt(meta)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_events_ordered_and_linked(self):
        c = TraceCollector(self.store, "a1")
        e1 = c.llm_call(model="m", messages=[], response="r", usage={"prompt_tokens": 1, "completion_tokens": 2})
        e2 = c.tool_call("finish", {}, "s", "ok", parent_id=e1.id)
        evs = self.store.events("a1")
        self.assertEqual([e.seq for e in evs], [1, 2])
        self.assertEqual(evs[1].parent_id, e1.id)

    def test_finish_and_get(self):
        c = TraceCollector(self.store, "a1")
        c.error("boom")
        self.store.finish_attempt("a1", "error")
        meta = self.store.get_attempt("a1")
        self.assertEqual(meta.status, "error")
        self.assertIsNotNone(meta.finished_at)

    def test_export_structure(self):
        c = TraceCollector(self.store, "a1")
        c.llm_call(model="m", messages=[], response="r", usage={"prompt_tokens": 1, "completion_tokens": 2})
        self.store.finish_attempt("a1", "passed")
        doc = self.store.export("a1")
        self.assertEqual(doc["schema_version"], 4)
        self.assertEqual(doc["attempt"]["status"], "passed")
        self.assertEqual(len(doc["events"]), 1)

    def test_export_missing_raises(self):
        with self.assertRaises(KeyError):
            self.store.export("nope")

    def test_finish_missing_raises(self):
        with self.assertRaises(KeyError):
            self.store.finish_attempt("nope", "passed")

    def test_invalid_event_rejected(self):
        c = TraceCollector(self.store, "a1")
        with self.assertRaises(TraceValidationError):
            c.emit("nope", {"message": "x"})

    def test_duplicate_seq_rejected(self):
        """R-07：数据库层唯一约束直接拒绝重复 seq（sqlite3.IntegrityError）。"""
        import json
        import sqlite3
        from agenttrace.model import new_id, now_iso
        c = TraceCollector(self.store, "a1")
        c.error("first")  # seq=1
        # 绕过 collector 的内存计数，直接构造重复 seq=1 的事件写入
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id(), "a1", 1, now_iso(), "error", None,
                 json.dumps({"message": "dup"}, ensure_ascii=False)),
            )
        self.store._conn.rollback()

    def test_duplicate_seq_rejected_via_append(self):
        """R-07：通过 append_event 写入重复 seq，抛 TraceValidationError 且不污染已有事件。"""
        from agenttrace.model import TraceEvent, new_id, now_iso
        c = TraceCollector(self.store, "a1")
        c.error("first")  # seq=1
        dup = TraceEvent(id=new_id(), attempt_id="a1", seq=1, ts=now_iso(),
                         type="error", parent_id=None, data={"message": "dup"})
        with self.assertRaises(TraceValidationError):
            self.store.append_event(dup)
        evs = self.store.events("a1")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].data["message"], "first")

    def test_legacy_index_migrated_to_unique(self):
        """R-07：旧库的普通索引在重新打开时自动升级为唯一索引。"""
        import sqlite3
        db_path = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE attempts (
                attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, agent TEXT NOT NULL,
                model TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, meta TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE events (
                id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, seq INTEGER NOT NULL,
                ts TEXT NOT NULL, type TEXT NOT NULL, parent_id TEXT, data TEXT NOT NULL
            );
            CREATE INDEX idx_events_attempt_seq ON events(attempt_id, seq);
            """
        )
        conn.commit()
        conn.close()
        store = TraceStore(db_path)  # 打开即触发迁移
        rows = store._conn.execute("PRAGMA index_list('events')").fetchall()
        idx = [r for r in rows if r[1] == "idx_events_attempt_seq"]
        self.assertTrue(idx and idx[0][2], "迁移后索引应为 UNIQUE")
        store.close()


if __name__ == "__main__":
    unittest.main()
