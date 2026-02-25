from __future__ import annotations

import unittest

from ableton_chain_mcp.constants import SCHEMA_PATH
from ableton_chain_mcp.observability import MetricsStore, TraceStore
from ableton_chain_mcp.schema_loader import ActionSchema
from ableton_chain_mcp.mcp_server.orchestrator import ExecutionOrchestrator
from ableton_chain_mcp.mcp_server.tool_registry import ToolRegistry


class _FakeBridgeClient:
    def request(self, payload, timeout_sec=None):
        _ = timeout_sec
        req_type = payload.get("type")
        if req_type == "health_check":
            return {
                "ok": True,
                "error_code": None,
                "message": "bridge healthy",
                "route_used": "bridge",
                "duration_ms": 1.0,
                "correlation_id": payload.get("correlation_id", "cid"),
                "payload": {"lom_adapter": {"ready": True}},
            }
        if req_type == "bridge_capabilities":
            return {
                "ok": True,
                "error_code": None,
                "message": "gateway compatible",
                "route_used": "bridge",
                "duration_ms": 1.0,
                "correlation_id": payload.get("correlation_id", "cid"),
                "payload": {"compatible": True, "required_actions": ["build_device_chain", "inspect_track_chain"]},
            }
        if req_type == "execute":
            return {
                "ok": True,
                "error_code": None,
                "message": "ok",
                "route_used": "api",
                "duration_ms": 2.0,
                "correlation_id": payload.get("correlation_id", "cid"),
                "payload": {"echo": payload.get("action")},
            }
        return {
            "ok": False,
            "error_code": "BAD_REQUEST",
            "message": "unsupported",
            "route_used": "api",
            "duration_ms": 1.0,
            "correlation_id": payload.get("correlation_id", "cid"),
            "payload": {},
        }

    def ping(self):
        return True


class _FakeSupervisorStatus:
    def __init__(self):
        self.running = True
        self.pid = 123
        self.missed_heartbeats = 0
        self.restart_count_window = 0
        self.circuit_break_until_epoch = 0


class _FakeSupervisor:
    def status(self):
        return _FakeSupervisorStatus()


class _OrchestratorShim:
    def __init__(self):
        schema = ActionSchema.from_file(SCHEMA_PATH)
        self._impl = ExecutionOrchestrator(
            schema=schema,
            bridge_client=_FakeBridgeClient(),
            bridge_supervisor=_FakeSupervisor(),
            metrics=MetricsStore(),
            traces=TraceStore(),
        )

    @property
    def schema(self):
        return self._impl.schema

    def execute_action(self, **kwargs):
        return self._impl.execute_action(**kwargs)

    def execute_bridge_request(self, **kwargs):
        return self._impl.execute_bridge_request(**kwargs)


class TestToolRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry(_OrchestratorShim())

    def test_action_call_returns_envelope(self) -> None:
        result = self.registry.call_tool(
            name="action.build_device_chain",
            arguments={"steps": [{"device_name": "EQ Eight"}]},
            correlation_id="test-cid",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["correlation_id"], "test-cid")

    def test_invalid_action_payload(self) -> None:
        result = self.registry.call_tool(
            name="action.build_device_chain",
            arguments={"steps": "bad"},
            correlation_id="test-cid",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "INVALID_ACTION_PAYLOAD")

    def test_bridge_health_tool(self) -> None:
        result = self.registry.call_tool(
            name="bridge.health_check",
            arguments={},
            correlation_id="test-cid",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["route_used"], "bridge")

    def test_bridge_capabilities_tool(self) -> None:
        result = self.registry.call_tool(
            name="bridge.capabilities",
            arguments={},
            correlation_id="test-cid",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["payload"]["compatible"])

    def test_unknown_tool(self) -> None:
        result = self.registry.call_tool(name="lom.get", arguments={}, correlation_id="test-cid")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "UNKNOWN_TOOL")


if __name__ == "__main__":
    unittest.main()
