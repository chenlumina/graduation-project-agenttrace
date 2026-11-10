import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.collector import TraceCollector
from agenttrace.mcptools import AuditedMCPClient
from agenttrace.model import AttemptMeta, new_id
from agenttrace.policy import Policy
from agenttrace.store import TraceStore

POLICY = Policy(
    name="test",
    deny_tools=["run_command"],
    read_deny_globs=["hidden_tests/*"],
    write_allow_globs=["src/*"],
)


class FakeUnderlying:
    """不带 MCP 依赖的底层客户端替身：验证审计层的决策与留痕逻辑。"""

    def __init__(self, tools):
        self._tools = [SimpleNamespace(name=n, description=d) for n, d in tools]
        self.calls = []

    def list_tools(self):
        return self._tools

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "write_file":
            return {"isError": False, "text": f"--- a/{args['path']}\n+++ b/{args['path']}\n@@\n+x\n"}
        return {"isError": False, "text": f"content-of-{args.get('path', name)}"}


class TestPolicyEngine(unittest.TestCase):
    def test_deny_tool(self):
        d = POLICY.evaluate("run_command", {"cmd": ["echo"]})
        self.assertFalse(d.allowed)
        self.assertEqual(d.rule_id, "deny_tool:run_command")

    def test_read_deny_glob(self):
        self.assertFalse(POLICY.evaluate("read_file", {"path": "hidden_tests/test_utils.py"}).allowed)
        self.assertTrue(POLICY.evaluate("read_file", {"path": "src/utils.py"}).allowed)

    def test_write_allowlist(self):
        self.assertTrue(POLICY.evaluate("write_file", {"path": "src/utils.py"}).allowed)
        self.assertFalse(POLICY.evaluate("write_file", {"path": "tests/evil.py"}).allowed)

    def test_arg_deny_pattern(self):
        p = Policy(arg_deny_patterns={"run_command": "rm.*-rf|del.*/"})
        self.assertFalse(p.evaluate("run_command", {"cmd": ["rm", "-rf", "/"]}).allowed)
        self.assertTrue(p.evaluate("run_command", {"cmd": ["echo", "hi"]}).allowed)

    def test_default_deny(self):
        p = Policy(default_action="deny")
        d = p.evaluate("read_file", {"path": "a"})
        self.assertFalse(d.allowed)
        self.assertEqual(d.rule_id, "default_deny")

    def test_from_dict(self):
        p = Policy.from_dict({"deny_tools": ["x"], "default": "deny"})
        self.assertEqual(p.default_action, "deny")
        self.assertEqual(p.deny_tools, ["x"])
        self.assertIsNone(Policy.from_dict(None))


class TestAuditedClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        self.attempt_id = new_id()
        self.store.begin_attempt(
            AttemptMeta(attempt_id=self.attempt_id, task_id="t", agent="a", model="m")
        )
        self.collector = TraceCollector(self.store, self.attempt_id)
        self.underlying = FakeUnderlying([("read_file", "读文件"), ("write_file", "写文件"), ("run_command", "执行命令")])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def audited(self, policy=None):
        a = AuditedMCPClient(self.underlying, policy or POLICY, self.collector)
        a.record_declarations()
        return a

    def test_declarations_recorded(self):
        self.audited()
        audits = [e for e in self.store.events(self.attempt_id) if e.type == "mcp_audit"]
        self.assertEqual([e.data["decision"] for e in audits], ["declared", "declared", "declared"])
        self.assertEqual([e.data["tool"] for e in audits], ["read_file", "write_file", "run_command"])

    def test_allow_path(self):
        a = self.audited()
        status, payload, _ = a.call("read_file", {"path": "src/utils.py"})
        self.assertEqual(status, "ok")
        self.assertIn("content-of", payload)
        # 授权决策统一落 policy_audit（与本地通道同一口径）
        allow = [
            e for e in self.store.events(self.attempt_id)
            if e.type == "policy_audit" and e.data["decision"] == "allow"
        ]
        self.assertEqual(len(allow), 1)
        self.assertEqual(allow[0].data["rule_id"], "default_allow")

    def test_deny_tool_never_reaches_server(self):
        a = self.audited()
        status, payload, _ = a.call("run_command", {"cmd": ["echo", "hi"]})
        self.assertEqual(status, "denied")
        self.assertIn("拒绝", payload)
        self.assertEqual(self.underlying.calls, [])  # 拒绝的调用不能到达 MCP 服务器
        evs = self.store.events(self.attempt_id)
        deny = [e for e in evs if e.type == "policy_audit" and e.data["decision"] == "deny"]
        self.assertEqual(len(deny), 1)
        self.assertEqual(deny[0].data["rule_id"], "deny_tool:run_command")
        tool_calls = [e for e in evs if e.type == "tool_call"]
        self.assertEqual(tool_calls[-1].data["status"], "denied")

    def test_read_deny_hidden_tests(self):
        a = self.audited()
        status, _, _ = a.call("read_file", {"path": "hidden_tests/test_utils.py"})
        self.assertEqual(status, "denied")

    def test_write_produces_diff_event(self):
        a = self.audited()
        status, _, _ = a.call("write_file", {"path": "src/utils.py", "content": "x"})
        self.assertEqual(status, "ok")
        diffs = [e for e in self.store.events(self.attempt_id) if e.type == "file_diff"]
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].data["path"], "src/utils.py")

    def test_undeclared_tool_denied(self):
        a = self.audited()
        status, _, _ = a.call("run_tests", {})  # 服务器未声明 run_tests
        self.assertEqual(status, "denied")
        deny = [
            e for e in self.store.events(self.attempt_id)
            if e.type == "mcp_audit" and e.data["decision"] == "deny"
        ]
        self.assertEqual(deny[-1].data["rule_id"], "undeclared")


if __name__ == "__main__":
    unittest.main()
