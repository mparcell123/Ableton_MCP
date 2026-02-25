from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from typing import Any, Dict, List


def _load_harness_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "llm_chain_harness.py"
    spec = importlib.util.spec_from_file_location("llm_chain_harness", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestLlmChainHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _load_harness_module()

    def test_alias_canonicalization_for_parameter_updates(self) -> None:
        payload = {
            "steps": [
                {
                    "device_name": "EQ Eight",
                    "parameter_updates": [
                        {
                            "parameter": "1 Frequency A",
                            "target": 100.0,
                            "unit": "hz",
                            "fallback": 0.5,
                        },
                        {
                            "id": 2,
                            "text": "high pass",
                        },
                    ],
                }
            ]
        }

        normalized, changed = self.harness._normalize_tool_arguments("action.build_device_chain", payload)
        self.assertTrue(changed)

        updates = normalized["steps"][0]["parameter_updates"]
        self.assertEqual(updates[0]["param_name"], "1 Frequency A")
        self.assertEqual(updates[0]["target_display_value"], 100.0)
        self.assertEqual(updates[0]["target_unit"], "hz")
        self.assertEqual(updates[0]["fallback_value"], 0.5)
        self.assertEqual(updates[1]["param_index"], 2)
        self.assertEqual(updates[1]["target_display_text"], "high pass")

    def test_run_turn_allows_parameter_mutation_without_prior_inspect(self) -> None:
        responses: List[Dict[str, Any]] = [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "action.build_device_chain",
                                "arguments": {
                                    "steps": [
                                        {
                                            "device_name": "EQ Eight",
                                            "parameter_updates": [{"param_name": "1 Frequency A", "value": 0.2}],
                                        }
                                    ]
                                },
                            }
                        }
                    ]
                }
            },
            {"message": {"content": "done"}},
        ]

        executed: List[str] = []

        def fake_post_ollama_chat(**_: Any) -> Dict[str, Any]:
            return responses.pop(0)

        def fake_execute_tool_call(**kwargs: Any) -> Dict[str, Any]:
            executed.append(kwargs["tool_name"])
            return {"ok": True, "message": "ok"}

        original_post = self.harness._post_ollama_chat
        original_execute = self.harness._execute_tool_call
        self.harness._post_ollama_chat = fake_post_ollama_chat
        self.harness._execute_tool_call = fake_execute_tool_call
        try:
            _ = self.harness._run_turn(
                server=object(),
                model="model",
                ollama_url="http://127.0.0.1:11434",
                think="low",
                timeout_sec=5.0,
                tool_defs=[],
                tool_input_schemas={
                    "action.build_device_chain": {
                        "type": "object",
                        "properties": {
                            "steps": {"type": "array"},
                        },
                        "required": ["steps"],
                        "additionalProperties": False,
                    }
                },
                messages=[{"role": "system", "content": "sys"}],
                user_prompt="Remove lows",
                show_thinking=False,
                auto_approve=True,
            )
        finally:
            self.harness._post_ollama_chat = original_post
            self.harness._execute_tool_call = original_execute

        self.assertEqual(executed, ["action.build_device_chain"])

    def test_run_turn_allows_parameter_mutation_after_successful_inspect(self) -> None:
        responses: List[Dict[str, Any]] = [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "action.inspect_track_chain", "arguments": {"include_parameters": True}}}
                    ]
                }
            },
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "action.update_device_parameters",
                                "arguments": {
                                    "updates": [
                                        {
                                            "device_index": 0,
                                            "parameter_updates": [{"param_name": "Gain", "value": 0.4}],
                                        }
                                    ]
                                },
                            }
                        }
                    ]
                }
            },
            {"message": {"content": "done"}},
        ]

        executed: List[str] = []

        def fake_post_ollama_chat(**_: Any) -> Dict[str, Any]:
            return responses.pop(0)

        def fake_execute_tool_call(**kwargs: Any) -> Dict[str, Any]:
            executed.append(kwargs["tool_name"])
            return {"ok": True, "message": "ok"}

        original_post = self.harness._post_ollama_chat
        original_execute = self.harness._execute_tool_call
        self.harness._post_ollama_chat = fake_post_ollama_chat
        self.harness._execute_tool_call = fake_execute_tool_call
        try:
            _ = self.harness._run_turn(
                server=object(),
                model="model",
                ollama_url="http://127.0.0.1:11434",
                think="low",
                timeout_sec=5.0,
                tool_defs=[],
                tool_input_schemas={
                    "action.inspect_track_chain": {
                        "type": "object",
                        "properties": {"include_parameters": {"type": "boolean"}},
                        "additionalProperties": False,
                    },
                    "action.update_device_parameters": {
                        "type": "object",
                        "properties": {
                            "updates": {"type": "array"},
                        },
                        "required": ["updates"],
                        "additionalProperties": False,
                    },
                },
                messages=[{"role": "system", "content": "sys"}],
                user_prompt="remove lows",
                show_thinking=False,
                auto_approve=True,
            )
        finally:
            self.harness._post_ollama_chat = original_post
            self.harness._execute_tool_call = original_execute

        self.assertEqual(executed, ["action.inspect_track_chain", "action.update_device_parameters"])

    def test_run_turn_validates_against_called_tool_schema(self) -> None:
        responses: List[Dict[str, Any]] = [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "action.inspect_track_chain",
                                "arguments": {"steps": []},
                            }
                        }
                    ]
                }
            },
            {"message": {"content": "done"}},
        ]

        def fake_post_ollama_chat(**_: Any) -> Dict[str, Any]:
            return responses.pop(0)

        original_post = self.harness._post_ollama_chat
        self.harness._post_ollama_chat = fake_post_ollama_chat
        try:
            history = self.harness._run_turn(
                server=object(),
                model="model",
                ollama_url="http://127.0.0.1:11434",
                think="low",
                timeout_sec=5.0,
                tool_defs=[],
                tool_input_schemas={
                    "action.inspect_track_chain": {
                        "type": "object",
                        "properties": {"include_parameters": {"type": "boolean"}},
                        "additionalProperties": False,
                    },
                    "action.build_device_chain": {
                        "type": "object",
                        "properties": {"steps": {"type": "array"}},
                        "required": ["steps"],
                        "additionalProperties": False,
                    },
                },
                messages=[{"role": "system", "content": "sys"}],
                user_prompt="show me chain",
                show_thinking=False,
                auto_approve=True,
            )
        finally:
            self.harness._post_ollama_chat = original_post

        invalid_errors = []
        for msg in history:
            if msg.get("role") != "tool":
                continue
            payload = json.loads(msg.get("content") or "{}")
            if payload.get("error_code") == "INVALID_TOOL_ARGS":
                invalid_errors.append(payload)

        self.assertTrue(invalid_errors)

    def test_run_turn_completes_mutate_summary_with_higher_round_limit(self) -> None:
        responses: List[Dict[str, Any]] = [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "action.build_device_chain",
                                "arguments": {
                                    "steps": [
                                        {
                                            "device_name": "EQ Eight",
                                            "parameter_updates": [{"param_name": "1 Gain A", "value": 0.3}],
                                        }
                                    ]
                                },
                            }
                        }
                    ]
                }
            },
            {"message": {"content": "Chain built successfully."}},
        ]

        executed: List[str] = []

        def fake_post_ollama_chat(**_: Any) -> Dict[str, Any]:
            return responses.pop(0)

        def fake_execute_tool_call(**kwargs: Any) -> Dict[str, Any]:
            executed.append(kwargs["tool_name"])
            return {"ok": True, "message": "ok"}

        original_post = self.harness._post_ollama_chat
        original_execute = self.harness._execute_tool_call
        self.harness._post_ollama_chat = fake_post_ollama_chat
        self.harness._execute_tool_call = fake_execute_tool_call
        try:
            history = self.harness._run_turn(
                server=object(),
                model="model",
                ollama_url="http://127.0.0.1:11434",
                think="low",
                timeout_sec=5.0,
                tool_defs=[],
                tool_input_schemas={
                    "action.build_device_chain": {
                        "type": "object",
                        "properties": {"steps": {"type": "array"}},
                        "required": ["steps"],
                        "additionalProperties": False,
                    },
                },
                messages=[{"role": "system", "content": "sys"}],
                user_prompt="add a chain",
                show_thinking=False,
                auto_approve=True,
                max_tool_rounds=6,
            )
        finally:
            self.harness._post_ollama_chat = original_post
            self.harness._execute_tool_call = original_execute

        self.assertEqual(executed, ["action.build_device_chain"])
        assistant_contents = [msg.get("content") for msg in history if msg.get("role") == "assistant"]
        self.assertIn("Chain built successfully.", assistant_contents)

    def test_run_turn_emits_loop_limit_message_when_rounds_exhausted(self) -> None:
        def fake_post_ollama_chat(**_: Any) -> Dict[str, Any]:
            return {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "action.inspect_track_chain", "arguments": {"include_parameters": True}}}
                    ]
                }
            }

        def fake_execute_tool_call(**_: Any) -> Dict[str, Any]:
            return {"ok": True, "message": "ok"}

        original_post = self.harness._post_ollama_chat
        original_execute = self.harness._execute_tool_call
        self.harness._post_ollama_chat = fake_post_ollama_chat
        self.harness._execute_tool_call = fake_execute_tool_call
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                history = self.harness._run_turn(
                    server=object(),
                    model="model",
                    ollama_url="http://127.0.0.1:11434",
                    think="low",
                    timeout_sec=5.0,
                    tool_defs=[],
                    tool_input_schemas={
                        "action.inspect_track_chain": {
                            "type": "object",
                            "properties": {"include_parameters": {"type": "boolean"}},
                            "additionalProperties": False,
                        }
                    },
                    messages=[{"role": "system", "content": "sys"}],
                    user_prompt="inspect forever",
                    show_thinking=False,
                    auto_approve=True,
                    max_tool_rounds=3,
                )
        finally:
            self.harness._post_ollama_chat = original_post
            self.harness._execute_tool_call = original_execute

        self.assertTrue(history)
        self.assertIn("Reached tool-call loop limit (3 rounds) for this turn.", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
