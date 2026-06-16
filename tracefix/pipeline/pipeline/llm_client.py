"""Small provider-agnostic LLM helpers used by the pipeline.

Tool-calling lives in ``tracefix.pipeline.tool_client``. This module provides
the shared configuration object and a text-only client used for context
summarization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-5"
    api_key: str = ""
    base_url: str = ""
    reasoning_effort: str = ""
    thinking_budget: int = 0
    max_tokens: int = 32768
    temperature: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMTextResponse:
    content: str
    usage: dict[str, int] = field(default_factory=dict)
    raw_response: Any = None


class LLMClient:
    """Text-only LLM client for summarization and utility calls."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._openai_client = None
        self._anthropic_client = None

    def chat(self, *, system_prompt: str, user_prompt: str) -> LLMTextResponse:
        if self.config.provider in {"openai", "openrouter", "ollama"}:
            return self._chat_openai_compatible(system_prompt, user_prompt)
        if self.config.provider == "anthropic":
            return self._chat_anthropic(system_prompt, user_prompt)
        raise ValueError(f"Unsupported provider: {self.config.provider}")

    def _chat_openai_compatible(self, system_prompt: str, user_prompt: str) -> LLMTextResponse:
        from openai import OpenAI

        if self._openai_client is None:
            kwargs: dict[str, Any] = {"api_key": self.config.api_key}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._openai_client = OpenAI(**kwargs)

        request: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.config.base_url:
            request["max_tokens"] = self.config.max_tokens
        else:
            request["max_completion_tokens"] = self.config.max_tokens
        if self.config.reasoning_effort and self._supports_reasoning_effort():
            request["reasoning_effort"] = self.config.reasoning_effort
        elif self.config.temperature != 1.0:
            request["temperature"] = self.config.temperature

        response = self._openai_client.chat.completions.create(**request)
        message = response.choices[0].message
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens or 0,
                "completion_tokens": response.usage.completion_tokens or 0,
                "total_tokens": response.usage.total_tokens or 0,
            }
        return LLMTextResponse(
            content=message.content or "",
            usage=usage,
            raw_response=response,
        )

    def _supports_reasoning_effort(self) -> bool:
        if self.config.provider != "openai" or self.config.base_url:
            return False
        model = (self.config.model or "").lower()
        return model.startswith(("gpt-5", "o1", "o3", "o4"))

    def _chat_anthropic(self, system_prompt: str, user_prompt: str) -> LLMTextResponse:
        from anthropic import Anthropic

        if self._anthropic_client is None:
            self._anthropic_client = Anthropic(api_key=self.config.api_key)

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if self.config.thinking_budget > 0:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.config.thinking_budget,
            }
        elif self.config.temperature != 1.0:
            kwargs["temperature"] = self.config.temperature

        response = self._anthropic_client.messages.create(**kwargs)
        content_parts = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        ]
        usage = {
            "prompt_tokens": getattr(response.usage, "input_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "output_tokens", 0) or 0,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return LLMTextResponse(
            content="\n".join(content_parts),
            usage=usage,
            raw_response=response,
        )
