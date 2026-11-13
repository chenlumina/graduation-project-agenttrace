"""轨迹脱敏（R-22）：识别并遮蔽敏感信息，供导出与采集侧钩子使用。

定位：轨迹包含完整对话、文件内容与命令输出，可能夹带密钥/令牌/个人信息。
本模块提供两个入口：
- redact_text(text)：替换单段文本中的敏感模式；
- redact_obj(obj)：递归处理事件 data（dict/list/str），其余类型原样返回。

启用方式：
- 导出：CLI `export --redact`（默认开启，--no-redact 关闭）；store.export(redact=True)。
- 采集：环境变量 AGENTTRACE_REDACT=1 时，collector 在落库前对 data 脱敏。

设计取舍：脱敏是"最小遮蔽"——保留可读结构与长度特征（便于过程分析），
仅替换敏感片段本身。规则可扩展；正则覆盖有限，不能替代人工检查。
"""
from __future__ import annotations

import re
from typing import Any

#: (正则, 替换文本)；替换文本可用 \g<name> 引用分组
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 常见 API Key 形态
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "sk-***REDACTED***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA***REDACTED***"),
    # Bearer / Basic 凭据
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._\-+/=]{16,}"), r"\1 ***REDACTED***"),
    # 键值语境中的密钥字段（JSON / env / ini）
    (re.compile(r'(?i)("?(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)"?\s*[:=]\s*")([^"\n]{8,})(")'),
     r"\1***REDACTED***\3"),
    # PEM 私钥块
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "-----BEGIN PRIVATE KEY-----***REDACTED***-----END PRIVATE KEY-----"),
    # 邮箱
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "***@***"),
)


def redact_text(text: str) -> str:
    """遮蔽单段文本中的敏感模式（幂等：已遮蔽内容不会被重复处理）。"""
    if not text:
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_obj(obj: Any) -> Any:
    """递归脱敏：str 走 redact_text，dict/list 逐项处理，其余原样返回。"""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj
