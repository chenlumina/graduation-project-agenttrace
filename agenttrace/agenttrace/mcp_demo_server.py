"""独立 MCP 工具服务器（stdio 传输）——把受限工作区工具以 MCP 协议暴露。

仅依赖 mcp SDK 与标准库；工作区根目录通过环境变量 AT_WORKSPACE 传入，
隐藏测试目录通过 AT_HIDDEN_TESTS 传入（缺省时回落到工作区内 hidden_tests，
兼容旧的独立启动方式），因此可被任意 Python 以绝对脚本路径启动
（不受安全路径模式影响）。

工具即"MCP 声明"：客户端 list_tools 得到的就是本文件中注册的工具清单，
平台据此做"声明—调用"比对，未声明的调用会被审计层拦截。

隐藏测试隔离（C-3 修复）：run_tests 不在工作区内直接执行，而是把工作区
产物复制到临时目录并叠加**原始隐藏测试**后执行；Agent 既读不到测试内容，
也改不了测试。
"""
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 让本脚本既能独立启动，又能复用平台内部的判定装配逻辑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenttrace.verdict import LocalTestRunner  # noqa: E402

WS = Path(os.environ["AT_WORKSPACE"]).resolve()
HIDDEN = Path(os.environ.get("AT_HIDDEN_TESTS") or (WS / "hidden_tests")).resolve()
try:
    TEST_CMD = json.loads(os.environ.get("AT_TEST_CMD") or "[]")
except json.JSONDecodeError:
    TEST_CMD = []
if not TEST_CMD:
    TEST_CMD = [
        sys.executable, "-m", "unittest", "discover",
        "-s", "hidden_tests", "-p", "test_*.py", "-t", ".",
    ]

# 隐藏测试源码：优先从临时载体读取（读取后立即删除，磁盘上不留测试文件）
SOURCES: list[dict[str, str]] = []
_src_file = os.environ.get("AT_HIDDEN_SOURCES_FILE")
if _src_file:
    try:
        SOURCES = json.loads(Path(_src_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        SOURCES = []
    try:
        Path(_src_file).unlink()
    except OSError:
        pass

mcp = FastMCP("workspace-tools")


def _resolve(rel: str) -> Path:
    p = (WS / rel).resolve()
    if not p.is_relative_to(WS):
        raise ValueError(f"路径越界: {rel!r}")
    return p


@mcp.tool()
def read_file(path: str) -> str:
    """读取工作区内指定路径的文件内容。"""
    return _resolve(path).read_text(encoding="utf-8")


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """写入工作区内指定路径的文件，返回本次改动的 unified diff。"""
    p = _resolve(path)
    before = p.read_text(encoding="utf-8") if p.exists() else ""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


@mcp.tool()
def run_tests() -> str:
    """在隔离副本中运行隐藏测试（原始测试内容），返回退出码与输出。

    与最终判定走同一套加固执行器：隐藏测试只在内存中加载，被测代码读不到、
    改不了测试框架；结果 fail-closed 判定。
    """
    runner = LocalTestRunner(WS, HIDDEN, TEST_CMD, sources=SOURCES or None)
    res = runner.run(timeout=120)
    return (
        f"exit={res['exit_code']}\n"
        f"verdict={'passed' if res.get('verdict_ok') else 'failed'}"
        f"（{res.get('verdict_reason', '')}）\n"
        f"{res['stdout_tail']}\n{res['stderr_tail']}"
    )


if __name__ == "__main__":
    mcp.run()
