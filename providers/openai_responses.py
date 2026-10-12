"""Explicit text-only Responses reference adapter, without SDK dependencies."""

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

OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"


class OpenAIResponses:
    def prepare(
        self, module: Module, context: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        if module.model is None or module.limits is None:
            raise ProviderError("failed", "openai_activation_unresolved")
        messages = text_messages(context, prompt)
        return {
            "model": module.model,
            "instructions": messages[0]["content"],
            "input": messages[1]["content"],
            "max_output_tokens": module.limits.max_output_tokens,
            "store": False,
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
            or module.endpoint != OPENAI_RESPONSES_ENDPOINT
            or module.model is None
            or not secret
        ):
            raise ProviderError("failed", "openai_activation_unresolved")
        payload = self.prepare(module, context, prompt)
        reply = transport.post(
            module.endpoint,
            payload,
            {"Content-Type": "application/json", "Authorization": "Bearer " + secret},
            module.limits,
            cancel,
        )
        outputs, usage = reply.get("output"), reply.get("usage")
        if not isinstance(outputs, list) or not isinstance(usage, dict):
            raise ProviderError("unknown", "invalid_openai_result")
        texts: list[str] = []
        refused = False
        for item in outputs:
            if not isinstance(item, dict):
                raise ProviderError("unknown", "invalid_openai_output")
            if item.get("type") == "reasoning":
                continue
            if (
                item.get("type") != "message"
                or item.get("role") != "assistant"
                or not isinstance(item.get("content"), list)
            ):
                raise ProviderError("unknown", "unsupported_openai_output")
            for part in item["content"]:
                if not isinstance(part, dict):
                    raise ProviderError("unknown", "invalid_openai_content")
                if part.get("type") == "output_text" and isinstance(
                    part.get("text"), str
                ):
                    texts.append(part["text"])
                elif part.get("type") == "refusal" and isinstance(
                    part.get("refusal"), str
                ):
                    refused = True
                else:
                    raise ProviderError("unknown", "unsupported_openai_content")
        incoming, outgoing = (
            count(usage.get("input_tokens")),
            count(usage.get("output_tokens")),
        )
        if count(usage.get("total_tokens")) != incoming + outgoing:
            raise ProviderError("unknown", "inconsistent_openai_usage")
        status = reply.get("status")
        if status not in {"completed", "incomplete"}:
            raise ProviderError("unknown", "unsupported_openai_status")
        text = "\n".join(texts)
        if refused:
            status = "refused"
        elif status == "completed" and not text.strip():
            raise ProviderError("unknown", "missing_openai_text")
        request_id = reply.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise ProviderError("unknown", "missing_openai_request_id")
        return Result(status, text, incoming, outgoing, request_id)
