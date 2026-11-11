"""LLM 接入层：统一 chat 接口。

- ScriptedLLM    ：确定性脚本 LLM，按预设 JSON 动作序列返回，用于离线打通链路与测试；
- OpenAICompatLLM ：OpenAI 兼容接口适配器，需用户自行配置环境变量，不内置、不猜测任何密钥。
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    # R-11：采样参数快照（temperature/top_p/seed/provider 等，不含密钥）与结束原因，
    # 由采集层随 llm_call 事件落库，保证实验可复现
    params: dict | None = None
    finish_reason: str = ""


class ScriptedLLM:
    """按预设动作序列逐个返回 JSON 字符串的确定性 LLM（离线演示/回归测试用）。"""

    def __init__(self, actions: list[dict[str, Any]], model: str = "scripted-mini"):
        self.model = model
        self._actions: list[dict[str, Any]] = list(actions)
        self._i = 0

    def chat(self, messages: list[dict[str, str]]) -> LLMResponse:
        if self._i >= len(self._actions):
            raise RuntimeError("脚本动作已用尽（scripted actions exhausted）")
        action = self._actions[self._i]
        self._i += 1
        return LLMResponse(
            content=json.dumps(action, ensure_ascii=False),
            model=self.model,
            prompt_tokens=120,
            completion_tokens=40,
        )


class OpenAICompatLLM:
    """OpenAI 兼容 chat/completions 适配器。

    凭据约定（绝不猜测、绝不内置密钥）：
    - AGENTTRACE_LLM_BASE_URL ：服务地址，如 https://api.example.com/v1
    - AGENTTRACE_LLM_MODEL    ：模型名
    - AGENTTRACE_LLM_API_KEY  ：用户自己的 API Key
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        top_p: float | None = None,
        seed: int | None = None,
    ):
        self.base_url = base_url or os.environ.get("AGENTTRACE_LLM_BASE_URL")
        self.model = model or os.environ.get("AGENTTRACE_LLM_MODEL")
        self.api_key = api_key or os.environ.get("AGENTTRACE_LLM_API_KEY")
        # R-11：采样参数快照——落轨迹用于复现，绝不含密钥
        self.params: dict[str, Any] = {
            "temperature": temperature,
            "top_p": top_p,
            "seed": seed,
            "base_url": self.base_url,
            "interface": "openai-compat",
        }
        missing = [
            name
            for name, val in (
                ("AGENTTRACE_LLM_BASE_URL", self.base_url),
                ("AGENTTRACE_LLM_MODEL", self.model),
                ("AGENTTRACE_LLM_API_KEY", self.api_key),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                "使用真实模型需要配置环境变量: " + ", ".join(missing) + "（不使用猜测的凭据）"
            )

    def chat(self, messages: list[dict[str, str]]) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model, "messages": messages,
            "temperature": self.params["temperature"],
        }
        if self.params.get("top_p") is not None:
            payload["top_p"] = self.params["top_p"]
        if self.params.get("seed") is not None:
            payload["seed"] = self.params["seed"]
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode("utf-8"))
        usage = body.get("usage", {})
        choices = body.get("choices") or [{}]
        return LLMResponse(
            content=choices[0].get("message", {}).get("content", ""),
            model=self.model,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            params=dict(self.params),
            finish_reason=str(choices[0].get("finish_reason") or ""),
        )
