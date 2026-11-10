import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

try:
    import mcp  # noqa: F401

    from agenttrace.mcptools import AuditedMCPClient, MCPToolClient, SERVER_SCRIPT

    HAS_MCP = Path(SERVER_SCRIPT).exists()
except ImportError:
    HAS_MCP = False

from agenttrace.collector import TraceCollector
from agenttrace.model import AttemptMeta, new_id
from agenttrace.policy import Policy
from agenttrace.store import TraceStore

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "tasks" / "fix_leap_year"

FIXED_UTILS = (
    "def is_leap_year(year: int) -> bool:\n"
    '    """闰年：四年一闰，百年不闰，四百年再闰。"""\n'
    "    if year % 400 == 0:\n"
    "        return True\n"
    "    if year % 100 == 0:\n"
    "        return False\n"
    "    return year % 4 == 0\n"
)


@unittest.skipUnless(HAS_MCP, "需要 mcp SDK（pip install mcp）")
class TestMCPEndToEnd(unittest.TestCase):
    """真实 MCP 链路：stdio 子进程服务器 + 客户端会话 + 策略审计。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        self.ws = Path(self.tmp.name) / "ws"
        shutil.copytree(TASK_DIR / "setup", self.ws, dirs_exist_ok=True)
        shutil.copytree(TASK_DIR / "hidden_tests", self.ws / "hidden_tests", dirs_exist_ok=True)
        self.attempt_id = new_id()
        self.store.begin_attempt(
            AttemptMeta(attempt_id=self.attempt_id, task_id="mcp_e2e", agent="a", model="m")
        )
        self.collector = TraceCollector(self.store, self.attempt_id)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_real_mcp_roundtrip_with_audit(self):
        policy = Policy(
            deny_tools=["run_command"],
            read_deny_globs=["hidden_tests/*"],
            write_allow_globs=["src/*"],
        )
        client = MCPToolClient([sys.executable, str(SERVER_SCRIPT)], workspace=self.ws).start()
        try:
            audited = AuditedMCPClient(client, policy, self.collector)
            audited.record_declarations()
            self.assertGreaterEqual(len(audited.declarations), 3)

            # 1) 允许：读文件（经真实 MCP 协议）
            status, payload, _ = audited.call("read_file", {"path": "src/utils.py"})
            self.assertEqual(status, "ok")
            self.assertIn("is_leap_year", payload)

            # 2) 拒绝：禁读隐藏测试
            status, payload, _ = audited.call("read_file", {"path": "hidden_tests/test_utils.py"})
            self.assertEqual(status, "denied")
            self.assertIn("禁读", payload)

            # 3) 拒绝：黑名单工具
            status, _, _ = audited.call("run_command", {"cmd": ["echo", "hi"]})
            self.assertEqual(status, "denied")

            # 4) 允许：写文件，MCP 服务器返回 unified diff
            status, _, full = audited.call("write_file", {"path": "src/utils.py", "content": FIXED_UTILS})
            self.assertEqual(status, "ok")
            self.assertTrue(full.startswith("---"))

            # 5) 允许：经 MCP 运行隐藏测试，修复后应通过
            status, out, _ = audited.call("run_tests", {})
            self.assertEqual(status, "ok")
            self.assertIn("OK", out)

            events = self.store.events(self.attempt_id)
            # MCP 协议层声明事件 + 未声明工具的协议层拦截
            audits = [e.data["decision"] for e in events if e.type == "mcp_audit"]
            self.assertEqual(audits.count("declared"), len(audited.declarations))
            self.assertEqual(audits.count("deny"), 1)  # run_command 未被 MCP 服务器声明
            # 授权决策统一落 policy_audit（与本地通道同一口径）
            decisions = [e.data["decision"] for e in events if e.type == "policy_audit"]
            self.assertEqual(decisions.count("allow"), 3)
            self.assertEqual(decisions.count("deny"), 1)  # 禁读隐藏测试
            diffs = [e for e in events if e.type == "file_diff"]
            self.assertEqual(len(diffs), 1)
            self.assertEqual(diffs[0].data["path"], "src/utils.py")
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
