"""Explicit text-only Anthropic Messages adapter, without SDK dependencies."""

from __future__ import annotations

from typing import Any

from .base import (
    Cancellation,
    Module,
    ProviderError,
    Result,
    Transport,
    count,
    text_messages,
)

ANTHROPIC_MESSAGES_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# Stop reasons a text draft can end with; tool use, server-tool pauses and stop sequences are never requested here.
COMPLETED_STOPS = {"end_turn"}
INCOMPLETE_STOPS = {"max_tokens", "model_context_window_exceeded"}
REFUSED_STOPS = {"refusal"}
# No cache_control is ever sent, so any cache token count is usage this adapter cannot price.
CACHE_USAGE_FIELDS = ("cache_creation_input_tokens", "cache_read_input_tokens")


class AnthropicMessages:
    def prepare(
        self, module: Module, context: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        if module.model is None or module.limits is None:
            raise ProviderError("failed", "anthropic_activation_unresolved")
        messages = text_messages(context, prompt)
        return {
            "model": module.model,
            "max_tokens": module.limits.max_output_tokens,
            "system": messages[0]["content"],
            "messages": [{"role": "user", "content": messages[1]["content"]}],
        }

    def generate(
        self,
        module: Module,
        context: dict[str, Any],
        prompt: str,
        transport: Transport,
        cancel: Cancellation,
        secret: str | None,
    ) -> Result:
        if (
            module.limits is None
            or module.endpoint != ANTHROPIC_MESSAGES_ENDPOINT
            or module.model is None
            or not secret
        ):
            raise ProviderError("failed", "anthropic_activation_unresolved")
        payload = self.prepare(module, context, prompt)
        reply = transport.post(
            module.endpoint,
            payload,
            {
                "Content-Type": "application/json",
                "x-api-key": secret,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            module.limits,
            cancel,
        )
        content, usage = reply.get("content"), reply.get("usage")
        if (
            reply.get("type") != "message"
            or reply.get("role") != "assistant"
            or not isinstance(content, list)
            or not isinstance(usage, dict)
        ):
            raise ProviderError("unknown", "invalid_anthropic_result")
        texts: list[str] = []
        for block in content:
            if (
                not isinstance(block, dict)
                or block.get("type") != "text"
                or not isinstance(block.get("text"), str)
            ):
                raise ProviderError("unknown", "unsupported_anthropic_content")
            texts.append(block["text"])
        if any(usage.get(field) not in (None, 0) for field in CACHE_USAGE_FIELDS):
            raise ProviderError("unknown", "unexpected_anthropic_cache_usage")
        incoming, outgoing = (
            count(usage.get("input_tokens")),
            count(usage.get("output_tokens")),
        )
        stop = reply.get("stop_reason")
        text = "\n".join(texts)
        if stop in REFUSED_STOPS:
            status = "refused"
        elif stop in INCOMPLETE_STOPS:
            status = "incomplete"
        elif stop in COMPLETED_STOPS:
            if not text.strip():
                raise ProviderError("unknown", "missing_anthropic_text")
            status = "completed"
        else:
            raise ProviderError("unknown", "unsupported_anthropic_stop")
        request_id = reply.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise ProviderError("unknown", "missing_anthropic_request_id")
        return Result(status, text, incoming, outgoing, request_id)
