"""MCP 接入层：stdio 客户端桥 + 权限审计包装。

- MCPToolClient    ：在后台线程的事件循环里维持 MCP 会话（stdio 传输），
                     对上层暴露同步的 list_tools / call；
- AuditedMCPClient ：每次 tools/call 前先做"声明比对 + 策略评估"，
                     授权决策以 mcp_audit 事件留痕；拒绝的调用不会到达
                     MCP 服务器；允许的调用落 tool_call 事件
                     （write_file 成功时附带 file_diff）。

留痕链路：llm_call → mcp_audit（声明比对）/ policy_audit（授权决策）→ tool_call（执行结果）→ file_diff（如适用）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from .agent import detect_injection
from .codeaudit import audit_python_source
from .collector import TraceCollector
from .envutil import sanitized_env
from .policy import Policy

SERVER_SCRIPT = Path(__file__).resolve().parent / "mcp_demo_server.py"


class MCPToolClient:
    """MCP stdio 客户端：会话在后台线程中常驻，同步接口桥接。"""

    def __init__(self, server_command: list[str], workspace: Path, hidden_dir: Path | None = None,
                 test_cmd: list[str] | None = None, sources_file: Path | None = None):
        self._server_command = server_command
        self._workspace = Path(workspace)
        # 隐藏测试目录（可选）：缺省时回落到工作区内的 hidden_tests（兼容旧用法）
        self._hidden_dir = Path(hidden_dir) if hidden_dir else (self._workspace / "hidden_tests")
        # 任务自带的测试命令（缺省时服务端用 unittest discover 默认值）
        self._test_cmd = list(test_cmd) if test_cmd else []
        # 隐藏测试源码的临时载体：服务端启动时读取并删除（磁盘上不留测试文件）
        self._sources_file = Path(sources_file) if sources_file else None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._session: Any = None
        self._tools: list[Any] = []
        self._error: BaseException | None = None

    # ---- 生命周期 ----

    def start(self) -> "MCPToolClient":
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mcp-client")
        self._thread.start()
        if not self._ready.wait(timeout=30):
            err = self._error
            self.close()
            raise RuntimeError(f"MCP 会话启动超时: {err}")
        if self._error is not None:
            self.close()
            raise RuntimeError(f"MCP 会话启动失败: {self._error}")
        return self

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except BaseException as e:  # 启动期异常需要传回主线程
            self._error = e
            self._ready.set()
        finally:
            self._closed.set()

    async def _main(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        import mcp as _mcp_pkg

        env = sanitized_env(
            {
                "AT_WORKSPACE": str(self._workspace),
                "AT_HIDDEN_TESTS": str(self._hidden_dir),
                "AT_TEST_CMD": json.dumps(self._test_cmd),
                "AT_HIDDEN_SOURCES_FILE": str(self._sources_file) if self._sources_file else "",
            }
        )

        # sanitized env 下子进程的包定位不可靠（本机的包分散在多个
        # site-packages，靠父进程的 .pth/环境拼齐，清洗后必丢）：把父进程
        # 完整 sys.path 经 JSON 传给 bootstrap 注入，再启动服务器脚本。
        # 只传目录路径，不传任何凭据（H-1 约束保持）。
        parent_paths = [p for p in sys.path if p]
        bootstrap = (
            "import sys, json, runpy\n"
            "for p in reversed(json.loads(sys.argv[1])):\n"
            "    if p not in sys.path:\n"
            "        sys.path.insert(0, p)\n"
            "target = sys.argv[2]\n"
            "sys.argv = [target] + sys.argv[3:]\n"
            "runpy.run_path(target, run_name='__main__')\n"
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", bootstrap, json.dumps(parent_paths), *self._server_command[1:]],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                self._tools = list(listing.tools)
                self._session = session
                self._ready.set()
                await asyncio.to_thread(self._closed.wait)  # 会话保持到 close()

    # ---- 同步接口 ----

    def list_tools(self) -> list[Any]:
        return self._tools

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._session is None or self._loop is None:
            raise RuntimeError("MCP 会话未就绪")
        fut = asyncio.run_coroutine_threadsafe(self._call(name, args), self._loop)
        return fut.result(timeout=180)

    async def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        res = await self._session.call_tool(name, args)
        text = "\n".join(getattr(c, "text", "") for c in res.content)
        return {"isError": bool(res.isError), "text": text}

    def close(self) -> None:
        self._closed.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        if self._loop is not None:
            self._loop.close()
            self._loop = None


class AuditedMCPClient:
    """在 MCP 调用外层做声明比对、策略评估与全程留痕。"""

    def __init__(self, underlying: MCPToolClient, policy: Policy, collector: TraceCollector):
        self.underlying = underlying
        self.policy = policy
        self.collector = collector
        self.declarations: list[dict[str, str]] = []

    def record_declarations(self) -> None:
        """把 MCP list_tools 的声明逐条留痕（decision=declared）。"""
        for t in self.underlying.list_tools():
            desc = (getattr(t, "description", "") or "").strip()
            self.declarations.append({"name": t.name, "description": desc})
            self.collector.emit(
                "mcp_audit",
                {
                    "decision": "declared",
                    "tool": t.name,
                    "rule_id": "declaration",
                    "reason": desc[:200] or "MCP 工具声明",
                },
            )

    def call(
        self, tool: str, args: dict[str, Any], parent_id: str | None = None
    ) -> tuple[str, str, Any]:
        """返回 (status, payload, result)；status ∈ ok / error / denied。"""
        # 1) 声明比对：未声明的工具一律拒绝
        if not any(d["name"] == tool for d in self.declarations):
            self.collector.emit(
                "mcp_audit",
                {"decision": "deny", "tool": tool, "rule_id": "undeclared",
                 "reason": "调用了 MCP 服务器未声明的工具"},
                parent_id,
            )
            self.collector.tool_call(tool, args, "工具未声明，已被审计层拦截", "denied", parent_id)
            return "denied", "工具未在 MCP 服务器声明，已被权限策略拦截", "denied"

        # 2) 策略评估（授权决策统一落 policy_audit，与本地通道同一口径）
        decision = self.policy.evaluate(tool, args)
        self.collector.policy_audit(
            "allow" if decision.allowed else "deny",
            tool, decision.rule_id, decision.reason,
            parent_id,
        )
        if not decision.allowed:
            self.collector.tool_call(
                tool, args, f"被策略拒绝: {decision.reason}", "denied", parent_id
            )
            return "denied", f"被权限策略拒绝（{decision.reason}），请调整方案后重试", "denied"

        # 3) 允许 → 真实 MCP 调用
        try:
            res = self.underlying.call(tool, args)
        except Exception as e:
            self.collector.tool_call(tool, args, f"MCP 调用异常: {e}", "error", parent_id)
            return "error", f"MCP 调用异常: {e}", "error"

        text = res["text"]
        status = "error" if res["isError"] else "ok"
        if tool == "write_file" and status == "ok" and text.startswith("---"):
            path = str(args.get("path", ""))
            audit = (
                audit_python_source(path, str(args.get("content", "")))
                if path.endswith(".py")
                else None
            )
            self.collector.file_diff(path, text, parent_id, audit=audit)
        # 注入特征检测（H-3）：MCP 通道返回的文件内容同样可能夹带提示注入
        extra = None
        if status == "ok" and tool in ("read_file", "run_tests"):
            flags = detect_injection(text)
            if flags:
                extra = {"injection_flags": flags}
        self.collector.tool_call(tool, args, text[:2000], status, parent_id, extra=extra)
        return status, text[:2000], text
