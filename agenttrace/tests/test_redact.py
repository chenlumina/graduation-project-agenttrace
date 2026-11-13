import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.collector import TraceCollector
from agenttrace.model import AttemptMeta
from agenttrace.redact import redact_obj, redact_text
from agenttrace.store import TraceStore


class TestRedact(unittest.TestCase):
    def test_redact_patterns(self):
        """R-22：密钥/令牌/邮箱/私钥块遮蔽，普通文本不受影响。"""
        cases = [
            ("key=sk-abcdefghijklmnopqrstuvwxyz123456", "sk-***REDACTED***"),
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6", "Bearer ***REDACTED***"),
            ('{"api_key": "supersecretvalue123"}', "***REDACTED***"),
            ("联系 zhang@example.com 处理", "***@***"),
            ("AKIAABCDEFGHIJKLMNOP", "AKIA***REDACTED***"),
        ]
        for raw, expect in cases:
            out = redact_text(raw)
            self.assertIn(expect, out)
        self.assertEqual(redact_text("正常代码 diff + line"), "正常代码 diff + line")
        self.assertIsNone(redact_obj(None))
        self.assertEqual(redact_obj({"a": ["sk-abcdefghijklmnopqrst"]})["a"][0], "sk-***REDACTED***")

    def test_export_redact_option(self):
        """R-22：export(redact=True) 遮蔽事件中的敏感信息；默认保真。"""
        with tempfile.TemporaryDirectory() as td:
            store = TraceStore(Path(td) / "t.db")
            store.begin_attempt(AttemptMeta(attempt_id="a1", task_id="t", agent="a", model="m"))
            c = TraceCollector(store, "a1")
            c.tool_call("run_command", {"cmd": ["curl", "-H", "Authorization: Bearer tok_abcdefghijklmnop123456"]},
                        "输出含 sk-abcdefghijklmnopqrstuvwx", "ok")
            raw = store.export("a1")
            self.assertIn("sk-abcdefghijklmnopqrstuvwx", str(raw))
            masked = store.export("a1", redact=True)
            self.assertNotIn("sk-abcdefghijklmnopqrstuvwx", str(masked))
            self.assertIn("REDACTED", str(masked))
            store.close()

    def test_collector_redact_env_hook(self):
        """R-22：AGENTTRACE_REDACT=1 时采集侧落库前脱敏。"""
        with tempfile.TemporaryDirectory() as td:
            store = TraceStore(Path(td) / "t.db")
            store.begin_attempt(AttemptMeta(attempt_id="a2", task_id="t", agent="a", model="m"))
            os.environ["AGENTTRACE_REDACT"] = "1"
            try:
                c = TraceCollector(store, "a2")
                c.error("leaked sk-abcdefghijklmnopqrstuvwxyz")
            finally:
                os.environ.pop("AGENTTRACE_REDACT", None)
            ev = store.events("a2")[0]
            self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", ev.data["message"])
            store.close()

    def test_purge_by_retention(self):
        """R-22：按保留期清理历史尝试与事件。"""
        with tempfile.TemporaryDirectory() as td:
            store = TraceStore(Path(td) / "t.db")
            store.begin_attempt(AttemptMeta(attempt_id="old", task_id="t", agent="a", model="m",
                                            started_at="2020-01-01T00:00:00.000+00:00"))
            TraceCollector(store, "old").error("x")
            store.begin_attempt(AttemptMeta(attempt_id="new", task_id="t", agent="a", model="m"))
            TraceCollector(store, "new").error("y")
            removed = store.delete_attempts_before("2021-01-01T00:00:00.000+00:00")
            self.assertEqual(removed, 1)
            self.assertEqual(store.count_attempts(), 1)
            self.assertEqual(store.count_events("old"), 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
