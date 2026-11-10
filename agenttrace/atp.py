"""AgentTrace 统一命令行入口。

用法：
    python atp.py run-task tasks/fix_leap_year --db traces.db
    python atp.py export <attempt_id> --db traces.db --out trace.json
    python atp.py attempts --db traces.db

说明：部分发行版 Python 启用了安全路径模式（sys.flags.safe_path），
`python -m agenttrace` 会因工作目录不在 sys.path 而失败；本入口显式把
源码目录加入 sys.path，保证在任何标准 Python 3.11+ 环境下可用。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agenttrace.__main__ import main


def _load_env(path: Path | None = None) -> None:
    """加载本地 .env 文件（存在时）；不覆盖已有环境变量。

    用于存放 API Key 等本地凭据：不进源码、不进文档、不提交。
    """
    path = path or Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    _load_env()
    sys.exit(main())
