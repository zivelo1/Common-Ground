"""Native Ollama chat adapter; local-only assurance is an activation prerequisite."""

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


class Ollama:
    def prepare(
        self, module: Module, context: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        if module.model is None or module.limits is None:
            raise ProviderError("failed", "ollama_activation_unresolved")
        return {
            "model": module.model,
            "messages": text_messages(context, prompt),
            "stream": False,
            "options": {"num_predict": module.limits.max_output_tokens},
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
            or module.endpoint is None
            or module.model is None
            or not module.local_only_verified
        ):
            raise ProviderError("failed", "ollama_activation_unresolved")
        payload = self.prepare(module, context, prompt)
        reply = transport.post(
            module.endpoint,
            payload,
            {"Content-Type": "application/json"},
            module.limits,
            cancel,
        )
        message = reply.get("message")
        if (
            reply.get("done") is not True
            or not isinstance(message, dict)
            or message.get("role") != "assistant"
            or not isinstance(message.get("content"), str)
        ):
            raise ProviderError("unknown", "invalid_ollama_result")
        reason = reply.get("done_reason")
        if reason not in {"stop", "length"}:
            raise ProviderError("unknown", "unsupported_ollama_completion")
        status = (
            "completed"
            if reason == "stop" and message["content"].strip()
            else "incomplete"
        )
        return Result(
            status,
            message["content"],
            count(reply.get("prompt_eval_count")),
            count(reply.get("eval_count")),
        )
