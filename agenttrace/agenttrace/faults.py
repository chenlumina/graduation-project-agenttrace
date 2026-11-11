"""故障注入（R-05）：可控扰动实验，验证平台的过程诊断价值。

目的：正常任务流程下过程指标无区分度（全部通过）；主动构造异常条件后，
平台的耗时归因（R-09）、无效调用率、注入留痕（H-3）等模块应产生可观测信号——
"功能存在性"到"功能有效性"的证据链，可作为论文独立章节。

注入能力（均作用于本地通道的工具执行层，MCP 通道暂不支持）：
- slow_tool_ms     ：每次工具执行人为延迟（毫秒）→ 耗时归因指标可见 tool_time_ms 异常；
- flaky_rate       ：每次工具调用以该概率随机失败 → invalid_tool_rate 上升、
                     错误码 tool_error 事件增加（结构化归因可判读）；
- inject_instruction：read_file 返回内容前插入提示注入样本 → injection_flags 留痕。

用法：
    MiniAgent(llm, faults=FaultProfile(...))            # 单次运行
    run_experiment(..., fault="slow_tool")              # 批量实验（profile 名记入 meta）
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FaultProfile:
    """一次扰动实验的注入配置（可序列化进 attempt.meta 留痕）。"""

    name: str
    slow_tool_ms: int = 0
    flaky_rate: float = 0.0
    inject_instruction: bool = False

    def to_meta(self) -> dict:
        return {
            "name": self.name,
            "slow_tool_ms": self.slow_tool_ms,
            "flaky_rate": self.flaky_rate,
            "inject_instruction": self.inject_instruction,
        }


#: 预置扰动场景（论文故障注入实验的建议矩阵）
PRESET_PROFILES: dict[str, FaultProfile] = {
    "baseline": FaultProfile("baseline"),
    "slow_tool": FaultProfile("slow_tool", slow_tool_ms=1500),
    "flaky_tool": FaultProfile("flaky_tool", flaky_rate=0.5),
    "prompt_injection": FaultProfile("prompt_injection", inject_instruction=True),
}


def resolve_fault(fault: "str | FaultProfile | None") -> FaultProfile | None:
    """按名称解析预置 profile；传 None/未知名返回 None（不注入）。"""
    if fault is None:
        return None
    if isinstance(fault, FaultProfile):
        return fault
    return PRESET_PROFILES.get(fault)
