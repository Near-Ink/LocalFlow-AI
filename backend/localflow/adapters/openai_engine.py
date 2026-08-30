"""OpenAI 兼容引擎适配器 — 云端 API

支持任何 OpenAI 格式的接口：DeepSeek / GPT / 通义 / 本地 vLLM OpenAI 接口等。
"""

from __future__ import annotations

import json
from typing import AsyncIterator, List, Optional

import httpx

from ..ports.engine import (
    ChatMessage,
    ChatResponse,
    GenerateOptions,
    LLMEngine,
)


async def fetch_remote_models(base_url: str, api_key: str, timeout: float = 30.0) -> List[str]:
    """调用 OpenAI 兼容端点 GET /models 拉取该账号实际可用的模型 id。

    这是「模型选择严谨性」的关键：可用性由厂商官方接口实时返回，
    而非依赖本地维护的静态目录。不支持 /models 的端点会抛异常由调用方处理。
    """
    base = (base_url or "").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{base}/models", headers=headers)
        r.raise_for_status()
        data = r.json()
        ids = [m.get("id") for m in (data or {}).get("data", []) if isinstance(m, dict)]
        return [i for i in ids if i]


def _normalize_tool_calls(raw_calls) -> Optional[list]:
    """把后端原始 tool_calls 归一化为稳定结构 [{id,name,arguments(dict)}]"""
    if not raw_calls:
        return None
    result = []
    for i, c in enumerate(raw_calls):
        fn = c.get("function", {}) if isinstance(c, dict) else {}
        args = fn.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        result.append({
            "id": c.get("id") or f"call_{i}",
            "name": fn.get("name", ""),
            "arguments": args,
        })
    return result or None


class OpenAIEngine(LLMEngine):
    """OpenAI 兼容云端引擎适配器"""

    name = "openai"

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        default_model: str = "gpt-4o-mini",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self._client = httpx.AsyncClient(timeout=120.0)

    @property
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def health(self) -> bool:
        # 简单检查：能否连上 base_url（不消耗 token）
        if not self.api_key:
            return False
        try:
            r = await self._client.get(
                f"{self.base_url}/models",
                headers=self._headers,
            )
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[dict]:
        # 云端模型列表由用户配置，不通过 API 拉取（避免消耗且不同厂商返回格式不一）
        return [{"id": self.default_model, "name": self.default_model, "size": 0}]

    async def chat(
        self,
        model: str,
        messages: List[ChatMessage],
        options: Optional[GenerateOptions] = None,
    ) -> ChatResponse:
        opts = options or GenerateOptions()
        payload = {
            "model": model,
            "messages": [self._msg_to_dict(m) for m in messages],
            "stream": False,
            "temperature": opts.temperature,
            "top_p": opts.top_p,
        }
        if opts.max_tokens:
            payload["max_tokens"] = opts.max_tokens
        if opts.stop:
            payload["stop"] = opts.stop
        if opts.tools:
            payload["tools"] = opts.tools

        r = await self._client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers,
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]
        usage = data.get("usage", {})
        message = choice.get("message", {})
        return ChatResponse(
            content=message.get("content", ""),
            model=model,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=_normalize_tool_calls(message.get("tool_calls")),
            raw=data,
        )

    async def chat_stream(
        self,
        model: str,
        messages: List[ChatMessage],
        options: Optional[GenerateOptions] = None,
    ) -> AsyncIterator[str]:
        opts = options or GenerateOptions(stream=True)
        payload = {
            "model": model,
            "messages": [self._msg_to_dict(m) for m in messages],
            "stream": True,
            "temperature": opts.temperature,
            "top_p": opts.top_p,
        }
        if opts.max_tokens:
            payload["max_tokens"] = opts.max_tokens

        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers,
            json=payload,
        ) as resp:
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    import json
                    obj = json.loads(data)
                except Exception:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {})
                chunk = delta.get("content", "")
                if chunk:
                    yield chunk

    # --- helpers ---

    def _msg_to_dict(self, msg: ChatMessage) -> dict:
        d = {"role": msg.role}
        if msg.images:
            # OpenAI 兼容多模态：content 为 [{type:text},{type:image_url}]（用于支持视觉的模型）
            content = [{"type": "text", "text": msg.content}] if msg.content else []
            for img in msg.images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": img},  # img 本身是 data URI
                })
            d["content"] = content
        else:
            d["content"] = msg.content
        if msg.name:
            d["name"] = msg.name
        if msg.tool_calls:
            # agent 层用稳定结构 {id,name,arguments(dict)}；OpenAI 转成嵌套 wire 格式
            d["tool_calls"] = [
                {
                    "id": c.get("id"),
                    "type": "function",
                    "function": {
                        "name": c.get("name", ""),
                        "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False),
                    },
                }
                for c in msg.tool_calls if isinstance(c, dict)
            ]
        return d