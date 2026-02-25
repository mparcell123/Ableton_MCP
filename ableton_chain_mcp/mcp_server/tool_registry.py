"""MCP tool registry and dispatch."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from ..tool_schemas import BRIDGE_TOOL_SCHEMAS, build_action_tool_schemas
from .orchestrator import ExecutionOrchestrator


class ToolRegistry:
    def __init__(
        self,
        orchestrator: ExecutionOrchestrator,
    ) -> None:
        self._orchestrator = orchestrator
        self._action_tools = build_action_tool_schemas(orchestrator.schema)
        self._tools = list(self._action_tools) + BRIDGE_TOOL_SCHEMAS

    def list_tools(self) -> List[Dict[str, Any]]:
        return list(self._tools)

    def call_tool(
        self,
        *,
        name: str,
        arguments: Dict[str, Any] | None,
        correlation_id: str | None = None,
    ) -> Dict[str, Any]:
        payload = dict(arguments or {})
        cid = correlation_id or str(uuid.uuid4())

        if name.startswith("action."):
            action_name = name.split(".", 1)[1]
            return self._orchestrator.execute_action(
                action_name=action_name,
                arguments=payload,
                correlation_id=cid,
            )

        if name == "bridge.health_check":
            return self._orchestrator.execute_bridge_request(
                request_type="health_check",
                correlation_id=cid,
            )

        if name == "bridge.capabilities":
            return self._orchestrator.execute_bridge_request(
                request_type="bridge_capabilities",
                correlation_id=cid,
            )

        return {
            "ok": False,
            "error_code": "UNKNOWN_TOOL",
            "message": f"Unknown tool '{name}'",
            "route_used": "none",
            "duration_ms": 0.0,
            "correlation_id": cid,
            "payload": {},
        }
