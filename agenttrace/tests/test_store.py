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
        self.assertEqual(doc["schema_version"], 3)
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


if __name__ == "__main__":
    unittest.main()
