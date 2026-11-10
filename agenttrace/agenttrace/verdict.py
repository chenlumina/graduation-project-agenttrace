"""判定执行器（安全模型 v4）：隐藏测试在内存中执行，判定结果 fail-closed。

核心约定：
- **隐藏测试不落盘到判定环境**：父进程读取测试源码，经 stdin 交给独立的判定子进程
  （`agenttrace/_verdict_runner.py`）在内存中加载执行。被测代码既读不到测试文件，
  也无法把测试内容经 stdout 回灌给模型（C-3 修复）。
- **被测代码不能改写测试框架**：判定子进程在运行前对 `unittest` 命名空间与
  `TestCase` 类做快照、运行后比对，并安装审计钩子阻断属性改写（C-3 修复）。
- **判定 fail-closed**：必须同时满足「退出码 0 + 输出含 OK + 哨兵文件 nonce 匹配 +
  用例数 > 0 + 未检出篡改」才算通过。被测代码 `os._exit(0)` 或抹掉输出都会判失败。
- 判定目录只放被测产物（剔除标准库同名模块、`sitecustomize`、字节码缓存、符号链接），
  隐藏测试始终待在独立临时根里，Agent 的工具与命令都够不着。
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .envutil import sanitized_env

#: 标准库顶层模块名（Python 3.10+ 提供）：判定目录中若出现同名条目，
#: 会遮蔽标准库（unittest / test / os / types …），使测试框架被 Agent 控制。
_STDLIB_NAMES: frozenset[str] = frozenset(getattr(sys, "stdlib_module_names", ())) | {
    "sitecustomize",
    "usercustomize",
}

#: 被测产物顶层中会被剔除的文件/目录（可劫持解释器或遮蔽标准库）
EXCLUDED_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "sitecustomize.py",  # 解释器启动时自动导入 → 可篡改判定
        "usercustomize.py",
        "conftest.py",
        "unittest",  # 遮蔽标准库 unittest
    }
)

#: 判定子进程脚本（随包分发，运行时复制到判定目录）
RUNNER_SCRIPT = Path(__file__).resolve().parent / "_verdict_runner.py"

#: 判定目录中一律剔除的字节码缓存（可复用陈旧 .pyc 劫持源码）
_EXCLUDED_SUFFIXES = (".pyc", ".pyo")


def _is_shadowing_name(name: str) -> bool:
    """顶层条目是否会遮蔽标准库模块。

    既看完整名（目录名 "unittest"），也看去扩展名的文件名（"unittest.py"）——
    后者同样会遮蔽标准库包，因为判定目录的工作目录在 sys.path 首位。
    """
    if name in _STDLIB_NAMES or name in EXCLUDED_TOP_LEVEL or name == "hidden_tests":
        return True
    stem = Path(name).stem
    return stem != name and stem in _STDLIB_NAMES


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    """拷贝时忽略：字节码缓存 + **任意层级的符号链接**。

    符号链接必须整个跳过：跟随会把宿主文件内容复制进判定目录，
    保留链接则仍可被被测代码读取宿主文件（评审发现 #5）。
    """
    ignored = set(shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")(directory, names))
    for name in names:
        try:
            if (Path(directory) / name).is_symlink():
                ignored.add(name)
        except OSError:
            ignored.add(name)
    return ignored


def _copy_workspace(agent_dir: Path, dest: Path) -> None:
    """把被测产物复制到判定目录（剔除遮蔽项、字节码缓存与符号链接）。"""
    dest.mkdir(parents=True, exist_ok=True)
    if not agent_dir.exists():
        return
    for item in agent_dir.iterdir():
        if _is_shadowing_name(item.name):
            continue
        if item.is_symlink():
            continue  # 符号链接可能指向工作区之外，直接跳过
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                target,
                dirs_exist_ok=True,
                symlinks=False,
                ignore=_copy_ignore,
            )
        else:
            if item.suffix in _EXCLUDED_SUFFIXES:
                continue  # 陈旧字节码不得进入判定目录
            shutil.copy2(item, target)


def prepare_verdict_dir(agent_dir: Path, dest: Path) -> Path:
    """组装判定目录：dest/app 下是被测产物（隐藏测试不落在这里）。"""
    app_dir = Path(dest) / "app"
    _copy_workspace(Path(agent_dir), app_dir)
    return app_dir


def read_hidden_sources(hidden_dir: Path, purge: bool = False) -> list[dict[str, str]]:
    """读取隐藏测试源码（只在宿主进程内读取，不落到判定环境）。

    purge=True 时读取后**删除磁盘上的测试文件**：被测代码在判定进程里向上遍历
    临时目录时若还能找到隐藏测试文件，就能读走其内容（评审发现 #4）。
    """
    files: list[dict[str, str]] = []
    hidden = Path(hidden_dir)
    if not hidden.exists():
        return files
    for path in sorted(hidden.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        files.append(
            {"name": path.name, "source": path.read_text(encoding="utf-8")}
        )
    if purge:
        for path in sorted(hidden.rglob("*.py"), reverse=True):
            try:
                path.unlink()
            except OSError:
                pass
    return files


def _write_sentinel(path: Path, nonce: str, ran: int, tampered: bool, ok: bool) -> None:
    payload = f"{nonce}|ran={ran}|tamper={'yes' if tampered else 'no'}|ok={'yes' if ok else 'no'}"
    try:
        path.write_text(payload, encoding="utf-8")
    except OSError:
        pass


def _parse_sentinel(path: Path, nonce: str) -> dict[str, Any] | None:
    """解析哨兵文件；nonce 不匹配一律视为无效（fail-closed）。"""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    parts = raw.split("|")
    if not parts or parts[0] != nonce:
        return None
    info: dict[str, Any] = {"ran": 0, "tampered": True, "ok": False}
    for part in parts[1:]:
        key, _, value = part.partition("=")
        if key == "ran":
            info["ran"] = int(value or 0)
        elif key == "tamper":
            info["tampered"] = value == "yes"
        elif key == "ok":
            info["ok"] = value == "yes"
    return info


def _verdict_ok(exit_code: int, output: str, sentinel: dict[str, Any] | None) -> tuple[bool, str]:
    """fail-closed 判定：任一条件不满足即失败。"""
    if sentinel is None:
        return False, "判定哨兵缺失或 nonce 不匹配（判定结果不可信）"
    if sentinel.get("tampered"):
        return False, "检测到被测代码改写测试框架（unittest），判定作废"
    ran = int(sentinel.get("ran", 0))
    if ran <= 0:
        return False, "未收集到任何测试用例（0 用例），判定作废"
    if exit_code != 0:
        return False, f"测试进程退出码 {exit_code}，判定失败"
    if "OK" not in output:
        return False, "测试输出中未见 OK，判定失败"
    if not sentinel.get("ok"):
        return False, "判定子进程报告未通过"
    return True, "通过"


def _run_child(cmd: list[str], cwd: Path, env: dict[str, str], payload: dict[str, Any],
               timeout: int) -> dict[str, Any]:
    """以 stdin 传参的方式启动判定子进程。"""
    kwargs: dict[str, Any] = dict(
        cwd=str(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if os.name != "nt":
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as e:
        return {"exit_code": -2, "stdout": "", "stderr": f"判定进程启动失败: {e}"}
    try:
        out, err = proc.communicate(json.dumps(payload), timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass
        out, err = proc.communicate()
        return {"exit_code": -1, "stdout": out or "", "stderr": f"判定超时（>{timeout}s）"}
    return {"exit_code": proc.returncode, "stdout": out or "", "stderr": err or ""}


def _build_payload(hidden_dir: Path, sentinel_path: str, ws_dir: str | Path) -> dict[str, Any]:
    # sentinel_path 必须是 POSIX 字符串：Windows 下 Path("/x") 会被转成反斜杠形式，
    # 容器内将写到错误位置（且宿主读不到）。
    # ws_dir 经 payload 传递（而非 PYTHONPATH）：部分发行版解释器内建 -E
    # （忽略 PYTHONPATH），判定子进程必须自己把被测产物目录插进 sys.path。
    return {
        "nonce": secrets.token_hex(16),
        "sentinel_path": str(sentinel_path),
        "ws_dir": str(ws_dir),
        "package": "hidden_tests",
        "files": read_hidden_sources(hidden_dir),
    }


def _local_cmd() -> list[str]:
    return [sys.executable, "-s", "-B", str(RUNNER_SCRIPT)]


def _is_unittest_discover(cmd: list[str]) -> bool:
    """判定命令是否为 `python -m unittest discover ...` 形态（可用内存执行）。"""
    parts = [str(c) for c in cmd]
    return any("unittest" in p for p in parts) and any("discover" in p for p in parts)


def _resolve_cmd(cmd: list[str]) -> list[str]:
    """把 {python} 占位符换成当前解释器。"""
    return [sys.executable if str(c) == "{python}" else str(c) for c in cmd]


def _fallback_run(agent_dir: Path, hidden_dir: Path, cmd: list[str], timeout: int) -> dict[str, Any]:
    """非 unittest-discover 命令的兜底执行路径（测试文件需落盘，属已知边界）。"""
    with tempfile.TemporaryDirectory(prefix="atp-verdict-fallback-") as td:
        root = Path(td)
        app_dir = prepare_verdict_dir(agent_dir, root)
        dest_hidden = app_dir / "hidden_tests"
        if Path(hidden_dir).exists():
            shutil.copytree(Path(hidden_dir), dest_hidden)
        argv = _resolve_cmd(cmd)
        kwargs: dict[str, Any] = dict(
            cwd=str(app_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=sanitized_env({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}),
        )
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(argv, **kwargs)
        except OSError as e:
            return {
                "cmd": argv, "exit_code": -2, "stdout_tail": "", "stderr_tail": f"命令启动失败: {e}",
                "tests_ran": 0, "tampered": False, "verdict_ok": False,
                "verdict_reason": f"测试命令启动失败: {e}",
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
                "cmd": argv, "exit_code": -1, "stdout_tail": (out or "")[-2000:],
                "stderr_tail": f"判定超时（>{timeout}s）", "tests_ran": 0, "tampered": False,
                "verdict_ok": False, "verdict_reason": "判定超时",
            }
        output = (out or "") + "\n" + (err or "")
        low = output.lower()
        zero = "ran 0 tests" in low or "no tests ran" in low
        ok = proc.returncode == 0 and not zero
        reason = "通过" if ok else ("未收集到测试用例" if zero else f"退出码 {proc.returncode}")
        return {
            "cmd": argv,
            "exit_code": proc.returncode,
            "stdout_tail": (out or "")[-2000:],
            "stderr_tail": (err or "")[-2000:],
            "tests_ran": 0,
            "tampered": False,
            "verdict_ok": ok,
            "verdict_reason": reason,
        }


class LocalTestRunner:
    """在宿主机上执行隐藏测试（测试源码只经 stdin 传递，不落盘）。"""

    def __init__(
        self,
        agent_dir: Path,
        hidden_dir: Path,
        test_cmd: list[str],
        sources: list[dict[str, str]] | None = None,
    ):
        self.agent_dir = Path(agent_dir)
        self.hidden_dir = Path(hidden_dir)
        self.test_cmd = list(test_cmd)
        # 隐藏测试源码在构造时一次性读入内存；走内存执行路径时并从磁盘移除：
        # 判定期间磁盘上不存在隐藏测试文件，被测代码无法通过遍历目录读到它们
        self._uses_in_memory = _is_unittest_discover(self.test_cmd)
        if sources is not None:
            self._sources = sources
        else:
            self._sources = read_hidden_sources(
                self.hidden_dir, purge=self._uses_in_memory
            )

    @property
    def sources(self) -> list[dict[str, str]]:
        return self._sources

    def run(self, timeout: int = 120) -> dict[str, Any]:
        if not _is_unittest_discover(self.test_cmd):
            # 非 unittest-discover 命令：走兜底路径（测试文件需落盘，属已知边界）
            return _fallback_run(self.agent_dir, self.hidden_dir, self.test_cmd, timeout)
        with tempfile.TemporaryDirectory(prefix="atp-verdict-") as td:
            root = Path(td)
            app_dir = prepare_verdict_dir(self.agent_dir, root)
            sentinel = root / "sentinel.txt"
            payload = _build_payload(self.hidden_dir, str(sentinel), self.agent_dir)
            payload["files"] = self._sources
            env = sanitized_env(
                {
                    "PYTHONPATH": str(app_dir),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1",
                }
            )
            cmd = _local_cmd()
            proc = _run_child(cmd, app_dir, env, payload, timeout)
            output = (proc["stdout"] or "") + "\n" + (proc["stderr"] or "")
            sentinel_info = _parse_sentinel(sentinel, payload["nonce"])
            ok, reason = _verdict_ok(proc["exit_code"], output, sentinel_info)
            return {
                "cmd": cmd,
                "exit_code": proc["exit_code"],
                "stdout_tail": (proc["stdout"] or "")[-2000:],
                "stderr_tail": (proc["stderr"] or "")[-2000:],
                "tests_ran": (sentinel_info or {}).get("ran", 0),
                "tampered": (sentinel_info or {}).get("tampered", True),
                "verdict_ok": ok,
                "verdict_reason": reason,
            }


class DockerTestRunner:
    """在一次性新鲜容器中执行隐藏测试（测试源码经 stdin 传递，不落盘）。"""

    def __init__(self, sandbox, agent_dir: Path, hidden_dir: Path, test_cmd: list[str],
                 sources: list[dict[str, str]] | None = None):
        self.sandbox = sandbox
        self.agent_dir = Path(agent_dir)
        self.hidden_dir = Path(hidden_dir)
        self.test_cmd = list(test_cmd)
        self._uses_in_memory = _is_unittest_discover(self.test_cmd)
        if sources is not None:
            self._sources = sources
        else:
            self._sources = read_hidden_sources(
                self.hidden_dir, purge=self._uses_in_memory
            )

    @property
    def sources(self) -> list[dict[str, str]]:
        return self._sources

    def run(self, timeout: int = 180) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="atp-verdict-") as td, \
                tempfile.TemporaryDirectory(prefix="atp-sentinel-") as td2:
            root = Path(td)
            sentinel_dir = Path(td2)
            app_dir = prepare_verdict_dir(self.agent_dir, root)
            shutil.copy2(RUNNER_SCRIPT, root / "runner.py")
            payload = _build_payload(self.hidden_dir, "/sentinel/result.txt", "/verdict/app")
            payload["files"] = self._sources
            proc = self.sandbox.run_verdict(root, sentinel_dir, payload, timeout=timeout)
            output = (proc.get("stdout_tail") or "") + "\n" + (proc.get("stderr_tail") or "")
            sentinel_info = _parse_sentinel(sentinel_dir / "result.txt", payload["nonce"])
            ok, reason = _verdict_ok(proc["exit_code"], output, sentinel_info)
            return {
                "cmd": proc.get("cmd", []),
                "exit_code": proc["exit_code"],
                "stdout_tail": proc.get("stdout_tail", ""),
                "stderr_tail": proc.get("stderr_tail", ""),
                "tests_ran": (sentinel_info or {}).get("ran", 0),
                "tampered": (sentinel_info or {}).get("tampered", True),
                "verdict_ok": ok,
                "verdict_reason": reason,
            }
