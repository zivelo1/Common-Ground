"""Explicit text-only adapter for the OpenAI-compatible Chat Completions format, without SDK dependencies.

Several API providers speak this format. Each registry entry is one instance locked to its provider's one reviewed
endpoint, so a configuration cannot point a key at another origin.
"""

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

# Read 12/10/2026: xAI "Chat Completions" guide (offered as a legacy endpoint beside its Responses API); OpenRouter API
# reference (max_tokens is deprecated there in favour of max_completion_tokens); OpenCode Zen docs (the unified
# chat-completions endpoint, in beta; models served through other wire formats are not reachable here).
XAI_CHAT_ENDPOINT = "https://api.x.ai/v1/chat/completions"
OPENROUTER_CHAT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
OPENCODE_ZEN_CHAT_ENDPOINT = "https://opencode.ai/zen/v1/chat/completions"
COMPLETED_FINISHES = {"stop"}
INCOMPLETE_FINISHES = {"length"}
REFUSED_FINISHES = {"content_filter"}


class ChatCompletions:
    def __init__(self, name: str, endpoint: str, limit_field: str) -> None:
        self.name, self.endpoint, self.limit_field = name, endpoint, limit_field

    def prepare(
        self, module: Module, context: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        if module.model is None or module.limits is None:
            raise ProviderError("failed", f"{self.name}_activation_unresolved")
        return {
            "model": module.model,
            "messages": text_messages(context, prompt),
            self.limit_field: module.limits.max_output_tokens,
            "stream": False,
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
            or module.endpoint != self.endpoint
            or module.model is None
            or not secret
        ):
            raise ProviderError("failed", f"{self.name}_activation_unresolved")
        payload = self.prepare(module, context, prompt)
        reply = transport.post(
            module.endpoint,
            payload,
            {"Content-Type": "application/json", "Authorization": "Bearer " + secret},
            module.limits,
            cancel,
        )
        choices, usage = reply.get("choices"), reply.get("usage")
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
            or not isinstance(usage, dict)
        ):
            raise ProviderError("unknown", f"invalid_{self.name}_result")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ProviderError("unknown", f"invalid_{self.name}_message")
        if message.get("tool_calls") or message.get("function_call"):
            # No tool is ever offered; a tool call is not a text draft.
            raise ProviderError("unknown", f"unsupported_{self.name}_tool_call")
        content, refusal = message.get("content"), message.get("refusal")
        if content is not None and not isinstance(content, str):
            raise ProviderError("unknown", f"unsupported_{self.name}_content")
        text = content or ""
        incoming, visible, total = (
            count(usage.get("prompt_tokens")),
            count(usage.get("completion_tokens")),
            count(usage.get("total_tokens")),
        )
        details = usage.get("completion_tokens_details")
        reasoning = count(details.get("reasoning_tokens", 0)) if isinstance(details, dict) else 0
        # Hidden reasoning is billed output. Providers count it either inside completion_tokens (OpenAI) or beside it
        # (xAI's documented example); total - prompt must match one of the two, or the usage cannot be priced.
        outgoing = total - incoming
        if outgoing not in {visible, visible + reasoning}:
            raise ProviderError("unknown", f"inconsistent_{self.name}_usage")
        finish = choice.get("finish_reason")
        if (isinstance(refusal, str) and refusal) or finish in REFUSED_FINISHES:
            status = "refused"
        elif finish in INCOMPLETE_FINISHES:
            status = "incomplete"
        elif finish in COMPLETED_FINISHES:
            if not text.strip():
                raise ProviderError("unknown", f"missing_{self.name}_text")
            status = "completed"
        else:
            raise ProviderError("unknown", f"unsupported_{self.name}_finish")
        request_id = reply.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise ProviderError("unknown", f"missing_{self.name}_request_id")
        return Result(status, text, incoming, outgoing, request_id)
