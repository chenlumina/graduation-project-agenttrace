"""轨迹采集器：为每次尝试维护递增 seq，统一生成并落库事件。"""
from __future__ import annotations

from typing import Any, Optional

from .model import TraceEvent, new_id, now_iso
from .store import TraceStore


class TraceCollector:
    """一次尝试（Attempt）内的事件采集入口。"""

    def __init__(self, store: TraceStore, attempt_id: str):
        self._store = store
        self.attempt_id = attempt_id
        self._seq = 0

    def emit(self, type: str, data: dict[str, Any], parent_id: Optional[str] = None) -> TraceEvent:
        self._seq += 1
        ev = TraceEvent(
            id=new_id(),
            attempt_id=self.attempt_id,
            seq=self._seq,
            ts=now_iso(),
            type=type,
            parent_id=parent_id,
            data=data,
        )
        self._store.append_event(ev)
        return ev

    # ---- 便捷方法：保证各事件 data 字段口径一致 ----

    def llm_call(
        self,
        model: str,
        messages: list[dict[str, str]],
        response: str,
        usage: dict[str, int],
        duration_ms: Optional[int] = None,
        parent_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        finish_reason: str = "",
        incremental: bool = False,
        context_chars: Optional[int] = None,
    ) -> TraceEvent:
        data: dict[str, Any] = {
            "model": model,
            # R-08：增量式记录——incremental=True 时 messages 仅为自上轮后新增的消息，
            # 完整对话用 rebuild_messages(events) 重建（消除平方级存储放大）
            "messages": messages,
            "messages_mode": "incremental" if incremental else "full",
            "response": response,
            "usage": usage,
        }
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        if params:  # R-11：采样参数快照（不含密钥），保证实验可复现
            data["params"] = params
        if finish_reason:
            data["finish_reason"] = finish_reason
        if context_chars is not None:
            data["context_chars"] = context_chars  # R-18：本轮 prompt 总字符数
        return self.emit("llm_call", data, parent_id)

    def tool_call(
        self, tool: str, args: dict[str, Any], result: Any, status: str,
        parent_id: Optional[str] = None, extra: Optional[dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
    ) -> TraceEvent:
        data: dict[str, Any] = {
            "tool": tool, "args": args, "result": result, "status": status,
        }
        if duration_ms is not None:
            data["duration_ms"] = duration_ms  # R-09：统一耗时口径
        if extra:
            # 只允许补充扩展字段，不允许覆盖必需字段
            data.update({k: v for k, v in extra.items() if k not in data})
        return self.emit("tool_call", data, parent_id)

    def policy_audit(
        self, decision: str, tool: str, rule_id: str, reason: str,
        parent_id: Optional[str] = None,
    ) -> TraceEvent:
        """授权决策留痕（通道无关：本地通道与 MCP 通道共用同一口径）。"""
        return self.emit(
            "policy_audit",
            {"decision": decision, "tool": tool, "rule_id": rule_id, "reason": reason},
            parent_id,
        )

    def file_diff(
        self, path: str, diff: str, parent_id: Optional[str] = None,
        audit: Optional[dict[str, Any]] = None,
    ) -> TraceEvent:
        data: dict[str, Any] = {"path": path, "diff": diff}
        if audit is not None:
            data["audit"] = audit
        return self.emit("file_diff", data, parent_id)

    def command_exec(
        self,
        cmd: list[str],
        exit_code: int,
        stdout_tail: str,
        stderr_tail: str,
        parent_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> TraceEvent:
        data: dict[str, Any] = {
            "cmd": cmd, "exit_code": exit_code, "stdout_tail": stdout_tail, "stderr_tail": stderr_tail,
        }
        if duration_ms is not None:
            data["duration_ms"] = duration_ms  # R-09：统一耗时口径
        return self.emit("command_exec", data, parent_id)

    def run_result(self, passed: bool, output_tail: str) -> TraceEvent:
        return self.emit("run_result", {"passed": passed, "output_tail": output_tail})

    def error(
        self, message: str, parent_id: Optional[str] = None, code: Optional[str] = None,
    ) -> TraceEvent:
        """code 为结构化错误码（R-14 铺垫）：机器可读枚举，与展示文案分离。"""
        data: dict[str, Any] = {"message": message}
        if code:
            data["error_code"] = code
        return self.emit("error", data, parent_id)
