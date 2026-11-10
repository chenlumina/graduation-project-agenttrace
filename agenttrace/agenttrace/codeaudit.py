"""AI 生成代码质量审计（开题报告 P2 子模块，M7）。

对轨迹中每一步 AI 产生的代码改动（file_diff 的新内容）做静态审计：
- 语法可编译性：ast.parse；
- 风险调用（基于 AST）：eval / exec / compile / __import__、os.system / os.popen、
  pickle.loads、subprocess 系列启用 shell=True；
- 裸 except（吞异常）；
- 敏感信息模式（基于正则）：硬编码密钥 / 口令样式。

审计结果作为 file_diff 事件的可选 audit 字段随轨迹留痕（不改变既有事件
的必需字段，向后兼容），供回放、指标（code_audit_high/warn）与论文实验使用。
当前仅覆盖 Python 文件（.py）；其他类型标记 not_python 跳过并如实说明。
"""
from __future__ import annotations

import ast
import re
from typing import Any

_RISK_CALLS: dict[str, str] = {
    "eval": "high",
    "exec": "high",
    "compile": "warn",
    "__import__": "high",
}

_RISK_ATTR_CALLS: dict[tuple[str, str], str] = {
    ("os", "system"): "high",
    ("os", "popen"): "high",
    ("pickle", "loads"): "warn",
}

_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|api_key|apikey|token)\s*=\s*[\"'][^\"']{6,}[\"']"
)


def _finding(level: str, rule: str, message: str, lineno: int) -> dict[str, Any]:
    return {"level": level, "rule": rule, "message": message, "lineno": lineno}


class _RiskVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[dict[str, Any]] = []

    def _base_name(self, func: ast.expr) -> str | None:
        return func.id if isinstance(func, ast.Name) else None

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in _RISK_CALLS:
                self.findings.append(
                    _finding(_RISK_CALLS[name], f"risk_call:{name}", f"调用了 {name}()", node.lineno)
                )
        elif isinstance(node.func, ast.Attribute):
            base_name = self._base_name(node.func.value)
            if base_name is not None:
                pair = (base_name, node.func.attr)
                if pair in _RISK_ATTR_CALLS:
                    level = _RISK_ATTR_CALLS[pair]
                    self.findings.append(
                        _finding(level, f"risk_call:{pair[0]}.{pair[1]}",
                                 f"调用了 {pair[0]}.{pair[1]}()", node.lineno)
                    )
                if base_name == "subprocess" and node.func.attr in (
                    "run", "Popen", "call", "check_output", "check_call"
                ):
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            self.findings.append(
                                _finding("high", "risk_call:subprocess.shell",
                                         "subprocess 调用启用了 shell=True", node.lineno)
                            )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.findings.append(
                _finding("warn", "bare_except", "裸 except 会吞掉所有异常", node.lineno)
            )
        self.generic_visit(node)


def audit_python_source(path: str, content: str) -> dict[str, Any]:
    """审计一段 Python 源码，返回 {audited, syntax_ok, findings:[...]}。"""
    result: dict[str, Any] = {"path": path, "syntax_ok": True, "findings": [], "audited": True}
    if not path.endswith(".py"):
        result["audited"] = False
        result["note"] = "非 Python 文件，跳过审计"
        return result

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        result["syntax_ok"] = False
        result["findings"].append(
            _finding("high", "syntax_error", f"语法错误: {e.msg}", e.lineno or 0)
        )
        return result

    visitor = _RiskVisitor()
    visitor.visit(tree)
    result["findings"].extend(visitor.findings)

    for lineno, line in enumerate(content.splitlines(), 1):
        if _SECRET_PATTERN.search(line):
            result["findings"].append(
                _finding("warn", "hardcoded_secret", "疑似硬编码密钥/口令", lineno)
            )

    # 去重（同一行同一规则只记一次）并按行号排序
    seen: set[tuple] = set()
    unique: list[dict[str, Any]] = []
    for f in result["findings"]:
        key = (f["rule"], f["lineno"], f["message"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    result["findings"] = sorted(unique, key=lambda f: (f["lineno"], f["rule"]))
    return result
