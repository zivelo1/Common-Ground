"""Contract tests using synthetic board and provider transports exclusively."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from pathlib import Path
from typing import Any, Self
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_support import temporary_directory

from adapter_runner import (
    AdapterError,
    BoardClient,
    HTTPTransport,
    Journal,
    NoRedirect,
    Runner,
    load_provider_config,
)
from providers.base import ProviderError


class Board:
    def __init__(self, actor: str = "agent") -> None:
        self.actor = actor
        self.channels = {
            "shared": {
                "id": "shared",
                "kind": "shared",
                "members": ["human", "agent", "other"],
            },
            "private": {
                "id": "private",
                "kind": "private",
                "members": ["human", "agent"],
            },
        }
        self.calls: list[Any] = []
        self.messages: dict[str, Any] = {}
        self.lost_ack = self.changed = self.bad_scope = False
        self.lock = threading.Lock()

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self.lock:
            self.calls.append((method, path, copy.deepcopy(body)))
            if path == "/api/state":
                return {
                    "me": {"id": self.actor, "kind": "agent"},
                    "channels": list(self.channels.values()),
                }
            if path.startswith("/api/brief?"):
                channel = parse_qs(urlsplit(path).query)["channel_id"][0]
                if (
                    channel not in self.channels
                    or self.actor not in self.channels[channel]["members"]
                ):
                    raise RuntimeError("Forbidden fixture channel")
                return {
                    "workspace": {"title": "Synthetic"},
                    "me": {"id": self.actor, "kind": "agent"},
                    "scope": {
                        "channel_ids": ["private" if self.bad_scope else channel]
                    },
                    "knowledge": [
                        {
                            "id": "fact",
                            "channel_id": channel,
                            "body": "Changed" if self.changed else "Synthetic fact",
                            "version": 2 if self.changed else 1,
                        }
                    ],
                    "recent_messages": [],
                    "sources": [],
                    "source_context": [],
                    "source_scan": {
                        "checked_at": str(time.time()),
                        "age_seconds": 0,
                        "issues": [],
                    },
                    "generated_at": str(time.time()),
                    "truncation": {},
                }
            if path == "/api/personal/enqueue":
                assert body is not None
                old = self.messages.get(body["client_id"])
                if old and (
                    old["body"] != body["body"]
                    or old["channel_id"] != body["channel_id"]
                ):
                    raise RuntimeError("Conflicting immutable fixture message")
                self.messages.setdefault(
                    body["client_id"],
                    {
                        **body,
                        "id": len(self.messages) + 1,
                        "sent": False,
                        "author_id": self.actor,
                    },
                )
                return {"delivered": old if old and old["sent"] else None}
            if path == "/api/personal/send":
                assert body is not None
                message = self.messages[body["client_id"]]
                message["sent"] = True
                if self.lost_ack:
                    self.lost_ack = False
                    raise RuntimeError("Lost synthetic acknowledgement")
                return dict(message)
            raise AssertionError("Unexpected synthetic API")


# The Chat Completions providers: registry name, the one reviewed endpoint, the output-limit field.
CHAT_PROVIDERS = (
    ("xai_chat", "https://api.x.ai/v1/chat/completions", "max_tokens"),
    ("openrouter_chat", "https://openrouter.ai/api/v1/chat/completions", "max_completion_tokens"),
    ("opencode_zen_chat", "https://opencode.ai/zen/v1/chat/completions", "max_tokens"),
)


class Transport:
    def __init__(self, provider: str = "openai", mode: str = "complete") -> None:
        self.provider, self.mode = provider, mode
        self.calls: list[Any] = []
        self.entered, self.release = threading.Event(), threading.Event()

    def post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        limits: Any,
        cancel: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "endpoint": endpoint,
                "payload": copy.deepcopy(payload),
                "headers": dict(headers),
            }
        )
        self.entered.set()
        if self.mode == "wait":
            while not self.release.wait(0.005):
                cancel.check()
        if self.mode == "timeout":
            raise TimeoutError("Sensitive synthetic transport detail")
        if self.mode == "malformed":
            return {"status": "completed", "output": [], "usage": {}}
        if self.provider == "anthropic":
            stop = {"incomplete": "max_tokens", "refused": "refusal"}.get(self.mode, "end_turn")
            if self.mode == "malformed":
                return {"type": "message", "role": "assistant", "content": [{"type": "tool_use"}],
                        "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 3}}
            return {"id": "msg_synthetic", "type": "message", "role": "assistant",
                    "content": [{"type": "text", "text": "Synthetic Claude answer"}], "stop_reason": stop,
                    "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 3,
                                                     "cache_creation_input_tokens": 0,
                                                     "cache_read_input_tokens": 0}}
        if self.provider == "gemini":
            finish = {"incomplete": "MAX_TOKENS", "refused": "SAFETY"}.get(self.mode, "STOP")
            # proto3 JSON omits zero counts: no toolUsePromptTokenCount here
            usage = {"promptTokenCount": 5, "candidatesTokenCount": 3, "thoughtsTokenCount": 2,
                     "totalTokenCount": 10 if self.mode != "malformed" else 11}
            return {"responseId": "synthetic-gemini", "usageMetadata": usage,
                    "candidates": [{"finishReason": finish, "content": {
                        "role": "model", "parts": [{"text": "Synthetic Gemini answer"}]}}]}
        if self.provider == "chat":
            finish = {"incomplete": "length", "refused": "content_filter"}.get(self.mode, "stop")
            chat_usage: dict[str, Any] = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
            if self.mode == "reasoning_separate":   # xAI's documented example: reasoning counted beside the completion
                chat_usage.update(total_tokens=10, completion_tokens_details={"reasoning_tokens": 2})
            if self.mode == "reasoning_included":   # OpenAI's rule: reasoning counted inside completion_tokens
                chat_usage.update(completion_tokens=5, total_tokens=10, completion_tokens_details={"reasoning_tokens": 2})
            if self.mode == "bad_total":
                chat_usage["total_tokens"] = 12
            message: dict[str, Any] = {"role": "assistant", "content": "Synthetic chat answer"}
            if self.mode == "tool_call":   # a tool call is never a draft, even beside text and a "stop" finish
                message["tool_calls"] = [{"id": "call_synthetic", "type": "function"}]
            return {"id": "chatcmpl-synthetic", "object": "chat.completion", "model": "synthetic-model",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": chat_usage}
        if self.provider == "ollama":
            return {
                "message": {"role": "assistant", "content": "Synthetic local answer"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 5,
                "eval_count": 3,
            }
        content = (
            [{"type": "refusal", "refusal": "Synthetic refusal"}]
            if self.mode == "refused"
            else [{"type": "output_text", "text": "Synthetic answer"}]
        )
        return {
            "id": "synthetic-response",
            "status": "incomplete" if self.mode == "incomplete" else "completed",
            "output": [
                {"type": "reasoning"},
                {"type": "message", "role": "assistant", "content": content},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        }


class ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = temporary_directory(self, 
            prefix="common-ground-providers-"
        )
        self.root = Path(self.temp.name)
        self.path = self.root / "providers.json"
        self.raw: dict[str, Any] = {
            "schema_version": 1,
            "workspace_config": "workspace.json",
            "instance_limits": {
                "max_concurrent": 2,
                "daily_budget_usd": "1",
                "pricing_max_age_days": 30,
            },
            "modules": [
                {
                    "id": "remote",
                    "provider": "openai_responses",
                    "participant_id": "agent",
                    "enabled": True,
                    "model": "synthetic-model",
                    "endpoint": "https://api.openai.com/v1/responses",
                    "allowed_channel_ids": ["shared"],
                    "credential_env": "SYNTHETIC_TEST_KEY",
                    "local_only_verified": False,
                    "local_only_evidence": None,
                    "limits": {
                        "max_context_bytes": 12000,
                        "max_response_bytes": 12000,
                        "max_output_tokens": 50,
                        "timeout_seconds": 1,
                        "max_concurrent": 1,
                        "max_run_usd": "0.1",
                    },
                    "pricing": {
                        "input_usd_per_million": "1",
                        "output_usd_per_million": "2",
                        "max_input_tokens": 100,
                        "checked_at": time.strftime("%Y-%m-%d", time.gmtime()),
                        "evidence": "https://developers.openai.com/api/docs/pricing",
                    },
                }
            ],
        }
        self.board, self.transport = Board(), Transport()
        self.write()
        self.journal = Journal(self.root / "state")
        self.runner = self.make()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self) -> None:
        self.path.write_text(json.dumps(self.raw))

    def make(
        self, board: Board | None = None, transport: Transport | None = None
    ) -> Runner:
        return Runner(
            load_provider_config(self.path),
            self.journal,
            board or self.board,
            transport or self.transport,
            lambda name: "synthetic-only-secret",
        )

    def prepare(self, channel: str = "shared") -> dict[str, Any]:
        return self.runner.prepare("remote", channel, "Review synthetic evidence")

    def run_job(self, record: dict[str, Any]) -> dict[str, Any]:
        return self.runner.run("remote", record["run_id"], record["prepared_sha256"])

    def test_actor_channel_and_backend_scope_fail_closed(self) -> None:
        with self.assertRaises(AdapterError):
            self.prepare("private")
        self.board.actor = "human"
        with self.assertRaises(AdapterError):
            self.prepare()
        self.board.actor, self.board.bad_scope = "agent", True
        with self.assertRaises(AdapterError):
            self.prepare()
        self.assertEqual(self.transport.calls, [])

    def test_prepare_never_reads_key_or_calls_provider_and_checks_hash(self) -> None:
        runner = Runner(
            load_provider_config(self.path),
            self.journal,
            self.board,
            self.transport,
            lambda name: self.fail("Key read during prepare"),
        )
        record = runner.prepare("remote", "shared", "Synthetic")
        with self.assertRaises(AdapterError):
            runner.run("remote", record["run_id"], "0" * 64)
        self.assertEqual(self.transport.calls, [])

    def test_context_and_config_change_requires_new_preparation(self) -> None:
        record = self.prepare()
        self.board.changed = True
        with self.assertRaises(AdapterError):
            self.run_job(record)
        self.board.changed = False
        self.raw["modules"][0]["limits"]["max_output_tokens"] = 40
        self.write()
        with self.assertRaises(AdapterError):
            self.make().run("remote", record["run_id"], record["prepared_sha256"])
        self.assertEqual(self.transport.calls, [])

    def test_disabled_unresolved_settings_fail_explicitly(self) -> None:
        for key, value in (
            ("enabled", False),
            ("model", None),
            ("pricing", None),
            ("credential_env", None),
        ):
            raw = copy.deepcopy(self.raw)
            raw["modules"][0][key] = value
            self.path.write_text(json.dumps(raw))
            with self.subTest(key=key), self.assertRaises(AdapterError):
                self.make().prepare("remote", "shared", "Synthetic")
        self.raw["instance_limits"]["daily_budget_usd"] = "0"
        self.write()
        with self.assertRaises(AdapterError):
            self.make().prepare("remote", "shared", "Synthetic")

    def test_strict_configuration_and_endpoint_validation(self) -> None:
        for endpoint in (
            "https://example.invalid/v1/responses",
            "https://api.openai.com.evil.invalid/v1/responses",
            "https://api.openai.com/v1/responses?x=1",
        ):
            self.raw["modules"][0]["endpoint"] = endpoint
            self.write()
            with self.assertRaises(AdapterError):
                load_provider_config(self.path)
        self.raw["modules"][0]["endpoint"] = "https://api.openai.com/v1/responses"
        self.raw["modules"][0]["auto_retry"] = True
        self.write()
        with self.assertRaises(AdapterError):
            load_provider_config(self.path)

    def test_openai_typed_output_usage_and_no_tools_or_cloud_state(self) -> None:
        record = self.prepare()
        result = self.run_job(record)
        self.assertEqual(
            (result["status"], result["result"]["text"]),
            ("completed", "Synthetic answer"),
        )
        payload = self.transport.calls[0]["payload"]
        self.assertFalse(payload["store"])
        self.assertNotIn("tools", payload)
        self.assertNotIn("previous_response_id", payload)
        self.assertEqual(payload["max_output_tokens"], 50)
        with self.assertRaises(AdapterError):
            self.run_job(record)
        self.assertEqual(len(self.transport.calls), 1)

    def test_unknown_timeout_durable_without_retry_and_retains_budget(self) -> None:
        self.transport.mode = "timeout"
        record = self.prepare()
        result = self.run_job(record)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(
            self.make().status("remote", record["run_id"])["status"], "unknown"
        )
        with self.assertRaises(AdapterError):
            self.run_job(record)
        self.assertEqual(
            (result["reserved_microusd"], result["charged_microusd"]), (200, None)
        )
        self.assertNotIn("Sensitive", json.dumps(result))
        self.assertEqual(len(self.transport.calls), 1)

    def test_partial_refused_and_invalid_output_are_not_success(self) -> None:
        for mode, expected in (
            ("refused", "refused"),
            ("incomplete", "incomplete"),
            ("malformed", "unknown"),
        ):
            self.transport.mode = mode
            record = self.prepare()
            self.assertEqual(self.run_job(record)["status"], expected)
            with self.assertRaises(AdapterError):
                self.runner.publish(
                    "remote", record["run_id"], record["prepared_sha256"]
                )
        self.assertEqual(self.board.messages, {})

    def test_atomic_global_budget_across_instances(self) -> None:
        self.raw["instance_limits"]["daily_budget_usd"] = "0.0002"
        self.raw["modules"][0]["limits"]["max_concurrent"] = 2
        self.write()
        self.runner, self.transport.mode = self.make(), "wait"
        first, second = self.prepare(), self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            task = pool.submit(self.run_job, first)
            self.assertTrue(self.transport.entered.wait(1))
            with self.assertRaises(AdapterError):
                self.make().run("remote", second["run_id"], second["prepared_sha256"])
            self.transport.release.set()
            self.assertEqual(task.result()["status"], "completed")
        self.assertEqual(len(self.transport.calls), 1)

    def test_lost_publication_ack_reuses_original_message_without_generation(
        self,
    ) -> None:
        record = self.prepare()
        self.run_job(record)
        self.board.lost_ack = True
        with self.assertRaises(AdapterError):
            self.runner.publish("remote", record["run_id"], record["prepared_sha256"])
        result = self.make().publish(
            "remote", record["run_id"], record["prepared_sha256"]
        )
        self.assertEqual(result["publication_status"], "published")
        self.assertEqual(list(self.board.messages), ["adapter:" + record["run_id"]])
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(
            self.runner.publish("remote", record["run_id"], record["prepared_sha256"])[
                "message_id"
            ],
            result["message_id"],
        )

    def test_private_ack_and_access_revocation_are_owner_scoped(self) -> None:
        self.raw["modules"][0]["allowed_channel_ids"].append("private")
        self.write()
        self.runner = self.make()
        record = self.prepare("private")
        self.run_job(record)
        with self.assertRaises(AdapterError):
            self.make(Board("other")).status("remote", record["run_id"])
        self.board.channels.pop("private")
        with self.assertRaises(AdapterError):
            self.runner.status("remote", record["run_id"])
        with self.assertRaises(AdapterError):
            self.runner.publish("remote", record["run_id"], record["prepared_sha256"])

    def test_manual_awaits_explicit_result_without_provider_request(self) -> None:
        self.raw["modules"][0].update(
            provider="manual",
            model=None,
            endpoint=None,
            pricing=None,
            credential_env=None,
        )
        self.write()
        self.runner = self.make()
        record = self.prepare()
        self.assertEqual(self.run_job(record)["status"], "awaiting_manual")
        result = self.runner.import_result(
            "remote",
            record["run_id"],
            record["prepared_sha256"],
            "Explicit synthetic manual answer",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.transport.calls, [])
        self.runner.publish("remote", record["run_id"], record["prepared_sha256"])
        with self.assertRaises(AdapterError):
            self.runner.import_result(
                "remote", record["run_id"], record["prepared_sha256"], "Overwrite"
            )

    def test_ollama_requires_local_only_attestation_and_explicit_limits(self) -> None:
        module = self.raw["modules"][0]
        module.update(
            provider="ollama",
            endpoint="http://127.0.0.1:11434/api/chat",
            pricing=None,
            credential_env=None,
        )
        self.write()
        with self.assertRaises(AdapterError):
            self.make().prepare("remote", "shared", "Synthetic")
        module.update(
            local_only_verified=True,
            local_only_evidence="Synthetic operator attestation",
        )
        self.write()
        transport = Transport("ollama")
        self.runner = self.make(transport=transport)
        record = self.prepare()
        self.assertEqual(self.run_job(record)["reserved_microusd"], 0)
        request = transport.calls[0]
        self.assertFalse(request["payload"]["stream"])
        self.assertEqual(request["payload"]["options"]["num_predict"], 50)
        self.assertNotIn("Authorization", request["headers"])

    def use_remote(self, provider: str, endpoint: str, model: str = "synthetic-model") -> Transport:
        self.raw["modules"][0].update(provider=provider, endpoint=endpoint, model=model)
        self.write()
        transport = Transport({"anthropic_messages": "anthropic", "gemini_generate": "gemini", "xai_chat": "chat",
                               "openrouter_chat": "chat", "opencode_zen_chat": "chat"}[provider])
        self.runner = self.make(transport=transport)
        return transport

    def test_anthropic_messages_wire_usage_and_stop_reasons(self) -> None:
        transport = self.use_remote("anthropic_messages", "https://api.anthropic.com/v1/messages")
        row = self.run_job(self.prepare())
        [call] = transport.calls
        self.assertEqual(call["endpoint"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(call["headers"], {"Content-Type": "application/json", "x-api-key": "synthetic-only-secret",
                                           "anthropic-version": "2023-06-01"})
        payload = call["payload"]
        self.assertEqual(sorted(payload), ["max_tokens", "messages", "model", "system"])
        self.assertEqual((payload["model"], payload["max_tokens"]), ("synthetic-model", 50))
        self.assertEqual([m["role"] for m in payload["messages"]], ["user"])
        self.assertIn("Explicit request:\nReview synthetic evidence", payload["messages"][0]["content"])
        self.assertIn("Return a text draft only", payload["system"])
        self.assertEqual((row["status"], row["result"]["text"], row["result"]["request_id"]),
                         ("completed", "Synthetic Claude answer", "msg_synthetic"))
        self.assertEqual(row["charged_microusd"], 1 * 5 + 2 * 3)
        for mode, status in (("incomplete", "incomplete"), ("refused", "refused"), ("malformed", "unknown")):
            with self.subTest(mode=mode):
                transport.mode = mode
                self.assertEqual(self.run_job(self.prepare())["status"], status)

    def test_anthropic_cache_usage_we_never_asked_for_is_not_priced_as_success(self) -> None:
        transport = self.use_remote("anthropic_messages", "https://api.anthropic.com/v1/messages")
        original = transport.post

        def cached(*args: Any, **kwargs: Any) -> dict[str, Any]:
            reply = original(*args, **kwargs)
            reply["usage"]["cache_creation_input_tokens"] = 7
            return reply

        transport.post = cached  # type: ignore[method-assign]
        row = self.run_job(self.prepare())
        self.assertEqual((row["status"], row["result"]["warning"]), ("unknown", "unexpected_anthropic_cache_usage"))

    def test_gemini_wire_counts_thinking_as_output_and_checks_the_total(self) -> None:
        transport = self.use_remote("gemini_generate", "https://generativelanguage.googleapis.com/v1beta/models",
                                    "gemini-synthetic-flash")
        row = self.run_job(self.prepare())
        [call] = transport.calls
        self.assertEqual(call["endpoint"], "https://generativelanguage.googleapis.com/v1beta/models/"
                                           "gemini-synthetic-flash:generateContent")
        self.assertEqual(call["headers"], {"Content-Type": "application/json", "x-goog-api-key": "synthetic-only-secret"})
        payload = call["payload"]
        self.assertEqual(sorted(payload), ["contents", "generationConfig", "systemInstruction"])
        self.assertEqual(payload["generationConfig"], {"maxOutputTokens": 50})
        self.assertIn("Return a text draft only", payload["systemInstruction"]["parts"][0]["text"])
        self.assertEqual([c["role"] for c in payload["contents"]], ["user"])
        # 3 answer tokens + 2 thinking tokens are both billed output
        self.assertEqual((row["status"], row["result"]["input_tokens"], row["result"]["output_tokens"]),
                         ("completed", 5, 5))
        self.assertEqual(row["charged_microusd"], 1 * 5 + 2 * 5)
        for mode, status in (("incomplete", "incomplete"), ("refused", "refused"), ("malformed", "unknown")):
            with self.subTest(mode=mode):
                transport.mode = mode
                self.assertEqual(self.run_job(self.prepare())["status"], status)

    def test_chat_completions_wire_for_grok_openrouter_and_opencode(self) -> None:
        self.raw["instance_limits"]["daily_budget_usd"] = "10"   # unknown results keep their reservation
        for provider, endpoint, limit_field in CHAT_PROVIDERS:
            with self.subTest(provider=provider):
                transport = self.use_remote(provider, endpoint)
                row = self.run_job(self.prepare())
                [call] = transport.calls
                self.assertEqual(call["endpoint"], endpoint)
                self.assertEqual(call["headers"], {"Content-Type": "application/json",
                                                   "Authorization": "Bearer synthetic-only-secret"})
                payload = call["payload"]
                self.assertEqual(sorted(payload), sorted(["messages", "model", "stream", limit_field]))
                self.assertEqual((payload["model"], payload[limit_field], payload["stream"]), ("synthetic-model", 50, False))
                self.assertEqual([m["role"] for m in payload["messages"]], ["system", "user"])
                self.assertIn("Return a text draft only", payload["messages"][0]["content"])
                self.assertIn("Explicit request:\nReview synthetic evidence", payload["messages"][1]["content"])
                self.assertEqual((row["status"], row["result"]["text"], row["result"]["request_id"]),
                                 ("completed", "Synthetic chat answer", "chatcmpl-synthetic"))
                self.assertEqual(row["charged_microusd"], 1 * 5 + 2 * 3)
                for mode, status in (("incomplete", "incomplete"), ("refused", "refused"), ("malformed", "unknown"),
                                     ("tool_call", "unknown"), ("bad_total", "unknown")):
                    with self.subTest(provider=provider, mode=mode):
                        transport.mode = mode
                        self.assertEqual(self.run_job(self.prepare())["status"], status)

    def test_chat_completions_bill_reasoning_inside_or_beside_the_completion(self) -> None:
        # Billed output = total - prompt, and it must equal the completion, or the completion plus its reasoning
        # tokens; any other total is unknown, never a priced success.
        transport = self.use_remote("xai_chat", "https://api.x.ai/v1/chat/completions")
        for mode in ("reasoning_separate", "reasoning_included"):
            with self.subTest(mode=mode):
                transport.mode = mode
                row = self.run_job(self.prepare())
                self.assertEqual((row["status"], row["result"]["output_tokens"]), ("completed", 5))
                self.assertEqual(row["charged_microusd"], 1 * 5 + 2 * 5)

    def test_gemini_model_must_be_one_safe_path_segment(self) -> None:
        for model in ("../other", "gemini:flash", "gemini/flash", "Gemini Flash"):
            with self.subTest(model=model):
                self.use_remote("gemini_generate", "https://generativelanguage.googleapis.com/v1beta/models", model)
                with self.assertRaises(AdapterError):
                    self.prepare()

    def test_each_remote_provider_accepts_only_its_reviewed_origin(self) -> None:
        for provider, endpoint in (
            ("anthropic_messages", "https://api.anthropic.com.evil.invalid/v1/messages"),
            ("anthropic_messages", "https://api.openai.com/v1/responses"),
            ("gemini_generate", "https://generativelanguage.googleapis.com/v1/models"),
            ("gemini_generate", "https://api.anthropic.com/v1/messages"),
            ("xai_chat", "https://api.x.ai.evil.invalid/v1/chat/completions"),
            ("openrouter_chat", "https://api.x.ai/v1/chat/completions"),
            ("opencode_zen_chat", "https://opencode.ai/zen/v1/messages"),
        ):
            with self.subTest(provider=provider, endpoint=endpoint):
                self.raw["modules"][0].update(provider=provider, endpoint=endpoint)
                self.write()
                with self.assertRaises(AdapterError):
                    load_provider_config(self.path)
        # positive control: each reviewed origin itself loads
        for provider, endpoint in (("anthropic_messages", "https://api.anthropic.com/v1/messages"),
                                   ("gemini_generate", "https://generativelanguage.googleapis.com/v1beta/models"),
                                   *((name, url) for name, url, _ in CHAT_PROVIDERS)):
            self.raw["modules"][0].update(provider=provider, endpoint=endpoint)
            self.write()
            self.assertEqual(load_provider_config(self.path).modules["remote"].endpoint, endpoint)

    def test_a_missing_key_stops_every_remote_provider_before_dispatch(self) -> None:
        for provider, endpoint in (("anthropic_messages", "https://api.anthropic.com/v1/messages"),
                                   ("gemini_generate", "https://generativelanguage.googleapis.com/v1beta/models"),
                                   *((name, url) for name, url, _ in CHAT_PROVIDERS)):
            with self.subTest(provider=provider):
                transport = self.use_remote(provider, endpoint)
                record = self.prepare()
                runner = Runner(load_provider_config(self.path), self.journal, self.board, transport, lambda name: "")
                with self.assertRaises(AdapterError):
                    runner.run("remote", record["run_id"], record["prepared_sha256"])
                self.assertEqual(transport.calls, [])

    def test_ollama_turns_thinking_off_so_the_output_budget_is_the_draft(self) -> None:
        # Measured 12/10 on the real local gemma4:12b-it-qat: with thinking on (its default), all 300 allowed output
        # tokens went to hidden reasoning and the draft came back empty ("incomplete").
        self.raw["modules"][0].update(
            provider="ollama",
            endpoint="http://127.0.0.1:11434/api/chat",
            pricing=None,
            credential_env=None,
            local_only_verified=True,
            local_only_evidence="Synthetic operator attestation",
        )
        self.write()
        transport = Transport("ollama")
        self.runner = self.make(transport=transport)
        self.run_job(self.prepare())
        self.assertIs(transport.calls[0]["payload"]["think"], False)

    def test_a_provider_refusal_is_a_definite_failure_and_the_rest_stays_unknown(
        self,
    ) -> None:
        # Measured 12/10: the local Ollama answered 400 "does not support chat" and the run showed only "unknown /
        # provider_transport_unconfirmed". A 4xx answer is a refusal before any generation; 408, 5xx and a lost
        # connection may have started work, so they stay unknown.
        module = load_provider_config(self.path).modules["remote"]
        assert module.limits is not None

        class Cancel:
            def check(self) -> None:
                return None

        class Refusing:
            def __init__(self, failure: Exception) -> None:
                self.failure = failure

            def open(self, *args: Any, **kwargs: Any) -> None:
                raise self.failure

        def http(code: int) -> HTTPError:
            return HTTPError("https://api.openai.com/v1/responses", code, "synthetic", Message(), None)

        cases: list[tuple[Exception, str, str]] = [
            (http(code), "failed", f"provider_http_{code}") for code in (400, 401, 403, 404, 422, 429)
        ]
        cases += [(http(code), "unknown", "provider_transport_unconfirmed") for code in (408, 500, 502, 503)]
        cases += [(URLError("synthetic"), "unknown", "provider_transport_unconfirmed"),
                  (TimeoutError(), "unknown", "provider_transport_unconfirmed")]
        for failure, status, category in cases:
            with (
                self.subTest(failure=repr(failure)),
                patch("adapter_runner.build_opener", return_value=Refusing(failure)),
                self.assertRaises(ProviderError) as caught,
            ):
                HTTPTransport().post(
                    "https://api.openai.com/v1/responses", {}, {}, module.limits, Cancel()
                )
            self.assertEqual((caught.exception.status, str(caught.exception)), (status, category))

    def test_stop_blocks_admission_and_pending_request_becomes_unknown(self) -> None:
        self.transport.mode = "wait"
        record = self.prepare()
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(self.run_job, record)
            self.assertTrue(self.transport.entered.wait(1))
            self.journal.begin_stop()
            with self.assertRaises(AdapterError):
                self.prepare()
            self.assertTrue(self.journal.drain(1))
            self.assertEqual(task.result()["status"], "unknown")
        self.journal.resume()
        self.assertEqual(self.prepare()["status"], "prepared")

    def test_private_journal_permissions_integrity_and_no_credentials(self) -> None:
        record = self.prepare()
        self.run_job(record)
        self.assertEqual(os.stat(self.journal.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.journal.directory).st_mode & 0o777, 0o700)
        self.assertNotIn(b"synthetic-only-secret", self.journal.path.read_bytes())
        with sqlite3.connect(self.journal.path) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_symlinked_journal_rejected(self) -> None:
        state = self.root / "other-state"
        state.mkdir()
        (state / "adapter_runs").symlink_to(
            self.journal.directory, target_is_directory=True
        )
        with self.assertRaises(AdapterError):
            Journal(state)

    def test_missing_key_fails_before_budget_reservation_or_dispatch(self) -> None:
        record = self.prepare()
        runner = Runner(
            load_provider_config(self.path),
            self.journal,
            self.board,
            self.transport,
            lambda name: "",
        )
        with self.assertRaises(AdapterError):
            runner.run("remote", record["run_id"], record["prepared_sha256"])
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(
            runner.status("remote", record["run_id"])["reserved_microusd"], 0
        )

    def test_exact_provider_preview_matches_wire_and_unknown_fields_do_not_export(
        self,
    ) -> None:
        request = self.board.request

        def altered(
            method: str, path: str, body: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            reply = request(method, path, body)
            if path.startswith("/api/brief?"):
                reply["private_unscoped_extra"] = "Must never leave this response"
            return reply

        with patch.object(self.board, "request", side_effect=altered):
            record = self.prepare()
            self.run_job(record)
        self.assertEqual(
            record["prepared"]["request_payload"], self.transport.calls[0]["payload"]
        )
        self.assertNotIn("Must never leave", json.dumps(self.transport.calls))

    def test_uncited_source_context_and_foreign_scan_issue_are_rejected(self) -> None:
        request = self.board.request
        for key, injected in (
            (
                "source_context",
                [{"source_id": "private-other", "headings": ["Private"]}],
            ),
            (
                "source_scan",
                {"issues": [{"source_id": "private-other", "error": "Private"}]},
            ),
            ("me", []),
        ):

            def altered(
                method: str,
                path: str,
                body: dict[str, Any] | None = None,
                key: str = key,
                injected: Any = injected,
            ) -> dict[str, Any]:
                reply = request(method, path, body)
                if path.startswith("/api/brief?"):
                    reply[key] = injected
                return reply

            with (
                self.subTest(key=key),
                patch.object(self.board, "request", side_effect=altered),
                self.assertRaises(AdapterError),
            ):
                self.prepare()
        self.assertEqual(self.transport.calls, [])

    def test_malformed_config_types_are_structured_errors(self) -> None:
        for key, value in (
            ("provider", []),
            ("allowed_channel_ids", [{}]),
            ("enabled", 1),
            ("model", {}),
        ):
            raw = copy.deepcopy(self.raw)
            raw["modules"][0][key] = value
            self.path.write_text(json.dumps(raw))
            with self.subTest(key=key), self.assertRaises(AdapterError):
                load_provider_config(self.path)

    def test_stale_pricing_and_provider_usage_above_bound_refuse_success(self) -> None:
        self.raw["modules"][0]["pricing"]["checked_at"] = "2000-01-01"
        self.write()
        with self.assertRaises(AdapterError):
            self.make().prepare("remote", "shared", "Synthetic")
        self.raw["modules"][0]["pricing"]["checked_at"] = time.strftime(
            "%Y-%m-%d", time.gmtime()
        )
        self.write()
        record = self.prepare()
        response = self.transport.post

        def exceeded(
            endpoint: str,
            payload: dict[str, Any],
            headers: dict[str, str],
            limits: Any,
            cancel: Any,
        ) -> dict[str, Any]:
            reply = response(endpoint, payload, headers, limits, cancel)
            reply["usage"] = {
                "input_tokens": 101,
                "output_tokens": 3,
                "total_tokens": 104,
            }
            return reply

        with patch.object(self.transport, "post", side_effect=exceeded):
            result = self.run_job(record)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["charged_microusd"])

    def test_same_run_concurrent_generation_is_once_only(self) -> None:
        self.transport.mode = "wait"
        record = self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_job, record)
            self.assertTrue(self.transport.entered.wait(1))
            with self.assertRaises(AdapterError):
                self.make().run("remote", record["run_id"], record["prepared_sha256"])
            self.transport.release.set()
            self.assertEqual(first.result()["status"], "completed")
        self.assertEqual(len(self.transport.calls), 1)

    def test_stop_drain_timeout_reports_incomplete_without_fake_success(self) -> None:
        record = self.prepare()
        original = self.transport.post

        def ignores_cancel(
            endpoint: str,
            payload: dict[str, Any],
            headers: dict[str, str],
            limits: Any,
            cancel: Any,
        ) -> dict[str, Any]:
            self.transport.entered.set()
            self.transport.release.wait(1)
            return original(endpoint, payload, headers, limits, cancel)

        with (
            patch.object(self.transport, "post", side_effect=ignores_cancel),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            task = pool.submit(self.run_job, record)
            self.assertTrue(self.transport.entered.wait(1))
            self.journal.begin_stop()
            self.assertFalse(self.journal.drain(0.01))
            with self.assertRaises(AdapterError):
                self.journal.resume()
            self.transport.release.set()
            self.assertEqual(task.result()["status"], "unknown")
        self.assertTrue(self.journal.drain(1))

    def test_process_interruption_recovers_unknown_without_generating_again(
        self,
    ) -> None:
        record = self.prepare()
        marker = self.root / "synthetic-child-entered"
        code = """import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from adapter_runner import Journal,load_provider_config
j=Journal(Path(sys.argv[2])); cfg=load_provider_config(Path(sys.argv[3])); m=cfg.modules['remote']
with j.operation(sys.argv[4]):
 j.claim(sys.argv[4],m,cfg,200)
 Path(sys.argv[5]).write_text('synthetic active claim')
 time.sleep(30)
"""
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                code,
                str(Path(__file__).resolve().parents[1]),
                str(self.root / "state"),
                str(self.path),
                record["run_id"],
                str(marker),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline:
                if child.poll() is not None:
                    self.fail("Synthetic child exited before its claim")
                time.sleep(0.005)
            self.assertTrue(marker.exists())
            child.terminate()
            child.wait(3)
            reopened = self.make()
            result = reopened.status("remote", record["run_id"])
            self.assertEqual(
                (
                    result["status"],
                    result["reserved_microusd"],
                    result["charged_microusd"],
                ),
                ("unknown", 200, None),
            )
            with self.assertRaises(AdapterError):
                reopened.run("remote", record["run_id"], record["prepared_sha256"])
            self.assertEqual(self.transport.calls, [])
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(3)
            if child.stderr is not None:
                self.assertEqual(child.stderr.read(), b"")
                child.stderr.close()

    def test_protected_journal_fingerprint_detects_changed_prepared_bytes(self) -> None:
        record = self.prepare()
        with sqlite3.connect(self.journal.path) as db:
            db.execute(
                "UPDATE jobs SET prepared=? WHERE run_id=?", ("{}", record["run_id"])
            )
        with self.assertRaises(AdapterError):
            self.runner.status("remote", record["run_id"])
        self.assertEqual(self.transport.calls, [])

    def test_canonical_client_is_reused_without_fixed_origin_or_direct_database(
        self,
    ) -> None:
        class SyntheticCLI:
            @staticmethod
            def request(
                config_path: Path, actor: str, method: str, path: str, body: Any
            ) -> dict[str, Any]:
                return {"config": str(config_path), "actor": actor, "path": path}

        with patch("adapter_runner.import_module", return_value=SyntheticCLI):
            client = BoardClient(self.root / "workspace.json", "agent")
            self.assertEqual(client.request("GET", "/api/state")["actor"], "agent")
        with (
            patch("adapter_runner.import_module", side_effect=ImportError),
            self.assertRaises(AdapterError),
        ):
            client.request("GET", "/api/state")

    def test_no_redirect_and_bounded_http_parser_use_synthetic_opener_only(
        self,
    ) -> None:
        with self.assertRaises(ProviderError):
            NoRedirect().redirect_request(
                None, None, 302, "", None, "https://example.invalid"
            )
        module = load_provider_config(self.path).modules["remote"]
        assert module.limits is not None

        class Cancel:
            def check(self) -> None:
                return None

        class Reply:
            def __init__(self, raw: bytes) -> None:
                self.raw = raw

            def __enter__(self) -> Self:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read1(self, limit: int) -> bytes:
                result, self.raw = self.raw[:limit], self.raw[limit:]
                return result

        class Opener:
            def __init__(self, raw: bytes) -> None:
                self.raw = raw

            def open(self, *args: Any, **kwargs: Any) -> Reply:
                return Reply(self.raw)

        for payload in (
            b"not-json",
            b"[]",
            b"x" * (module.limits.max_response_bytes + 1),
        ):
            with (
                patch("adapter_runner.build_opener", return_value=Opener(payload)),
                self.assertRaises(ProviderError),
            ):
                HTTPTransport().post(
                    "https://api.openai.com/v1/responses",
                    {},
                    {},
                    module.limits,
                    Cancel(),
                )
        with patch(
            "adapter_runner.build_opener", return_value=Opener(b'{"fixture":true}')
        ):
            self.assertEqual(
                HTTPTransport().post(
                    "https://api.openai.com/v1/responses",
                    {},
                    {},
                    module.limits,
                    Cancel(),
                ),
                {"fixture": True},
            )

    def test_distinct_modules_share_global_budget_but_keep_actor_isolation(
        self,
    ) -> None:
        second = copy.deepcopy(self.raw["modules"][0])
        second.update(id="second", participant_id="other")
        self.raw["modules"].append(second)
        self.raw["instance_limits"]["daily_budget_usd"] = "0.0002"
        self.write()
        self.runner = self.make()
        first = self.prepare()
        other_transport = Transport()
        other = self.make(Board("other"), other_transport)
        second_run = other.prepare("second", "shared", "Other synthetic request")
        self.transport.mode = "wait"
        with ThreadPoolExecutor(max_workers=2) as pool:
            task = pool.submit(self.run_job, first)
            self.assertTrue(self.transport.entered.wait(1))
            with self.assertRaises(AdapterError):
                other.run("second", second_run["run_id"], second_run["prepared_sha256"])
            with self.assertRaises(AdapterError):
                other.status("second", first["run_id"])
            self.transport.release.set()
            self.assertEqual(task.result()["status"], "completed")
        self.assertEqual(other_transport.calls, [])

    def test_disabled_example_fails_before_workspace_or_key_access(self) -> None:
        example = Path(__file__).resolve().parents[1] / "providers.example.json"
        config = load_provider_config(example)
        self.assertTrue(all(not m.enabled for m in config.modules.values()))
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(example.with_name("adapter_runner.py")),
                "--providers",
                str(example),
                "--module",
                "manual",
                "prepare",
                "--channel",
                "shared",
                "--prompt-from-stdin",
            ],
            input="Synthetic",
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("disabled", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_provider_cannot_persist_an_echoed_credential(self) -> None:
        original = self.transport.post

        def echo(
            endpoint: str,
            payload: dict[str, Any],
            headers: dict[str, str],
            limits: Any,
            cancel: Any,
        ) -> dict[str, Any]:
            reply = original(endpoint, payload, headers, limits, cancel)
            reply["output"][1]["content"][0]["text"] = "synthetic-only-secret"
            return reply

        record = self.prepare()
        with patch.object(self.transport, "post", side_effect=echo):
            result = self.run_job(record)
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn(b"synthetic-only-secret", self.journal.path.read_bytes())

    def test_wrong_author_acknowledgement_is_not_accepted(self) -> None:
        record = self.prepare()
        self.run_job(record)
        original = self.board.request

        def wrong_author(
            method: str, path: str, body: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            reply = original(method, path, body)
            if path == "/api/personal/send":
                reply["author_id"] = "other"
            return reply

        with (
            patch.object(self.board, "request", side_effect=wrong_author),
            self.assertRaises(AdapterError),
        ):
            self.runner.publish("remote", record["run_id"], record["prepared_sha256"])
        self.assertEqual(
            self.runner.status("remote", record["run_id"])["publication_status"],
            "unconfirmed",
        )

    def reject_mixed_channel_brief(self, selected: str, foreign: str) -> None:
        self.raw["modules"][0]["allowed_channel_ids"] = ["shared", "private"]
        self.write()
        self.runner = self.make()
        request = self.board.request
        # These are the public brief collections, independently pinned by the API contract.
        collections = (
            "context",
            "knowledge",
            "lessons",
            "disputed",
            "stale",
            "decisions",
            "tasks",
            "sources",
            "recent_messages",
        )
        for collection in collections:

            def mixed(
                method: str,
                path: str,
                body: dict[str, Any] | None = None,
                collection: str = collection,
            ) -> dict[str, Any]:
                reply = request(method, path, body)
                if path.startswith("/api/brief?"):
                    reply.setdefault(collection, []).append(
                        {
                            "id": "foreign-entry",
                            "channel_id": foreign,
                            "body": "SYNTHETIC OTHER CHANNEL",
                            "version": 1,
                        }
                    )
                return reply

            with (
                self.subTest(selected=selected, foreign=foreign, collection=collection),
                patch.object(self.board, "request", side_effect=mixed),
                self.assertRaisesRegex(AdapterError, "selected channel"),
            ):
                self.runner.prepare(
                    "remote", selected, "Synthetic selected-channel request"
                )
        self.assertEqual(self.transport.calls, [])
        with sqlite3.connect(self.journal.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_shared_selection_rejects_allowed_private_entries(self) -> None:
        self.reject_mixed_channel_brief("shared", "private")

    def test_private_selection_rejects_allowed_shared_entries(self) -> None:
        self.reject_mixed_channel_brief("private", "shared")


if __name__ == "__main__":
    unittest.main()
