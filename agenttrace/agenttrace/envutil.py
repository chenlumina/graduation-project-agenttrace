"""子进程环境变量白名单（H-1 修复：凭据不外泄给被测/工具子进程）。

平台自身会持有 LLM 凭据（AGENTTRACE_LLM_API_KEY 等）。这些变量如果随
os.environ 全量传给 Agent 启动的命令、MCP 工具服务器或容器内的测试进程，
就等于把密钥交给了"不可信代码"。本模块提供统一的环境变量清洗：

- 只保留运行 Python / Docker / 测试所需的基础变量（白名单）；
- 额外剔除名字里含 KEY / TOKEN / SECRET / PASSWORD 等敏感片段的变量；
- 调用方可用 extra 显式追加少量必需变量（如 AT_WORKSPACE）。
"""
from __future__ import annotations

import os
from typing import Mapping

#: 允许透传给子进程的基础环境变量
SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "SYSTEMDRIVE",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMDATA",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
    }
)

#: 命中即剔除的敏感变量名片段（不区分大小写）
SENSITIVE_HINTS: tuple[str, ...] = (
    "API_KEY",
    "APIKEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "SESSION_KEY",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)


def is_sensitive(name: str) -> bool:
    upper = name.upper()
    return any(hint in upper for hint in SENSITIVE_HINTS)


def sanitized_env(
    extra: Mapping[str, str] | None = None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """返回可安全传给子进程的环境变量字典。

    - base 默认为当前进程环境（os.environ）；
    - 仅保留白名单变量，且剔除敏感名；
    - extra 中的键原样保留（调用方显式指定，视同已审查）。
    """
    src = os.environ if base is None else base
    env: dict[str, str] = {}
    for key, value in src.items():
        if key in SAFE_ENV_KEYS and not is_sensitive(key):
            env[key] = value
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env
