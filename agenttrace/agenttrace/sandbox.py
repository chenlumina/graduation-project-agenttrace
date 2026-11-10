"""Docker 沙箱：被测 Agent 的工具命令在隔离容器中执行（M6）。

隔离模型（v2，论文中如实报告其边界）：
- 资源限额：--memory / --cpus / --pids-limit；
- 权限收敛：--cap-drop ALL + no-new-privileges；
- 网络隔离：--network none；
- 测试防篡改/防窥探（C-3 修复）：隐藏测试**不挂载进 Agent 容器**，
  判定时由宿主侧组装"Agent 产物副本 + 原始隐藏测试"的干净目录，
  以只读方式挂载进一次性新鲜容器执行；
- 保密性（防窥探测试内容）由可选的策略层 read_deny_globs 提供，容器层不重复。

与 Docker 交互全部走 CLI（不经 SDK），便于在不同 Docker 兼容实现上运行；
CLI 路径优先取 PATH，找不到时回退 Windows 默认安装位置。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .tools import Workspace

DEFAULT_IMAGE = os.environ.get("AGENTTRACE_DOCKER_IMAGE", "python:3.13-slim")

_FALLBACK_CLI = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")


def docker_cli() -> str:
    found = shutil.which("docker")
    if found:
        return found
    if _FALLBACK_CLI.exists():
        return str(_FALLBACK_CLI)
    raise RuntimeError("未找到 docker CLI；请确认 Docker Desktop 已安装并启动")


def engine_available() -> bool:
    try:
        proc = subprocess.run(
            [docker_cli(), "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:
        return False


def _mount(spec: str) -> str:
    """统一挂载规格的路径分隔符（Windows 反斜杠 → 正斜杠）。"""
    return spec.replace("\\", "/")


def _result(cmd: list[str], proc: subprocess.CompletedProcess) -> dict[str, Any]:
    return {
        "cmd": cmd,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


class DockerSandbox:
    """每次尝试一个常驻容器：Agent 的命令经 docker exec 在其中执行。

    隐藏测试**不挂载进 Agent 容器**（C-3 修复）：Agent 既读不到测试内容，
    也无法篡改；测试只在判定容器里以原始副本执行。
    """

    def __init__(
        self,
        agent_dir: Path,
        hidden_dir: Path | None = None,
        image: str = DEFAULT_IMAGE,
        memory: str = "512m",
        cpus: str = "1",
    ):
        self.cli = docker_cli()
        self.agent_dir = Path(agent_dir)
        self.hidden_dir = Path(hidden_dir) if hidden_dir else None
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.name = f"atp-{uuid.uuid4().hex[:12]}"

    def start(self) -> "DockerSandbox":
        # Agent 容器只挂载自己的工作区：隐藏测试不可见、不可写
        mounts = ["-v", _mount(f"{self.agent_dir}:/workspace")]
        cmd = [
            self.cli, "run", "-d", "--name", self.name,
            "--network", "none", "--memory", self.memory, "--cpus", self.cpus,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--tmpfs", "/tmp:size=64m",
            *mounts, "-w", "/workspace", self.image, "sleep", "infinity",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Docker 容器启动失败: {proc.stderr.strip()[:300]}")
        return self

    def exec(self, cmd: list[str], timeout: int = 60) -> dict[str, Any]:
        """在容器内执行命令。超时时杀掉的是 docker 客户端进程，容器内进程
        仍受资源限额约束，并随容器的最终销毁结束（v1 已知边界，论文中说明）。"""
        try:
            proc = subprocess.run(
                [self.cli, "exec", "-w", "/workspace", self.name, *[str(c) for c in cmd]],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "cmd": cmd,
                "exit_code": -1,
                "stdout_tail": "",
                "stderr_tail": f"容器内命令超时（>{timeout}s）",
            }
        return _result(cmd, proc)

    def run_verdict(
        self,
        verdict_root: Path,
        sentinel_dir: Path,
        payload: dict[str, Any],
        timeout: int = 180,
    ) -> dict[str, Any]:
        """最终判定：一次性新鲜容器。

        - 判定目录（被测产物 + 判定脚本）以**只读**方式挂载；
        - 隐藏测试源码经 stdin 传入，**不落盘**（被测代码读不到测试内容）；
        - 哨兵目录单独可写挂载，供判定子进程回报结果。
        """
        full = [
            self.cli, "run", "--rm", "-i",
            "--network", "none", "--memory", self.memory, "--cpus", self.cpus,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--tmpfs", "/tmp:size=64m",
            "-v", _mount(f"{verdict_root}:/verdict:ro"),
            "-v", _mount(f"{sentinel_dir}:/sentinel"),
            "-w", "/verdict/app",
            "-e", "PYTHONPATH=/verdict/app",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTHONNOUSERSITE=1",
            self.image, "python", "/verdict/runner.py",
        ]
        try:
            proc = subprocess.run(
                full, input=json.dumps(payload), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "cmd": full,
                "exit_code": -1,
                "stdout_tail": "",
                "stderr_tail": f"判定容器超时（>{timeout}s）",
            }
        return _result(full, proc)

    def stop(self) -> None:
        try:
            subprocess.run(
                [self.cli, "rm", "-f", self.name],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
        except Exception:
            pass  # 清理失败不影响评测结果


class DockerWorkspace(Workspace):
    """与本地 Workspace 同接口；命令在沙箱容器内执行，文件读写走宿主侧（绑定挂载同源）。"""

    def __init__(self, root: Path, sandbox: DockerSandbox):
        super().__init__(root)
        self.sandbox = sandbox

    def run(self, cmd: list[str], timeout: int = 60) -> dict[str, Any]:
        return self.sandbox.exec(cmd, timeout=timeout)
