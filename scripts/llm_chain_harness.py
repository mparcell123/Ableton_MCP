#!/usr/bin/env python3
"""Interactive prompt harness for chain-only MCP using Ollama."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ableton_chain_mcp.constants import (
    DEFAULT_BRIDGE_SOCKET_PATH,
    DEFAULT_GATEWAY_HOST,
    DEFAULT_GATEWAY_PORT,
)
from ableton_chain_mcp.mcp_server.server import MCPServer


SYSTEM_PROMPT = (
    "You are an Ableton Live chain assistant. "
    "Use the provided MCP tools: action.inspect_track_chain, action.build_device_chain, and action.update_device_parameters. "
    "For end-state requests (for example: make a hat less harsh, warmer, brighter, tighter), plan and apply one or more effect devices first using action.build_device_chain with parameter_updates. "
    "Use action.update_device_parameters when the user clearly wants to modify existing devices. "
    "Use action.inspect_track_chain only when needed to disambiguate track/device context or verify/follow up after changes. "
    "Use canonical parameter update keys only: param_name or param_index; and exactly one of value, target_display_value, or target_display_text. "
    "Example update item: {\"param_name\":\"1 Frequency A\", \"target_display_value\":100.0, \"target_unit\":\"hz\"}. "
    "Example text update item: {\"param_name\":\"Filter Type\", \"target_display_text\":\"high pass\"}. "
    "For builds use steps as an array of objects: [{\"device_name\":\"...\", \"parameter_updates\":[...]}]. "
    "Use target.use_selected_track=true unless the user specifies a track. "
    "After tool results are provided, summarize outcome briefly."
)

_ACTION_TOOL_DEFAULT_DESCRIPTIONS = {
    "action.build_device_chain": "Build and configure an Ableton device chain",
    "action.update_device_parameters": "Update parameters on existing devices in an Ableton track",
    "action.inspect_track_chain": "Inspect devices and parameters on an Ableton track",
}

_MUTATING_ACTIONS = {
    "action.build_device_chain",
    "action.update_device_parameters",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-oss:120b-cloud")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--think", choices=["low", "medium", "high"], default="low")
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=6,
        help="Maximum assistant/tool exchange rounds per user turn (minimum 3).",
    )
    parser.add_argument("--timeout-sec", type=float, default=90.0)
    parser.add_argument("--health-timeout-sec", type=float, default=20.0)
    parser.add_argument("--bridge-socket", default=DEFAULT_BRIDGE_SOCKET_PATH)
    parser.add_argument("--gateway-host", default=DEFAULT_GATEWAY_HOST)
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_PORT)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--show-thinking", action="store_true")
    parser.add_argument("--auto-approve", action="store_true")
    return parser.parse_args()


def _jsonrpc_tool_call(server: MCPServer, name: str, arguments: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
    response = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
                "correlation_id": correlation_id,
            },
        }
    )
    result = response.get("result", {})
    content = result.get("content") or []
    if not content:
        return {
            "ok": False,
            "error_code": "EMPTY_TOOL_RESPONSE",
            "message": "Tool call returned no content",
            "route_used": "none",
            "duration_ms": 0.0,
            "correlation_id": correlation_id,
            "payload": {},
        }
    return content[0].get("json", {})


def _jsonrpc_tools_list(server: MCPServer) -> List[Dict[str, Any]]:
    response = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        }
    )
    return response.get("result", {}).get("tools", [])


def _get_tool_schema(server: MCPServer, tool_name: str) -> Dict[str, Any]:
    for tool in _jsonrpc_tools_list(server):
        if tool.get("name") == tool_name:
            return tool
    raise RuntimeError(f"tool not found in tools/list: {tool_name}")


def _get_action_ollama_tools(server: MCPServer) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    tools: List[Dict[str, Any]] = []
    schemas: Dict[str, Dict[str, Any]] = {}
    for tool_name, default_desc in _ACTION_TOOL_DEFAULT_DESCRIPTIONS.items():
        tool = _get_tool_schema(server, tool_name)
        schema = tool.get("inputSchema", {"type": "object"})
        schemas[tool_name] = schema
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool.get("description", default_desc),
                    "parameters": schema,
                },
            }
        )
    return tools, schemas


def _post_ollama_chat(
    *,
    ollama_url: str,
    model: str,
    think: str,
    timeout_sec: float,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    endpoint = urllib.parse.urljoin(ollama_url.rstrip("/") + "/", "api/chat")
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "think": think,
        "stream": False,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach Ollama at {endpoint}: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned non-JSON response: {raw[:500]}") from exc


def _normalize_arguments(raw_args: Any) -> Dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Tool arguments were not valid JSON: {raw_args}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError(f"Tool arguments must be an object, got: {type(raw_args).__name__}")


def _coerce_legacy_steps(arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """
    Safe canonical transforms for common malformed model outputs:
    - steps=[{\"add_device\": {...}}]
    - steps=[\"Limiter\", \"EQ Eight\"]
    - steps item contains `action` wrapper fields
    - top-level single-device shape {\"device_name\": \"Limiter\"}
    """
    normalized = dict(arguments)
    changed = False

    steps = normalized.get("steps")

    # Top-level single-device style to proper steps list.
    if not isinstance(steps, list):
        if isinstance(steps, dict):
            steps = [steps]
            normalized["steps"] = steps
            changed = True
        elif isinstance(normalized.get("device_name"), str):
            step: Dict[str, Any] = {"device_name": normalized.get("device_name")}
            if isinstance(normalized.get("parameter_updates"), list):
                step["parameter_updates"] = normalized.get("parameter_updates")
            elif isinstance(normalized.get("parameter_updates"), dict):
                step["parameter_updates"] = [normalized.get("parameter_updates")]
            normalized["steps"] = [step]
            changed = True
            steps = normalized["steps"]
        else:
            return normalized, changed

    normalized_steps = []
    for step in steps:
        if isinstance(step, str):
            normalized_steps.append({"device_name": step})
            changed = True
            continue

        if not isinstance(step, dict):
            normalized_steps.append(step)
            continue

        merged = dict(step)
        if isinstance(step.get("add_device"), dict):
            merged = dict(step.get("add_device") or {})
            for key in ("device_name", "device_class", "position", "insert_index", "parameter_updates"):
                if key in step and key not in merged:
                    merged[key] = step.get(key)
            changed = True

        # Drop wrapper verb field that violates schema.
        if "action" in merged:
            merged.pop("action", None)
            changed = True

        # Common aliases for device field.
        if "device_name" not in merged:
            for alt in ("device", "name", "effect", "plugin"):
                value = merged.get(alt)
                if isinstance(value, str) and value.strip():
                    merged["device_name"] = value
                    changed = True
                    break

        # Common alias for parameter updates.
        if "parameter_updates" not in merged and "parameters" in merged:
            params = merged.get("parameters")
            if isinstance(params, list):
                merged["parameter_updates"] = params
                changed = True
            elif isinstance(params, dict):
                merged["parameter_updates"] = [params]
                changed = True
            merged.pop("parameters", None)
        elif isinstance(merged.get("parameter_updates"), dict):
            merged["parameter_updates"] = [merged.get("parameter_updates")]
            changed = True

        normalized_steps.append(merged)

    normalized["steps"] = normalized_steps
    return normalized, changed


def _coerce_legacy_updates(arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """
    Safe canonical transforms for update payloads:
    - updates can be a dict or can be provided under steps
    - updates item can contain update_device wrapper fields
    - updates item can use parameters alias
    """
    normalized = dict(arguments)
    changed = False

    updates = normalized.get("updates")
    if not isinstance(updates, list):
        if isinstance(updates, dict):
            updates = [updates]
            normalized["updates"] = updates
            changed = True
        elif isinstance(normalized.get("steps"), list):
            updates = normalized.pop("steps")
            normalized["updates"] = updates
            changed = True
        elif isinstance(normalized.get("step"), dict):
            updates = [normalized.pop("step")]
            normalized["updates"] = updates
            changed = True
        else:
            return normalized, changed

    normalized_updates = []
    for item in updates:
        if not isinstance(item, dict):
            normalized_updates.append(item)
            continue

        merged = dict(item)
        if isinstance(item.get("update_device"), dict):
            merged = dict(item.get("update_device") or {})
            for key in ("device_name", "device_index", "device_occurrence", "parameter_updates"):
                if key in item and key not in merged:
                    merged[key] = item.get(key)
            changed = True

        if "action" in merged:
            merged.pop("action", None)
            changed = True

        if "device_name" not in merged:
            for alt in ("device", "name", "effect", "plugin"):
                value = merged.get(alt)
                if isinstance(value, str) and value.strip():
                    merged["device_name"] = value
                    changed = True
                    break

        if "parameter_updates" not in merged and "parameters" in merged:
            params = merged.get("parameters")
            if isinstance(params, list):
                merged["parameter_updates"] = params
                changed = True
            elif isinstance(params, dict):
                merged["parameter_updates"] = [params]
                changed = True
            merged.pop("parameters", None)
        elif isinstance(merged.get("parameter_updates"), dict):
            merged["parameter_updates"] = [merged.get("parameter_updates")]
            changed = True

        normalized_updates.append(merged)

    normalized["updates"] = normalized_updates
    return normalized, changed


def _canonicalize_parameter_update(update: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    normalized = dict(update)
    changed = False

    def _set_from_aliases(canonical: str, aliases: Tuple[str, ...]) -> None:
        nonlocal changed
        if canonical in normalized:
            return
        for alias in aliases:
            if alias in normalized and normalized.get(alias) is not None:
                normalized[canonical] = normalized.get(alias)
                changed = True
                return

    _set_from_aliases("param_name", ("parameter", "name", "param", "parameter_name"))
    _set_from_aliases("param_index", ("index", "param_id", "parameter_id"))

    if "id" in normalized and "param_name" not in normalized and "param_index" not in normalized:
        raw_id = normalized.get("id")
        if isinstance(raw_id, int) and not isinstance(raw_id, bool):
            normalized["param_index"] = raw_id
            changed = True
        elif isinstance(raw_id, str) and raw_id.strip():
            normalized["param_name"] = raw_id
            changed = True

    _set_from_aliases("target_display_value", ("target", "display_value", "target_value"))
    _set_from_aliases("target_display_text", ("text", "label", "target_text"))
    _set_from_aliases("target_unit", ("unit",))
    _set_from_aliases("fallback_value", ("fallback", "default_value"))

    return normalized, changed


def _canonicalize_parameter_updates_list(raw_updates: Any) -> Tuple[Any, bool]:
    changed = False
    updates = raw_updates
    if isinstance(raw_updates, dict):
        updates = [raw_updates]
        changed = True

    if not isinstance(updates, list):
        return updates, changed

    normalized_updates = []
    for update in updates:
        if not isinstance(update, dict):
            normalized_updates.append(update)
            continue
        normalized_update, update_changed = _canonicalize_parameter_update(update)
        if update_changed:
            changed = True
        normalized_updates.append(normalized_update)

    return normalized_updates, changed


def _canonicalize_mutation_payload(tool_name: str, arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    normalized = dict(arguments)
    changed = False

    if tool_name == "action.build_device_chain":
        steps = normalized.get("steps")
        if not isinstance(steps, list):
            return normalized, changed
        normalized_steps = []
        for step in steps:
            if not isinstance(step, dict):
                normalized_steps.append(step)
                continue
            merged = dict(step)
            updates, updates_changed = _canonicalize_parameter_updates_list(merged.get("parameter_updates"))
            if updates_changed:
                merged["parameter_updates"] = updates
                changed = True
            normalized_steps.append(merged)
        normalized["steps"] = normalized_steps
        return normalized, changed

    if tool_name == "action.update_device_parameters":
        items = normalized.get("updates")
        if not isinstance(items, list):
            return normalized, changed
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            merged = dict(item)
            updates, updates_changed = _canonicalize_parameter_updates_list(merged.get("parameter_updates"))
            if updates_changed:
                merged["parameter_updates"] = updates
                changed = True
            normalized_items.append(merged)
        normalized["updates"] = normalized_items

    return normalized, changed


def _normalize_tool_arguments(tool_name: str, arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    normalized = dict(arguments)
    changed = False

    if tool_name == "action.build_device_chain":
        normalized, step_changed = _coerce_legacy_steps(normalized)
        changed = changed or step_changed
    elif tool_name == "action.update_device_parameters":
        normalized, update_changed = _coerce_legacy_updates(normalized)
        changed = changed or update_changed

    if tool_name in _MUTATING_ACTIONS:
        normalized, canonical_changed = _canonicalize_mutation_payload(tool_name, normalized)
        changed = changed or canonical_changed

    return normalized, changed


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _validate_against_tool_schema(value: Any, schema: Dict[str, Any], path: str = "$") -> str | None:
    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _type_matches(value, expected_type):
        return f"{path} expected {expected_type}, got {type(value).__name__}"

    if expected_type == "object":
        if not isinstance(value, dict):
            return None
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        additional = schema.get("additionalProperties", True)

        for field in required:
            if field not in value:
                return f"{path} missing required field '{field}'"

        for key, child in value.items():
            if key not in properties:
                if additional is False:
                    return f"{path} contains unknown field '{key}'"
                continue
            err = _validate_against_tool_schema(child, properties[key], f"{path}.{key}")
            if err:
                return err

    if expected_type == "array":
        if not isinstance(value, list):
            return None
        items = schema.get("items") or {}
        for index, item in enumerate(value):
            err = _validate_against_tool_schema(item, items, f"{path}[{index}]")
            if err:
                return err

    if "enum" in schema:
        enum_values = schema.get("enum") or []
        if value not in enum_values:
            return f"{path} must be one of {enum_values}"

    return None


def _render_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True)


def _wait_for_bridge_ready(server: MCPServer, timeout_sec: float) -> Tuple[bool, Dict[str, Any]]:
    deadline = time.time() + max(timeout_sec, 0.0)
    last: Dict[str, Any] = {}
    while True:
        cid = str(uuid.uuid4())
        health = _jsonrpc_tool_call(server, "bridge.health_check", {}, cid)
        last = health
        if health.get("ok"):
            return True, health
        if time.time() >= deadline:
            return False, last
        time.sleep(1.0)


def _execute_tool_call(
    *,
    server: MCPServer,
    tool_name: str,
    arguments: Dict[str, Any],
    auto_approve: bool,
) -> Dict[str, Any] | None:
    print("\nProposed tool call:")
    print(f"- name: {tool_name}")
    print(f"- arguments:\n{_render_json(arguments)}")

    if not auto_approve:
        decision = input("Execute this in Ableton? [Y/n] ").strip().lower()
        if decision in {"n", "no"}:
            print("Skipped tool execution.")
            return None

    cid = str(uuid.uuid4())
    result = _jsonrpc_tool_call(server, tool_name, arguments, cid)
    print("\nTool result:")
    print(_render_json(result))
    return result


def _run_turn(
    *,
    server: MCPServer,
    model: str,
    ollama_url: str,
    think: str,
    timeout_sec: float,
    tool_defs: List[Dict[str, Any]],
    tool_input_schemas: Dict[str, Dict[str, Any]],
    messages: List[Dict[str, Any]],
    user_prompt: str,
    show_thinking: bool,
    auto_approve: bool,
    max_tool_rounds: int = 6,
) -> List[Dict[str, Any]]:
    history = list(messages)
    history.append({"role": "user", "content": user_prompt})
    round_limit = max(3, int(max_tool_rounds))

    for _ in range(round_limit):
        response = _post_ollama_chat(
            ollama_url=ollama_url,
            model=model,
            think=think,
            timeout_sec=timeout_sec,
            messages=history,
            tools=tool_defs,
        )
        message = response.get("message") or {}

        assistant_msg: Dict[str, Any] = {"role": "assistant"}
        if isinstance(message.get("content"), str):
            assistant_msg["content"] = message["content"]
        if isinstance(message.get("thinking"), str):
            assistant_msg["thinking"] = message["thinking"]
        if isinstance(message.get("tool_calls"), list):
            assistant_msg["tool_calls"] = message["tool_calls"]
        history.append(assistant_msg)

        thinking = message.get("thinking")
        if show_thinking and isinstance(thinking, str) and thinking.strip():
            print("\nLLM thinking:")
            print(thinking.strip())

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            content = str(message.get("content") or "").strip()
            if content:
                print("\nLLM:")
                print(content)
            return history

        for call in tool_calls:
            function_block = call.get("function") or {}
            tool_name = str(function_block.get("name") or "")
            if tool_name not in tool_input_schemas:
                err = {
                    "ok": False,
                    "error_code": "UNKNOWN_TOOL",
                    "message": f"Unsupported tool call requested: {tool_name}",
                }
                print("\nTool error:")
                print(_render_json(err))
                history.append({"role": "tool", "tool_name": tool_name or "unknown", "content": _render_json(err)})
                continue

            try:
                arguments = _normalize_arguments(function_block.get("arguments"))
            except RuntimeError as exc:
                err = {
                    "ok": False,
                    "error_code": "INVALID_TOOL_ARGS",
                    "message": str(exc),
                }
                print("\nTool error:")
                print(_render_json(err))
                history.append({"role": "tool", "tool_name": tool_name, "content": _render_json(err)})
                continue

            arguments, normalized = _normalize_tool_arguments(tool_name, arguments)
            if normalized:
                print("\nAdjusted tool args to schema-compliant canonical payload.")

            schema = tool_input_schemas.get(tool_name) or {"type": "object"}
            schema_error = _validate_against_tool_schema(arguments, schema)
            if schema_error:
                err = {
                    "ok": False,
                    "error_code": "INVALID_TOOL_ARGS",
                    "message": schema_error,
                }
                print("\nTool error:")
                print(_render_json(err))
                history.append({"role": "tool", "tool_name": tool_name, "content": _render_json(err)})
                continue

            result = _execute_tool_call(
                server=server,
                tool_name=tool_name,
                arguments=arguments,
                auto_approve=auto_approve,
            )
            if result is None:
                skip_result = {"ok": False, "error_code": "SKIPPED", "message": "User skipped execution"}
                history.append({"role": "tool", "tool_name": tool_name, "content": _render_json(skip_result)})
            else:
                history.append({"role": "tool", "tool_name": tool_name, "content": _render_json(result)})

    print(f"\nReached tool-call loop limit ({round_limit} rounds) for this turn.")
    return history


def main() -> int:
    args = parse_args()
    print("Starting MCP server...")
    server = MCPServer(
        bridge_socket_path=args.bridge_socket,
        gateway_host=args.gateway_host,
        gateway_port=args.gateway_port,
        log_level=args.log_level,
    )
    server.start()

    try:
        action_tools, tool_schemas = _get_action_ollama_tools(server)
        ready, health = _wait_for_bridge_ready(server, args.health_timeout_sec)
        if ready:
            print("Bridge is ready.")
        else:
            print("Bridge not ready yet; continuing anyway.")
            print(_render_json(health))
            if health.get("error_code") == "GATEWAY_INCOMPATIBLE":
                print(
                    "\nGateway is incompatible with strict chain-only mode.\n"
                    "Install/select the chain-only Gateway_Remote control surface in Ableton,\n"
                    "then restart this harness."
                )
                return 2

        print("\nInteractive mode:")
        print("- type your chain request in plain English")
        print("- /health to check bridge status")
        print("- /capabilities to check strict gateway compatibility")
        print("- /reset to clear conversation history")
        print("- /quit to exit")

        history: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

        while True:
            try:
                prompt = input("\nPrompt> ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting.")
                break

            if not prompt:
                continue
            lowered = prompt.lower()
            if lowered in {"/quit", "quit", "exit"}:
                break
            if lowered == "/reset":
                history = [{"role": "system", "content": SYSTEM_PROMPT}]
                print("Conversation context reset.")
                continue
            if lowered == "/health":
                health = _jsonrpc_tool_call(server, "bridge.health_check", {}, str(uuid.uuid4()))
                print(_render_json(health))
                continue
            if lowered == "/capabilities":
                caps = _jsonrpc_tool_call(server, "bridge.capabilities", {}, str(uuid.uuid4()))
                print(_render_json(caps))
                continue

            try:
                history = _run_turn(
                    server=server,
                    model=args.model,
                    ollama_url=args.ollama_url,
                    think=args.think,
                    timeout_sec=args.timeout_sec,
                    tool_defs=action_tools,
                    tool_input_schemas=tool_schemas,
                    messages=history,
                    user_prompt=prompt,
                    show_thinking=args.show_thinking,
                    auto_approve=args.auto_approve,
                    max_tool_rounds=args.max_tool_rounds,
                )
            except Exception as exc:
                print(f"Request failed: {exc}", file=sys.stderr)

    finally:
        print("Stopping MCP server...")
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
