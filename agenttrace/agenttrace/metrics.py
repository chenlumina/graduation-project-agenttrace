"""两层评测指标（开题报告"关键技术 4"）。

- 结果层：任务是否通过（以隐藏测试判定为准）；
- 过程层：从轨迹本身计算的行为质量指标——无效工具调用率、重复调用率、
  调用循环检测、连续错误重试深度等，用于刻画 Agent 行为并支撑失败归因。

过程层指标口径（v1，规则版，论文中需报告并讨论其局限）：
- 无效工具调用率 invalid_tool_rate ：status=error 的工具调用占比；
- 重复调用率   duplicate_tool_rate ：(tool, args) 完全相同的调用占比；
- 循环检测     loop_detected       ：同一 (tool, args) 连续出现 ≥3 次判定为循环；
- 连续错误重试 max_consecutive_errors：连续 status=error 的工具调用最大长度。
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
    "unknown": "未知原因",
}


def classify(events: list[TraceEvent]) -> dict[str, Any]:
    """规则版失败归因：按优先级从轨迹证据中提取失败原因标签。

    通过的任务也可能带过程异常标签（如越界尝试被拒后完成任务），
    此时 primary=passed，标签保留在 labels 中供过程分析使用。
    """
    metrics = compute_metrics(events)
    error_msgs = [str(e.data.get("message", "")) for e in events if e.type == "error"]
    err_tool_results = [
        str(e.data.get("result", ""))
        for e in events
        if e.type == "tool_call" and e.data.get("status") == "error"
    ]
    joined = "\n".join(error_msgs + err_tool_results)

    labels: list[str] = []
    if "路径越界" in joined:
        labels.append("workspace_escape")
    if "未知工具" in joined:
        labels.append("unknown_tool")
    if "空回复" in joined:
        labels.append("empty_response")
    if "无法解析" in joined:
        labels.append("parse_error")
    if "达到最大轮数" in joined or "max_turns_exceeded" in joined:
        labels.append("max_turns")

    if metrics["passed"]:
        primary = "passed"
    elif labels:
        primary = labels[0]
    elif metrics["finish_reached"]:
        primary = "tests_failed"
    else:
        primary = "incomplete"
    return {"passed": metrics["passed"], "primary": primary, "labels": labels}
