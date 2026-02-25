"""Generate MCP tool schemas from ActionSchema."""

from __future__ import annotations

from typing import Any, Dict, List

from .schema_loader import ActionSchema, PropertySpec


BRIDGE_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "bridge.health_check",
        "description": "Bridge runtime health status including gateway compatibility.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "name": "bridge.capabilities",
        "description": "Gateway compatibility and supported action capabilities.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
]


def _property_to_json_schema(prop: PropertySpec) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"type": prop.type}
    if prop.min is not None:
        payload["minimum"] = prop.min
    if prop.max is not None:
        payload["maximum"] = prop.max
    if prop.enum is not None:
        payload["enum"] = list(prop.enum)
    if prop.type == "array":
        payload["items"] = _property_to_json_schema(prop.items) if prop.items else {}
    if prop.type == "object":
        props = {}
        for key, value in (prop.properties or {}).items():
            props[key] = _property_to_json_schema(value)
        payload["properties"] = props
        payload["additionalProperties"] = False
        req = prop.required or []
        if req:
            payload["required"] = list(req)
    return payload


def build_action_tool_schemas(action_schema: ActionSchema) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    for action_name, spec in sorted(action_schema.actions().items()):
        properties = {
            name: _property_to_json_schema(prop)
            for name, prop in spec.properties.items()
        }
        required = list(spec.required)

        # dry_run is an MCP-level convenience field, not part of action schema payload.
        properties["dry_run"] = {"type": "boolean"}

        tools.append(
            {
                "name": f"action.{action_name}",
                "description": spec.description,
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": required,
                    "properties": properties,
                },
            }
        )
    return tools


def build_all_tool_schemas(action_schema: ActionSchema) -> List[Dict[str, Any]]:
    return build_action_tool_schemas(action_schema) + BRIDGE_TOOL_SCHEMAS
