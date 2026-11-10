"""工具权限策略引擎（M4 审计子模块的决策核心）。

策略按以下顺序评估，命中即返回：
1. allow_tools           —— 工具允许清单（非空时，清单外一律拒绝）；
2. deny_tools            —— 工具黑名单；
3. read_deny_globs       —— read_file 禁读路径；
4. write_allow_globs     —— write_file 可写白名单（非空时，白名单外一律拒绝）；
5. arg_deny_patterns     —— 特定工具的参数禁用正则（对 JSON 序列化后的参数匹配）；
6. default_action        —— 未命中任何规则时的默认动作（allow / deny）。

路径语义（v2 起）：路径类规则一律在**归一化后的工作区相对路径**上匹配。
归一化会做词法折叠（"a/../b" → "b"），并在结果仍越出工作区根时抛
PathEscapeError。因此 "src/../hidden_tests/x.py" 这类写法无法再绕过
write 白名单（C-4 修复）；越界路径的读请求交由工作区边界校验拦截，
写请求直接拒绝。
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any

# 参数禁用正则的长度上限：任务包提供的正则不可控，过长/病态正则会造成 ReDoS
MAX_PATTERN_LEN = 200

#: 被匹配文本的长度上限（参数序列化后可能很大，限制长度以约束回溯代价）
MAX_MATCH_LEN = 100_000

#: 嵌套量词启发式检测：形如 (a+)+ / (a*)* 的模式会产生灾难性回溯
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*]")

try:  # 可选依赖：regex 支持超时中断，能从根上掐断病态正则
    import regex as _regex  # type: ignore
except ImportError:  # pragma: no cover - 环境无 regex 时退化为启发式防护
    _regex = None  # type: ignore

#: regex 引擎下的单次匹配超时（秒）
REGEX_TIMEOUT = 0.5

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class PathEscapeError(ValueError):
    """路径非法或越界（绝对路径 / 盘符 / 折叠后仍含 ".."）。"""


def normalize_rel_path(raw: Any) -> str:
    """把原始路径归一化为工作区相对路径（POSIX 风格）。

    - 反斜杠统一为 "/"，丢弃 "." 与空段；
    - 词法折叠 ".."：有前序段则弹出，无前序段说明越出工作区根 → PathEscapeError；
    - 绝对路径与 Windows 盘符一律 PathEscapeError。

    归一化结果与 Workspace.resolve() 的解析结果一致（后者额外处理符号链接），
    因此策略判断与真实落盘位置不会出现偏差。
    """
    if not isinstance(raw, str):
        raise PathEscapeError("路径必须是字符串")
    s = raw.strip().replace("\\", "/")
    if not s:
        raise PathEscapeError("路径为空")
    if s.startswith("/") or _WINDOWS_DRIVE.match(s):
        raise PathEscapeError(f"不允许绝对路径: {raw!r}")
    out: list[str] = []
    for seg in s.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not out:
                raise PathEscapeError(f"路径越界: {raw!r}")
            out.pop()
            continue
        out.append(seg)
    if not out:
        raise PathEscapeError("路径为空")
    return "/".join(out)


@dataclass
class PolicyDecision:
    allowed: bool
    rule_id: str
    reason: str


@dataclass
class Policy:
    name: str = "default"
    default_action: str = "allow"  # "allow" | "deny"
    deny_tools: list[str] = field(default_factory=list)
    read_deny_globs: list[str] = field(default_factory=list)
    write_allow_globs: list[str] = field(default_factory=list)
    arg_deny_patterns: dict[str, str] = field(default_factory=dict)  # tool -> 正则
    allow_tools: list[str] = field(default_factory=list)  # 非空时：仅允许这些工具

    def evaluate(self, tool: str, args: dict[str, Any]) -> PolicyDecision:
        if self.allow_tools and tool not in self.allow_tools:
            return PolicyDecision(
                False, "not_allowed_tool", f"工具 {tool} 不在允许清单 {self.allow_tools} 中"
            )

        if tool in self.deny_tools:
            return PolicyDecision(False, f"deny_tool:{tool}", f"工具 {tool} 在黑名单中")

        path = str(args.get("path", "")) if isinstance(args, dict) else ""
        if tool in ("read_file", "write_file") and path:
            try:
                norm = normalize_rel_path(path)
            except PathEscapeError as e:
                # 读：路径越界交由工作区边界校验拦截（记为工具错误）；
                # 写：直接拒绝，避免归一化失败被当成"不受白名单约束"
                if tool == "write_file":
                    return PolicyDecision(False, "path_invalid", str(e))
                norm = ""
            if norm:
                if tool == "read_file":
                    for g in self.read_deny_globs:
                        if fnmatch.fnmatch(norm, g):
                            return PolicyDecision(
                                False, "read_deny_path", f"路径 {norm} 匹配禁读规则 {g}"
                            )
                if tool == "write_file":
                    if self.write_allow_globs and not any(
                        fnmatch.fnmatch(norm, g) for g in self.write_allow_globs
                    ):
                        return PolicyDecision(
                            False,
                            "write_allowlist",
                            f"路径 {norm} 不在可写白名单 {self.write_allow_globs} 内",
                        )

        pattern = self.arg_deny_patterns.get(tool)
        if pattern:
            if len(pattern) > MAX_PATTERN_LEN:
                return PolicyDecision(
                    False, "arg_pattern_too_long",
                    f"参数禁用模式过长（>{MAX_PATTERN_LEN}），拒绝执行",
                )
            if _NESTED_QUANTIFIER.search(pattern):
                # 嵌套量词 → 灾难性回溯风险，拒绝使用该模式（fail closed）
                return PolicyDecision(
                    False, "arg_pattern_unsafe",
                    "参数禁用模式含嵌套量词（可能引发灾难性回溯），拒绝执行",
                )
            try:
                args_json = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
                target = args_json[:MAX_MATCH_LEN]
                if _regex is not None:
                    # 有 regex 引擎时带超时，超时即视为命中风险 → 拒绝
                    try:
                        hit = _regex.search(pattern, target, timeout=REGEX_TIMEOUT)
                    except TimeoutError:
                        return PolicyDecision(
                            False, "arg_pattern_timeout",
                            f"参数禁用模式匹配超时（>{REGEX_TIMEOUT}s），拒绝执行",
                        )
                    matched = bool(hit)
                else:
                    matched = bool(re.search(pattern, target))
                if matched:
                    return PolicyDecision(False, f"arg_pattern:{tool}", f"参数匹配禁用模式 {pattern}")
            except re.error:
                return PolicyDecision(
                    False, "arg_pattern_invalid", "参数禁用模式不是合法正则，拒绝执行"
                )

        if self.default_action == "deny":
            return PolicyDecision(False, "default_deny", "未命中允许规则，按默认动作拒绝")
        return PolicyDecision(True, "default_allow", "未命中拒绝规则，默认允许")

    # ---- 构造与派生 ----

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Policy | None":
        if not d:
            return None
        return cls(
            name=str(d.get("name", "policy")),
            default_action=str(d.get("default", "allow")),
            deny_tools=list(d.get("deny_tools", [])),
            read_deny_globs=list(d.get("read_deny_globs", [])),
            write_allow_globs=list(d.get("write_allow_globs", [])),
            arg_deny_patterns=dict(d.get("arg_deny_patterns", {})),
            allow_tools=list(d.get("allow_tools", [])),
        )

    @classmethod
    def safe_default(cls) -> "Policy":
        """任务包未声明策略时使用的内置安全策略。

        允许常规工具集（含 run_command——在 Docker 沙箱内是安全的），
        但隐藏测试目录永远不可读写。
        """
        return cls(
            name="builtin-safe",
            default_action="allow",
            allow_tools=["read_file", "write_file", "run_tests", "run_command", "finish"],
            read_deny_globs=["hidden_tests/*", "hidden_tests/**"],
        )

    def copy(self) -> "Policy":
        return replace(
            self,
            deny_tools=list(self.deny_tools),
            read_deny_globs=list(self.read_deny_globs),
            write_allow_globs=list(self.write_allow_globs),
            arg_deny_patterns=dict(self.arg_deny_patterns),
            allow_tools=list(self.allow_tools),
        )

    def with_extra(
        self,
        *,
        deny_tools: list[str] | None = None,
        read_deny_globs: list[str] | None = None,
        write_allow_globs: list[str] | None = None,
    ) -> "Policy":
        """派生一份叠加了平台硬规则的策略（平台规则不可被任务包覆盖）。"""
        p = self.copy()
        if deny_tools:
            p.deny_tools = sorted(set(p.deny_tools) | set(deny_tools))
        if read_deny_globs:
            p.read_deny_globs = sorted(set(p.read_deny_globs) | set(read_deny_globs))
        if write_allow_globs:
            p.write_allow_globs = sorted(set(p.write_allow_globs) | set(write_allow_globs))
        return p
