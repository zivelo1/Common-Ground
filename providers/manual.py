"""Manual work never invokes a model."""

from __future__ import annotations

from typing import Any

from .base import Cancellation, Module, Result, Transport


class Manual:
    def prepare(
        self, module: Module, context: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        return {"context": context, "prompt": prompt, "delivery": "manual_handoff"}

    def generate(
        self,
        module: Module,
        context: dict[str, Any],
        prompt: str,
        transport: Transport,
        cancel: Cancellation,
        secret: str | None,
    ) -> Result:
        cancel.check()
        return Result("awaiting_manual", "", None, None)
