"""模型单价配置与成本折算（R-17）。

口径：成本金额 = prompt_tokens/1e6 × 输入单价 + completion_tokens/1e6 × 输出单价（CNY）。
单价表为每百万 token 人民币价格，按各平台公开价目填列；可用环境变量
AGENTTRACE_PRICE_FILE 指向 JSON 文件覆盖/补充（格式同 DEFAULT_PRICES）。

未知模型不猜测价格：计入 unpriced_tokens，报告如实标注"未计价"，
避免用错价目造成结论失真。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: 每百万 token 价格（CNY）：{"model": {"input": x, "output": y}}
#: 注：以下为公开价目的示例填列，实验前请按所用平台当期价目核对/覆盖。
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "mimo-v2.6-flash": {"input": 1.0, "output": 2.0},
    "mimo-v2.6-pro": {"input": 4.0, "output": 12.0},
    "gpt-4o-mini": {"input": 1.1, "output": 4.4},
    "gpt-4o": {"input": 18.0, "output": 72.0},
    "deepseek-chat": {"input": 2.0, "output": 8.0},
    "qwen-plus": {"input": 0.8, "output": 2.0},
}


def load_prices() -> dict[str, dict[str, float]]:
    """加载价目表：内置默认 + AGENTTRACE_PRICE_FILE JSON 覆盖。"""
    table = {k: dict(v) for k, v in DEFAULT_PRICES.items()}
    path = os.environ.get("AGENTTRACE_PRICE_FILE")
    if path and Path(path).exists():
        try:
            custom = json.loads(Path(path).read_text(encoding="utf-8"))
            for model, prices in custom.items():
                table[model] = {
                    "input": float(prices.get("input", 0.0)),
                    "output": float(prices.get("output", 0.0)),
                }
        except (ValueError, TypeError, OSError):
            pass  # 价目文件损坏时退回默认表，不阻断实验
    return table


def compute_cost(
    model: str, prompt_tokens: int, completion_tokens: int,
    prices: dict[str, dict[str, float]] | None = None,
) -> float | None:
    """折算单次调用成本（CNY）；模型无价目时返回 None（不猜测）。"""
    table = prices if prices is not None else load_prices()
    p = table.get(model)
    if p is None:
        return None
    return round(
        prompt_tokens / 1e6 * p["input"] + completion_tokens / 1e6 * p["output"], 6
    )
