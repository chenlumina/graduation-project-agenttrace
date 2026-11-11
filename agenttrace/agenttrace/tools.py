"""受限工作区：Agent 的文件与命令操作入口，带路径越界防护。"""
from __future__ import annotations

import difflib
import os
import signal
import subprocess
from pathlib import Path
from typing import Union

from .envutil import sanitized_env


class WorkspaceEscapeError(ValueError):
    """路径越界（R-14：结构化错误码 workspace_escape 的载体，兼容 ValueError 捕获）。"""

    error_code = "workspace_escape"


class Workspace:
    """以任务工作区为根的文件/命令访问层。Agent 只能通过它触碰文件系统。"""

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root).resolve()

    def resolve(self, rel: str) -> Path:
        """解析相对路径并确保不越出工作区根目录。"""
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root):
            raise WorkspaceEscapeError(f"路径越界: {rel!r}")
        return p

    def read(self, rel: str) -> str:
        return self.resolve(rel).read_text(encoding="utf-8")

    def write(self, rel: str, content: str) -> str:
        """写入文件并返回 unified diff（新建文件时 before 为空）。"""
        p = self.resolve(rel)
        before = p.read_text(encoding="utf-8") if p.exists() else ""
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
        )

    def run(self, cmd: list[str], timeout: int = 60) -> dict:
        """在工作区根目录执行命令，返回结构化结果（输出截尾）。

        安全约定：环境变量走白名单（不把 API Key 等凭据传给子进程），
        超时按进程组回收（POSIX），避免子进程残留。
        """
        argv = [str(c) for c in cmd]
        kwargs: dict = dict(
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=sanitized_env(),
        )
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(argv, **kwargs)
        except OSError as e:
            return {
                "cmd": argv,
                "exit_code": -2,
                "stdout_tail": "",
                "stderr_tail": f"命令启动失败: {e}",
            }
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            except Exception:
                pass
            out, err = proc.communicate()
            return {
                "cmd": argv,
                "exit_code": -1,
                "stdout_tail": (out or "")[-2000:],
                "stderr_tail": f"命令超时（>{timeout}s）\n{(err or '')[-2000:]}",
            }
        return {
            "cmd": argv,
            "exit_code": proc.returncode,
            "stdout_tail": (out or "")[-2000:],
            "stderr_tail": (err or "")[-2000:],
        }
