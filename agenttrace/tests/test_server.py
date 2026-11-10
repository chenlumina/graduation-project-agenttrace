import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

try:
    import uvicorn  # noqa: F401

    from agenttrace.server import create_app

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

from agenttrace.llm import ScriptedLLM
from agenttrace.runner import TaskPackage, TaskRunner
from agenttrace.store import TraceStore

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "tasks" / "fix_leap_year"


@unittest.skipUnless(HAS_DEPS, "需要 fastapi / uvicorn（pip install fastapi uvicorn）")
class TestServerAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1) 准备数据库：真实跑一次示例任务
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "traces.db"
        store = TraceStore(db)
        task = TaskPackage.load(TASK_DIR)
        res = TaskRunner(store).run(task, ScriptedLLM(task.scripted_actions))
        store.close()
        assert res.passed
        cls.attempt_id = res.attempt_id

        # 2) 在线程内启动 uvicorn（随机空闲端口）
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()

        config = uvicorn.Config(create_app(db), host="127.0.0.1", port=cls.port, log_level="error")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 15
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.05)
        if not cls.server.started:
            raise RuntimeError("uvicorn 启动超时")
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.tmp.cleanup()

    def _get_json(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_attempts_list(self):
        status, body = self._get_json("/api/attempts")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["attempts"]), 1)
        self.assertEqual(body["attempts"][0]["status"], "passed")
        self.assertEqual(body["attempts"][0]["attempt_id"], self.attempt_id)

    def test_attempt_detail(self):
        status, body = self._get_json(f"/api/attempts/{self.attempt_id}")
        self.assertEqual(status, 200)
        self.assertEqual(body["schema_version"], 3)
        self.assertEqual(body["attempt"]["task_id"], "fix_leap_year")
        self.assertGreaterEqual(len(body["events"]), 10)
        types = {e["type"] for e in body["events"]}
        self.assertTrue({"llm_call", "tool_call", "file_diff", "command_exec", "run_result"} <= types)

    def test_attempt_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/api/attempts/nope", timeout=10)
        self.assertEqual(cm.exception.code, 404)

    def test_metrics_endpoint(self):
        status, body = self._get_json(f"/api/attempts/{self.attempt_id}/metrics")
        self.assertEqual(status, 200)
        self.assertTrue(body["metrics"]["passed"])
        self.assertEqual(body["metrics"]["tool_call_count"], 4)
        self.assertEqual(body["classification"]["primary"], "passed")

    def test_metrics_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/api/attempts/nope/metrics", timeout=10)
        self.assertEqual(cm.exception.code, 404)

    def test_index_page(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            html = r.read().decode("utf-8")
        self.assertIn("AgentTrace", html)
        self.assertIn("/static/app.js", html)

    def test_static_assets(self):
        for path in ("/static/app.js", "/static/style.css"):
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
