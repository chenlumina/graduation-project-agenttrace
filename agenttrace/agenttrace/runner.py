"""评测执行器：装配任务工作区、运行被测 Agent、用隐藏测试给出权威判定。

三种执行模式（工作区与隐藏测试始终物理分离，C-3 修复）：
- 本地（默认）：临时目录工作区，命令在宿主机执行；宿主执行属高危操作，
  默认**禁用 run_command**（需显式 allow_local_commands=True）；
- Docker（use_docker）：Agent 目录挂载进沙箱容器，命令经 docker exec 执行
  （资源限额 + 断网 + 权限收敛），隐藏测试不进 Agent 容器；
- MCP（use_mcp）：工具经真实 MCP 协议执行，策略评估 + 授权决策全程留痕。

隐藏测试目录始终位于 Agent 工作区之外；无论 Agent 何时运行测试、也无论
它如何篡改自己的工作区，判定用的都是"Agent 产物副本 + 原始隐藏测试"。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .agent import MiniAgent
from .collector import TraceCollector
from .model import AttemptMeta, new_id
from .policy import Policy
from .store import TraceStore
from .tools import Workspace
from .verdict import DockerTestRunner, LocalTestRunner

#: 平台硬规则：任何策略下都不可读写的目录（防窥探/防篡改隐藏测试）
PROTECTED_GLOBS = ["hidden_tests/*", "hidden_tests/**"]


@dataclass
class TaskPackage:
    """任务包 = 仓库快照（setup/）+ 自然语言指令 + 隐藏测试（hidden_tests/）。"""

    root: Path
    id: str
    instruction: str
    test_cmd: list[str]
    scripted_actions: list
    policy: Policy | None = None

    @classmethod
    def load(cls, path: str | Path) -> "TaskPackage":
        root = Path(path)
        cfg_path = root / "task.json"
        if not cfg_path.exists():
            raise FileNotFoundError(f"任务目录缺少 task.json: {root}")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        return cls(
            root=root,
            id=cfg["id"],
            instruction=cfg["instruction"],
            test_cmd=list(cfg["test_cmd"]),
            scripted_actions=cfg.get("scripted_actions") or [],
            policy=Policy.from_dict(cfg.get("policy")),
        )

    def resolved_test_cmd(self) -> list[str]:
        """把 {python} 占位符替换为当前解释器，保证本地可执行。"""
        return [sys.executable if c == "{python}" else str(c) for c in self.test_cmd]


@dataclass
class AttemptResult:
    attempt_id: str
    status: str
    passed: bool


def build_effective_policy(
    task_policy: Policy | None,
    *,
    allow_local_commands: bool = False,
    use_docker: bool = False,
) -> Policy:
    """装配本次尝试真正生效的策略。

    - 任务包未声明策略 → 使用内置安全策略（Policy.safe_default）；
    - 平台硬规则叠加：隐藏测试目录永不可读写；
    - 本地（宿主机）模式默认禁用 run_command（C-1）：宿主执行等价于执行
      不可信代码，只有显式开启 allow_local_commands 才放行；Docker 模式
      内命令受容器隔离，可放行。
    """
    base = task_policy or Policy.safe_default()
    hard_deny = []
    if not use_docker and not allow_local_commands:
        hard_deny.append("run_command")
    return base.with_extra(deny_tools=hard_deny, read_deny_globs=PROTECTED_GLOBS)


class TaskRunner:
    def __init__(self, store: TraceStore):
        self.store = store

    def run(
        self,
        task: TaskPackage,
        llm,
        agent: MiniAgent | None = None,
        max_turns: int = 8,
        use_mcp: bool = False,
        use_docker: bool = False,
        meta_extra: dict | None = None,
        allow_local_commands: bool = False,
    ) -> AttemptResult:
        if use_mcp and use_docker:
            raise ValueError("--mcp 与 --docker 暂不支持同时使用：MCP 工具服务器运行在宿主侧")
        attempt_id = new_id()
        policy = build_effective_policy(
            task.policy, allow_local_commands=allow_local_commands, use_docker=use_docker
        )
        meta = AttemptMeta(
            attempt_id=attempt_id,
            task_id=task.id,
            agent=(agent.name if agent is not None else MiniAgent.name),
            model=getattr(llm, "model", "unknown"),
            meta={
                "sandbox": "docker" if use_docker else "local",
                "policy": policy.name,
                "hidden_tests_isolated": True,
            },
        )
        if meta_extra:
            meta.meta.update(meta_extra)
        self.store.begin_attempt(meta)
        collector = TraceCollector(self.store, attempt_id)
        cleanup_dir: Path | None = None
        sandbox = None
        mcp_client = None

        passed = False
        status = "error"
        agent_error = False
        try:
            # 工作区与隐藏测试分离：隐藏测试放在**独立的临时根**里，
            # 与工作区没有父子关系，Agent 的命令/工具都够不着（评审发现 #4）
            cleanup_dir = Path(tempfile.mkdtemp(prefix="atp-ws-"))
            hidden_root = Path(tempfile.mkdtemp(prefix="atp-hid-"))
            cleanup_dirs = [cleanup_dir, hidden_root]
            agent_dir = cleanup_dir / "ws"
            hidden_dir = hidden_root / "hidden"
            agent_dir.mkdir()
            hidden_dir.mkdir()
            shutil.copytree(task.root / "setup", agent_dir, dirs_exist_ok=True)
            shutil.copytree(task.root / "hidden_tests", hidden_dir, dirs_exist_ok=True)

            if use_docker:
                from .sandbox import DockerSandbox, DockerWorkspace

                sandbox = DockerSandbox(agent_dir, hidden_dir=hidden_dir).start()
                workspace = DockerWorkspace(agent_dir, sandbox)
                test_cmd = ["python" if c == "{python}" else str(c) for c in task.test_cmd]
                test_runner = DockerTestRunner(sandbox, agent_dir, hidden_dir, test_cmd)
            else:
                workspace = Workspace(agent_dir)
                test_cmd = task.resolved_test_cmd()
                test_runner = LocalTestRunner(agent_dir, hidden_dir, test_cmd)

            if use_mcp:
                # MCP 通道：工具经真实 MCP 协议执行，策略评估 + 授权决策全程留痕
                from .mcptools import AuditedMCPClient, MCPToolClient, SERVER_SCRIPT

                # MCP 服务器需要隐藏测试源码：经一次性临时文件传递，服务端读取后即删除
                sources_file = Path(tempfile.mkstemp(prefix="atp-hidden-", suffix=".json")[1])
                sources_file.write_text(
                    json.dumps(getattr(test_runner, "sources", [])), encoding="utf-8"
                )
                mcp_client = MCPToolClient(
                    [sys.executable, str(SERVER_SCRIPT)],
                    workspace=agent_dir,
                    hidden_dir=hidden_dir,
                    test_cmd=task.resolved_test_cmd(),
                    sources_file=sources_file,
                ).start()
                try:
                    sources_file.unlink()
                except OSError:
                    pass
                audited = AuditedMCPClient(mcp_client, policy, collector)
                audited.record_declarations()
                agent = agent or MiniAgent(llm, max_turns=max_turns, tool_client=audited)
            else:
                agent = agent or MiniAgent(llm, max_turns=max_turns, policy=policy)

            try:
                agent.run(task.instruction, test_cmd, workspace, collector, test_runner=test_runner)
            except Exception as e:  # Agent 异常也进入轨迹，判定仍以测试为准
                collector.error(f"Agent 执行异常: {type(e).__name__}: {e}")
                agent_error = True

            # 最终判定：隔离副本 + 原始隐藏测试（Agent 的篡改无效），fail-closed 判定
            test_res = test_runner.run(timeout=180)
            collector.command_exec(
                test_res["cmd"], test_res["exit_code"], test_res["stdout_tail"], test_res["stderr_tail"]
            )
            verdict_output = test_res["stdout_tail"] + "\n" + test_res["stderr_tail"]
            passed = bool(test_res.get("verdict_ok")) and not agent_error
            if not test_res.get("verdict_ok"):
                collector.error(f"最终判定未通过: {test_res.get('verdict_reason', '未知原因')}")
            collector.run_result(passed, verdict_output[-2000:])
            status = "passed" if passed else ("error" if agent_error else "failed")
        except Exception as e:  # 装配等执行器自身异常
            collector.error(f"任务执行异常: {type(e).__name__}: {e}")
            passed = False
            status = "error"
        finally:
            if mcp_client is not None:
                mcp_client.close()
            if sandbox is not None:
                sandbox.stop()
            if cleanup_dir is not None:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            for extra_dir in cleanup_dirs:
                shutil.rmtree(extra_dir, ignore_errors=True)

        self.store.finish_attempt(attempt_id, status)
        return AttemptResult(attempt_id=attempt_id, status=status, passed=passed)
