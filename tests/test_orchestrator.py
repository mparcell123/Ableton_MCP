from __future__ import annotations

import unittest

from ableton_chain_mcp.constants import ERROR_GATEWAY_INCOMPATIBLE, SCHEMA_PATH
from ableton_chain_mcp.feature_flags import FeatureFlags
from ableton_chain_mcp.observability import MetricsStore, TraceStore
from ableton_chain_mcp.schema_loader import ActionSchema
from ableton_chain_mcp.mcp_server.orchestrator import ExecutionOrchestrator


class _FakeBridgeClient:
    def __init__(self, *, health_ok: bool = True, health_error_code: str | None = None):
        self._health_ok = health_ok
        self._health_error_code = health_error_code

    def request(self, payload, timeout_sec=None):
        _ = timeout_sec
        if payload.get("type") == "health_check":
            if self._health_ok:
                return {
                    "ok": True,
                    "error_code": None,
                    "message": "bridge healthy",
                    "route_used": "bridge",
                    "duration_ms": 1.0,
                    "correlation_id": payload.get("correlation_id", "cid"),
                    "payload": {"lom_adapter": {"ready": True}, "compatibility": {"compatible": True}},
                }
            return {
                "ok": False,
                "error_code": self._health_error_code,
                "message": "incompatible",
                "route_used": "bridge",
                "duration_ms": 1.0,
                "correlation_id": payload.get("correlation_id", "cid"),
                "payload": {"compatibility": {"compatible": False}},
            }
        return {
            "ok": True,
            "error_code": None,
            "message": "ok",
            "route_used": "api",
            "duration_ms": 2.0,
            "correlation_id": payload.get("correlation_id", "cid"),
            "payload": {"echo": payload.get("action")},
        }


class _FakeSupervisorStatus:
    def __init__(self, running=True):
        self.running = running
        self.pid = 123 if running else None
        self.missed_heartbeats = 0
        self.restart_count_window = 0
        self.circuit_break_until_epoch = 0


class _FakeSupervisor:
    def __init__(self, running=True):
        self._running = running

    def status(self):
        return _FakeSupervisorStatus(self._running)


class TestOrchestrator(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = ActionSchema.from_file(SCHEMA_PATH)

    def test_dry_run_validation(self) -> None:
        orch = ExecutionOrchestrator(
            schema=self.schema,
            bridge_client=_FakeBridgeClient(),
            bridge_supervisor=_FakeSupervisor(running=True),
            metrics=MetricsStore(),
            traces=TraceStore(),
            feature_flags=FeatureFlags(bridge_enabled=True),
        )
        result = orch.execute_action(
            action_name="build_device_chain",
            arguments={"dry_run": True, "steps": [{"device_name": "EQ Eight"}]},
            correlation_id="cid",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "dry_run validation passed")

    def test_bridge_disabled_returns_unavailable(self) -> None:
        orch = ExecutionOrchestrator(
            schema=self.schema,
            bridge_client=_FakeBridgeClient(),
            bridge_supervisor=_FakeSupervisor(running=True),
            metrics=MetricsStore(),
            traces=TraceStore(),
            feature_flags=FeatureFlags(bridge_enabled=False),
        )
        result = orch.execute_action(
            action_name="build_device_chain",
            arguments={"steps": [{"device_name": "EQ Eight"}]},
            correlation_id="cid",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "ABLETON_UNAVAILABLE")

    def test_gateway_incompatible_bubbles_for_mutating_actions(self) -> None:
        orch = ExecutionOrchestrator(
            schema=self.schema,
            bridge_client=_FakeBridgeClient(health_ok=False, health_error_code=ERROR_GATEWAY_INCOMPATIBLE),
            bridge_supervisor=_FakeSupervisor(running=True),
            metrics=MetricsStore(),
            traces=TraceStore(),
            feature_flags=FeatureFlags(bridge_enabled=True),
        )
        result = orch.execute_action(
            action_name="build_device_chain",
            arguments={"steps": [{"device_name": "EQ Eight"}]},
            correlation_id="cid",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], ERROR_GATEWAY_INCOMPATIBLE)


if __name__ == "__main__":
    unittest.main()
