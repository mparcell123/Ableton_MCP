"""MCP JSON-RPC server core with tool routing."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict

from ..constants import (
    DEFAULT_BRIDGE_SOCKET_PATH,
    DEFAULT_GATEWAY_HOST,
    DEFAULT_GATEWAY_PORT,
    SCHEMA_PATH,
)
from ..feature_flags import FeatureFlags
from ..logging_utils import configure_logging
from ..observability import EventStream, MetricsStore, TraceStore
from ..schema_loader import ActionSchema
from .bridge_client import BridgeClient
from .orchestrator import ExecutionOrchestrator
from .supervisor import BridgeSupervisor
from .tool_registry import ToolRegistry


class MCPServer:
    def __init__(
        self,
        *,
        bridge_socket_path: str = DEFAULT_BRIDGE_SOCKET_PATH,
        gateway_host: str = DEFAULT_GATEWAY_HOST,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        log_level: str = "INFO",
    ) -> None:
        configure_logging(log_level)
        self.logger = logging.getLogger("ableton_chain_mcp.server")
        self.flags = FeatureFlags.from_env()

        self.schema = ActionSchema.from_file(SCHEMA_PATH)
        self.bridge_client = BridgeClient(bridge_socket_path)
        self.metrics = MetricsStore()
        self.traces = TraceStore()
        self.events = EventStream()
        self.supervisor = BridgeSupervisor(
            bridge_client=self.bridge_client,
            socket_path=bridge_socket_path,
            gateway_host=gateway_host,
            gateway_port=gateway_port,
        )
        self.orchestrator = ExecutionOrchestrator(
            schema=self.schema,
            bridge_client=self.bridge_client,
            bridge_supervisor=self.supervisor,
            metrics=self.metrics,
            traces=self.traces,
            feature_flags=self.flags,
        )
        self.tools = ToolRegistry(self.orchestrator)

    def start(self) -> None:
        if self.flags.bridge_enabled:
            self.supervisor.start()

    def stop(self) -> None:
        if self.flags.bridge_enabled:
            self.supervisor.stop()

    def handle_jsonrpc(self, request: Dict[str, Any]) -> Dict[str, Any]:
        rid = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") or {}

        try:
            if method == "initialize":
                return self._response(
                    rid,
                    {
                        "protocolVersion": "2025-02-01",
                        "serverInfo": {
                            "name": "ableton-chain-mcp-server",
                            "version": "1.0.0",
                        },
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "experimental": {
                                "transports": ["stdio", "sse"],
                                "bridgeSocket": self.bridge_client.socket_path,
                                "featureFlags": self.flags.__dict__,
                            },
                        },
                    },
                )

            if method == "tools/list":
                return self._response(rid, {"tools": self.tools.list_tools()})

            if method == "tools/call":
                tool_name = str(params.get("name") or "")
                tool_args = params.get("arguments") or {}
                correlation_id = str(params.get("correlation_id") or uuid.uuid4())

                with_span_start = time.perf_counter() * 1000.0
                result = self.tools.call_tool(name=tool_name, arguments=tool_args, correlation_id=correlation_id)
                with_span_end = time.perf_counter() * 1000.0
                self.traces.add_span(
                    correlation_id=correlation_id,
                    name="mcp_request",
                    start_ms=with_span_start,
                    end_ms=with_span_end,
                    attrs={"tool": tool_name, "ok": bool(result.get("ok"))},
                )
                self.events.publish(
                    {
                        "event": "tool_call",
                        "correlation_id": correlation_id,
                        "tool": tool_name,
                        "ok": bool(result.get("ok")),
                    }
                )

                return self._response(
                    rid,
                    {
                        "content": [{"type": "json", "json": result}],
                        "isError": not bool(result.get("ok")),
                    },
                )

            if method == "ping":
                return self._response(rid, {"ok": True})

            if method == "metrics/get":
                return self._response(rid, {"metrics": self.metrics.snapshot()})

            if method == "traces/get":
                correlation_id = str(params.get("correlation_id") or "")
                return self._response(rid, {"spans": self.traces.list_by_correlation(correlation_id)})

            if method == "server/status":
                status = self.supervisor.status()
                return self._response(rid, {"supervisor": status.__dict__, "featureFlags": self.flags.__dict__})

            return self._error(rid, -32601, f"Method not found: {method}")
        except Exception as exc:
            self.logger.exception("jsonrpc handler failed")
            return self._error(rid, -32000, str(exc))

    def _response(self, rid: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _error(self, rid: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {
                "code": code,
                "message": message,
            },
        }
