"""两层评测指标（开题报告"关键技术 4"）。

- 结果层：任务是否通过（以隐藏测试判定为准）；
- 过程层：从轨迹本身计算的行为质量指标——无效工具调用率、重复调用率、
  调用循环检测、连续错误重试深度等，用于刻画 Agent 行为并支撑失败归因。

过程层指标口径（v1，规则版，论文中需报告并讨论其局限）：
- 无效工具调用率 invalid_tool_rate ：status=error 的工具调用占比；
- 重复调用率   duplicate_tool_rate ：(tool, args) 完全相同的调用占比；
- 循环检测     loop_detected       ：同一 (tool, args) 连续出现 ≥3 次判定为循环；
- 连续错误重试 max_consecutive_errors：连续 status=error 的工具调用最大长度。

失败归因（R-14 v2）：事件携带结构化 error_code（枚举、机器可读、与展示文案分离），
classify 优先读 error_code；旧轨迹（无 error_code）回退文本匹配，仅作过渡兼容。
主因判定规则见 classify 文档字符串——终止性错误码中最早出现者为主因，
不再依赖错误文案或代码语句顺序。
"""
from __future__ import annotations

import json
from typing import Any

from .model import TraceEvent


def _tool_key(ev: TraceEvent) -> tuple:
    return (ev.data.get("tool"), json.dumps(ev.data.get("args", {}), sort_keys=True, ensure_ascii=False))


def compute_metrics(events: list[TraceEvent]) -> dict[str, Any]:
    tool_calls = [e for e in events if e.type == "tool_call"]
    llm_calls = [e for e in events if e.type == "llm_call"]
    error_events = [e for e in events if e.type == "error"]
    run_results = [e for e in events if e.type == "run_result"]
    # 授权决策留痕：mcp_audit（协议层声明）+ policy_audit（策略决策，两通道共用）
    audit_events = [e for e in events if e.type in ("mcp_audit", "policy_audit")]

    invalid = [e for e in tool_calls if e.data.get("status") == "error"]
    keys = [_tool_key(e) for e in tool_calls]
    duplicates = len(keys) - len(set(keys))
    loop_detected = any(keys[i] == keys[i + 1] == keys[i + 2] for i in range(len(keys) - 2))

    max_consecutive_errors = streak = 0
    for e in tool_calls:
        if e.data.get("status") == "error":
            streak += 1
            max_consecutive_errors = max(max_consecutive_errors, streak)
        else:
            streak = 0

    prompt = sum(int(e.data.get("usage", {}).get("prompt_tokens", 0)) for e in llm_calls)
    completion = sum(int(e.data.get("usage", {}).get("completion_tokens", 0)) for e in llm_calls)

    # R-09：统一耗时归因——各阶段耗时占比 + 最慢步骤 TopN
    llm_time = sum(int(e.data.get("duration_ms", 0)) for e in llm_calls)
    tool_time = sum(int(e.data.get("duration_ms", 0)) for e in tool_calls)
    command_events = [e for e in events if e.type == "command_exec"]
    command_time = sum(int(e.data.get("duration_ms", 0)) for e in command_events)
    accounted = llm_time + tool_time
    # 最慢步骤 TopN（llm_call / tool_call / command_exec 混合排序，标注类型供人工判读；
    # command_exec 为 run_tests/run_command 的子集，占比口径不重复计入）
    timed = [
        e for e in events
        if e.type in ("llm_call", "tool_call", "command_exec") and e.data.get("duration_ms")
    ]
    slowest_steps = [
        {
            "seq": e.seq,
            "type": e.type,
            "name": e.data.get("tool") or e.data.get("model")
            or (" ".join(str(c) for c in e.data.get("cmd", []))[:80] if e.type == "command_exec" else ""),
            "duration_ms": int(e.data["duration_ms"]),
        }
        for e in sorted(timed, key=lambda x: int(x.data["duration_ms"]), reverse=True)[:5]
    ]

    passed = bool(run_results and run_results[-1].data["passed"])

    # R-15：重试过程指标——解析失败 / 空回复各自的重试计数（读结构化 error_code）
    parse_retries = sum(
        1 for e in error_events if e.data.get("error_code") == "parse_error"
    )
    empty_retries = sum(
        1 for e in error_events if e.data.get("error_code") == "empty_response"
    )
    return {
        "llm_call_count": len(llm_calls),
        "tool_call_count": len(tool_calls),
        "invalid_tool_calls": len(invalid),
        "invalid_tool_rate": round(len(invalid) / len(tool_calls), 4) if tool_calls else 0.0,
        "duplicate_tool_calls": duplicates,
        "duplicate_tool_rate": round(duplicates / len(tool_calls), 4) if tool_calls else 0.0,
        "loop_detected": loop_detected,
        "max_consecutive_errors": max_consecutive_errors,
        "finish_reached": any(e.data.get("tool") == "finish" for e in tool_calls),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "llm_time_ms": llm_time,
        "tool_time_ms": tool_time,
        "command_time_ms": command_time,
        "time_breakdown": {
            "llm_ms": llm_time,
            "tool_ms": tool_time,
            "command_ms": command_time,  # 子集信息：已含于 tool_ms，不重复计入占比
            "llm_share": round(llm_time / accounted, 4) if accounted else 0.0,
            "tool_share": round(tool_time / accounted, 4) if accounted else 0.0,
        },
        "slowest_steps": slowest_steps,
        "error_event_count": len(error_events),
        "parse_error_retries": parse_retries,
        "empty_response_retries": empty_retries,
        "audit_total": len(audit_events),
        "audit_denied": sum(1 for e in audit_events if e.data.get("decision") == "deny"),
        "code_audit_high": sum(
            1 for e in events
            if e.type == "file_diff" and isinstance(e.data.get("audit"), dict)
            for f in e.data["audit"].get("findings", []) if f.get("level") == "high"
        ),
        "code_audit_warn": sum(
            1 for e in events
            if e.type == "file_diff" and isinstance(e.data.get("audit"), dict)
            for f in e.data["audit"].get("findings", []) if f.get("level") == "warn"
        ),
        "passed": passed,
    }


PRIMARY_LABELS = {
    "passed": "通过",
    "parse_error": "动作解析失败",
    "empty_response": "模型空回复",
    "max_turns": "轮数耗尽",
    "tests_failed": "实现未通过测试",
    "incomplete": "未完成即终止",
    "workspace_escape": "工作区越界尝试",
    "unknown_tool": "调用未知工具",
    "tool_error": "工具执行失败",
    "agent_exception": "Agent 执行异常",
    "runner_exception": "执行器异常",
    "verdict_failed": "最终判定未通过",
    "unknown": "未知原因",
}

#: 会终止本次尝试的错误码（R-14 主因判定：终止性错误优先于过程性异常）
_TERMINAL_CODES = {
    "max_turns", "parse_error", "empty_response", "agent_exception", "runner_exception",
}


def _extract_codes(events: list[TraceEvent]) -> list[tuple[int, str]]:
    """按事件顺序收集 (seq, error_code)：error 事件与 error 状态的 tool_call。"""
    out: list[tuple[int, str]] = []
    for e in sorted(events, key=lambda x: x.seq):
        if e.type == "error" and e.data.get("error_code"):
            out.append((e.seq, str(e.data["error_code"])))
        elif e.type == "tool_call" and e.data.get("status") == "error" and e.data.get("error_code"):
            out.append((e.seq, str(e.data["error_code"])))
    return out


#: 旧轨迹（无 error_code 字段）的文本匹配回退表：v1 口径，仅作过渡期兼容
_LEGACY_TEXT_RULES = (
    ("workspace_escape", ("路径越界",)),
    ("unknown_tool", ("未知工具",)),
    ("empty_response", ("空回复",)),
    ("parse_error", ("无法解析",)),
    ("max_turns", ("达到最大轮数", "max_turns_exceeded")),
)


def classify(events: list[TraceEvent]) -> dict[str, Any]:
    """失败归因（R-14 v2）：优先读结构化 error_code，旧轨迹回退文本匹配。

    主因判定规则（明确、可写进论文）：
    1. 通过 → passed；
    2. 存在终止性错误码（max_turns / parse_error / empty_response / agent_exception /
       runner_exception）→ 取其中**最早出现**的一个；
    3. 无终止性错误但判定未通过（verdict_failed）或已 finish → tests_failed；
    4. 仅有过程性异常（如越界被拒）但未完成 → 取最早的过程性错误码；
    5. 无任何证据 → incomplete。
    通过的任务也可能带过程异常标签，primary=passed，标签保留供过程分析。
    """
    metrics = compute_metrics(events)
    seq_codes = _extract_codes(events)

    if seq_codes:
        labels = list(dict.fromkeys(c for _, c in seq_codes))
    else:
        # 旧轨迹回退：文本匹配（文案改动会导致归因失效，v2 起不再依赖）
        error_msgs = [str(e.data.get("message", "")) for e in events if e.type == "error"]
        err_tool_results = [
            str(e.data.get("result", ""))
            for e in events
            if e.type == "tool_call" and e.data.get("status") == "error"
        ]
        joined = "\n".join(error_msgs + err_tool_results)
        labels = [
            code for code, needles in _LEGACY_TEXT_RULES
            if any(n in joined for n in needles)
        ]
        if "空回复" in joined and "empty_response" in joined:
            labels.append("empty_response")

    codes = [c for _, c in seq_codes]
    if metrics["passed"]:
        primary = "passed"
    else:
        terminal = [c for c in codes if c in _TERMINAL_CODES]
        if terminal:
            primary = terminal[0]  # 最早的终止性错误
        elif "verdict_failed" in codes:
            primary = "tests_failed"
        elif metrics["finish_reached"]:
            primary = "tests_failed"
        elif codes:
            primary = codes[0]  # 最早的过程性异常
        elif labels:
            primary = labels[0]
        else:
            primary = "incomplete"
    return {"passed": metrics["passed"], "primary": primary, "labels": labels}
