import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.codeaudit import audit_python_source
from agenttrace.llm import ScriptedLLM
from agenttrace.metrics import compute_metrics
from agenttrace.runner import TaskPackage, TaskRunner
from agenttrace.store import TraceStore

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "tasks" / "fix_leap_year"

CLEAN_CODE = "def add(a, b):\n    return a + b\n"

RISKY_CODE = (
    "import os\n"
    "import subprocess\n"
    "import pickle\n"
    "API_KEY = 'sk-abcdefghijklmnop'\n"
    "def run(user_input):\n"
    "    eval(user_input)\n"
    "    os.system(user_input)\n"
    "    subprocess.run(user_input, shell=True)\n"
    "    pickle.loads(b'x')\n"
    "    try:\n"
    "        __import__('os')\n"
    "    except:\n"
    "        pass\n"
)

BROKEN_CODE = "def broken(:\n    pass\n"

FIXED_UTILS = (
    "def is_leap_year(year: int) -> bool:\n"
    '    """闰年：四年一闰，百年不闰，四百年再闰。"""\n'
    "    if year % 400 == 0:\n"
    "        return True\n"
    "    if year % 100 == 0:\n"
    "        return False\n"
    "    return year % 4 == 0\n"
)


class TestAuditRules(unittest.TestCase):
    def test_clean_code(self):
        r = audit_python_source("src/utils.py", CLEAN_CODE)
        self.assertTrue(r["syntax_ok"])
        self.assertEqual(r["findings"], [])

    def test_risky_code_detected(self):
        r = audit_python_source("evil.py", RISKY_CODE)
        self.assertTrue(r["syntax_ok"])
        rules = {f["rule"] for f in r["findings"]}
        for expected in (
            "risk_call:eval",
            "risk_call:os.system",
            "risk_call:subprocess.shell",
            "risk_call:pickle.loads",
            "risk_call:__import__",
            "bare_except",
            "hardcoded_secret",
        ):
            self.assertIn(expected, rules)
        self.assertIn("high", {f["level"] for f in r["findings"]})

    def test_syntax_error(self):
        r = audit_python_source("broken.py", BROKEN_CODE)
        self.assertFalse(r["syntax_ok"])
        self.assertEqual(r["findings"][0]["rule"], "syntax_error")
        self.assertEqual(r["findings"][0]["level"], "high")

    def test_non_python_skipped(self):
        r = audit_python_source("README.md", "anything (")
        self.assertFalse(r["audited"])
        self.assertEqual(r["findings"], [])


class TestAuditInTrace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        self.task = TaskPackage.load(TASK_DIR)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_file_diff_carries_audit_and_metrics_count(self):
        actions = [
            # fix_leap_year 的策略限定可写路径为 src/*（本地通道同样受策略约束）
            {"tool": "write_file", "args": {"path": "src/evil.py", "content": RISKY_CODE}},
            {"tool": "write_file", "args": {"path": "src/utils.py", "content": FIXED_UTILS}},
            {"tool": "run_tests", "args": {}},
            {"tool": "finish", "args": {"summary": "done"}},
        ]
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(actions))
        self.assertTrue(res.passed)

        events = self.store.events(res.attempt_id)
        audits = {e.data["path"]: e.data.get("audit") for e in events if e.type == "file_diff"}
        self.assertIn("src/evil.py", audits)
        self.assertIn("src/utils.py", audits)
        self.assertTrue(audits["src/evil.py"]["syntax_ok"])
        self.assertTrue(
            any(f["rule"] == "risk_call:eval" for f in audits["src/evil.py"]["findings"])
        )
        self.assertEqual(audits["src/utils.py"]["findings"], [])

        m = compute_metrics(events)
        self.assertGreaterEqual(m["code_audit_high"], 1)
        self.assertGreaterEqual(m["code_audit_warn"], 1)


if __name__ == "__main__":
    unittest.main()
