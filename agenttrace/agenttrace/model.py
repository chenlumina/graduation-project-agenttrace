"""统一轨迹数据模型（开题报告"关键技术 1"）。

轨迹 = 一次评测尝试（Attempt）内的有序事件流。事件类型与必需字段：

- llm_call     : model, messages, response, usage          —— 一次模型调用
  （R-08 起 messages 为**增量记录**：首轮为完整对话快照 messages_mode="full"，
  其后各轮只记录自上轮之后新增的消息 messages_mode="incremental"，
  完整对话用 rebuild_messages(events) 重放重建，消除平方级存储放大）
- tool_call    : tool, args, result, status                —— Agent 的一个动作
- file_diff    : path, diff                                —— 工作区文件改动（unified diff）
- command_exec : cmd, exit_code, stdout_tail, stderr_tail  —— 一条命令的执行
- run_result   : passed, output_tail                       —— 隐藏测试最终判定
- error        : message                                   —— 采集到的异常
- mcp_audit    : decision, tool, rule_id, reason           —— MCP 协议层声明事件（declared/undeclared）
- policy_audit : decision, tool, rule_id, reason           —— 权限策略决策（allow/deny，通道无关）

事件通过 parent_id 建立父子关系：tool_call 挂在其触发的 llm_call 之下；
file_diff（及写文件衍生的代码审计）挂在产生它的 tool_call 之下（R-10 起），
形成 llm_call → tool_call → file_diff 三层因果链，改动可归属到具体工具调用。

schema 版本：v4 起（v3 新增 policy_audit 事件：把授权决策与 MCP 传输解耦，
本地通道与 MCP 通道共用同一套策略评估与留痕口径；v4 起 llm_call.messages
改为增量记录并携带 messages_mode 标记，旧库无该字段的 llm_call 视为 full 快照）。
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = 4

AGENT_STATUSES = ("running", "passed", "failed", "error")


class TraceValidationError(ValueError):
    """轨迹数据不符合模型约束。"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex


# 事件类型 -> 必需的 data 字段
EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "llm_call": ("model", "messages", "response", "usage"),
    "tool_call": ("tool", "args", "result", "status"),
    "file_diff": ("path", "diff"),
    "command_exec": ("cmd", "exit_code", "stdout_tail", "stderr_tail"),
    "run_result": ("passed", "output_tail"),
    "error": ("message",),
    "mcp_audit": ("decision", "tool", "rule_id", "reason"),
    "policy_audit": ("decision", "tool", "rule_id", "reason"),
}


@dataclass
class TraceEvent:
    """轨迹事件：轨迹的最小组成单元，同一尝试内按 seq 严格递增。"""

    id: str
    attempt_id: str
    seq: int
    ts: str
    type: str
    parent_id: Optional[str]
    data: dict[str, Any]

    def validate(self) -> None:
        if self.type not in EVENT_TYPES:
            raise TraceValidationError(f"未知事件类型: {self.type!r}")
        if not isinstance(self.data, dict):
            raise TraceValidationError("data 必须是对象")
        if not isinstance(self.seq, int) or self.seq < 1:
            raise TraceValidationError(f"seq 必须为正整数: {self.seq!r}")
        missing = [k for k in EVENT_TYPES[self.type] if k not in self.data]
        if missing:
            raise TraceValidationError(f"事件 {self.type} 缺少必需字段: {missing}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "attempt_id": self.attempt_id,
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "parent_id": self.parent_id,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TraceEvent":
        ev = cls(
            id=d["id"],
            attempt_id=d["attempt_id"],
            seq=d["seq"],
            ts=d["ts"],
            type=d["type"],
            parent_id=d.get("parent_id"),
            data=d.get("data", {}),
        )
        ev.validate()
        return ev


@dataclass
class AttemptMeta:
    """一次评测尝试的元信息。"""

    attempt_id: str
    task_id: str
    agent: str
    model: str
    status: str = "running"
    started_at: str = field(default_factory=now_iso)
    finished_at: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rebuild_messages(events: list["TraceEvent"]) -> list[dict[str, str]]:
    """从轨迹事件序列重建完整对话（R-08）。

    规则：messages_mode="incremental" 的 llm_call 逐段追加；
    "full"（或旧轨迹无标记）为当轮完整快照，整段重置。
    新轨迹（v4）：首轮 full + 后续增量 → 精确重建；
    旧轨迹（≤v3）：每个 llm_call 均为 full → 结果为最后一个 full 快照。
    """
    full: list[dict[str, str]] = []
    for e in events:
        if e.type != "llm_call":
            continue
        msgs = e.data.get("messages") or []
        if e.data.get("messages_mode") == "incremental":
            full.extend(msgs)
        else:
            full = list(msgs)
    return full
