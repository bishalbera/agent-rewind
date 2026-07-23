
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import anthropic

DEFAULT_MODEL = os.getenv("REWIND_MODEL", "claude-opus-4-8")

# Models that still accept a `temperature` sampling parameter.
_TEMPERATURE_OK_PREFIXES = ("claude-opus-4-6", "claude-sonnet-4", "claude-haiku", "gpt-")


def model_accepts_temperature(model: str) -> bool:
    return model.startswith(_TEMPERATURE_OK_PREFIXES)


@dataclass
class LLMResponse:
    """Provider-neutral view of one completion."""

    id: str
    model: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    #: Raw assistant content blocks, appended verbatim as the assistant turn.
    content: list[dict[str, Any]]
    text: str
    #: Parsed tool calls: [{"id", "name", "input"}].
    tool_uses: list[dict[str, Any]] = field(default_factory=list)


class LLMClient:
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model
        self._client = anthropic.Anthropic()

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 1024,
        temperature: float | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        model = model or self.model
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        if temperature is not None and model_accepts_temperature(model):
            kwargs["temperature"] = temperature

        resp = self._client.messages.create(**kwargs)

        content = [self._block_to_dict(b) for b in resp.content]
        text = "".join(b["text"] for b in content if b["type"] == "text")
        tool_uses = [
            {"id": b["id"], "name": b["name"], "input": b["input"]}
            for b in content
            if b["type"] == "tool_use"
        ]
        return LLMResponse(
            id=resp.id,
            model=resp.model,
            stop_reason=resp.stop_reason or "",
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            content=content,
            text=text,
            tool_uses=tool_uses,
        )

    @staticmethod
    def _block_to_dict(block: Any) -> dict[str, Any]:
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        # thinking / other block types — keep type so we can round-trip if needed.
        return {"type": block.type}
