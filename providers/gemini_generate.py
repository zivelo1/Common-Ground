"""Explicit text-only Gemini generateContent adapter, without SDK dependencies."""

from __future__ import annotations

import re
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

GEMINI_MODELS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
# The model becomes one URL path segment, so it may hold no separator, query or space.
GEMINI_MODEL = re.compile(r"[a-z0-9][a-z0-9.-]{0,127}\Z")
COMPLETED_FINISH = {"STOP"}
INCOMPLETE_FINISH = {"MAX_TOKENS"}
REFUSED_FINISH = {"SAFETY", "RECITATION", "LANGUAGE", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}
# proto3 JSON omits a count that is zero, so an absent count is a zero count, not a missing one.
OMITTED_ZERO = 0


class GeminiGenerate:
    def prepare(
        self, module: Module, context: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        if module.model is None or module.limits is None:
            raise ProviderError("failed", "gemini_activation_unresolved")
        if not GEMINI_MODEL.fullmatch(module.model):
            raise ProviderError("failed", "gemini_model_name_invalid")
        messages = text_messages(context, prompt)
        return {
            "systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
            "contents": [{"role": "user", "parts": [{"text": messages[1]["content"]}]}],
            "generationConfig": {"maxOutputTokens": module.limits.max_output_tokens},
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
            or module.endpoint != GEMINI_MODELS_ENDPOINT
            or module.model is None
            or not secret
        ):
            raise ProviderError("failed", "gemini_activation_unresolved")
        payload = self.prepare(module, context, prompt)
        reply = transport.post(
            f"{module.endpoint}/{module.model}:generateContent",
            payload,
            {"Content-Type": "application/json", "x-goog-api-key": secret},
            module.limits,
            cancel,
        )
        usage = reply.get("usageMetadata")
        if not isinstance(usage, dict):
            raise ProviderError("unknown", "invalid_gemini_result")
        incoming = count(usage.get("promptTokenCount"))
        answer, thoughts, tool_prompt = (
            count(usage.get(field, OMITTED_ZERO))
            for field in ("candidatesTokenCount", "thoughtsTokenCount", "toolUsePromptTokenCount")
        )
        if count(usage.get("totalTokenCount")) != incoming + answer + thoughts + tool_prompt:
            raise ProviderError("unknown", "inconsistent_gemini_usage")
        if tool_prompt:
            raise ProviderError("unknown", "unexpected_gemini_tool_usage")
        # Thinking tokens are billed as output: the quote and the limit check count both.
        outgoing = answer + thoughts
        request_id = reply.get("responseId")
        if not isinstance(request_id, str) or not request_id:
            raise ProviderError("unknown", "missing_gemini_request_id")
        feedback = reply.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            return Result("refused", "", incoming, outgoing, request_id)
        candidates = reply.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
            raise ProviderError("unknown", "invalid_gemini_candidates")
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        # a blocked candidate omits its content and an empty part list is omitted too (proto3 JSON)
        content = candidate.get("content", {})
        parts = content.get("parts", []) if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise ProviderError("unknown", "invalid_gemini_content")
        texts: list[str] = []
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                raise ProviderError("unknown", "unsupported_gemini_content")
            if part.get("thought") is not True:
                texts.append(part["text"])
        text = "\n".join(texts)
        if finish in REFUSED_FINISH:
            status = "refused"
        elif finish in INCOMPLETE_FINISH:
            status = "incomplete"
        elif finish in COMPLETED_FINISH:
            if not text.strip():
                raise ProviderError("unknown", "missing_gemini_text")
            status = "completed"
        else:
            raise ProviderError("unknown", "unsupported_gemini_finish")
        return Result(status, text, incoming, outgoing, request_id)
