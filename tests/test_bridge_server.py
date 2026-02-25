from __future__ import annotations

import json
import unittest

from ableton_chain_mcp.bridge.adapters.lom_adapter import LOMAdapter
from ableton_chain_mcp.bridge.server import BridgeServer
from ableton_chain_mcp.constants import ERROR_GATEWAY_INCOMPATIBLE, SCHEMA_PATH
from ableton_chain_mcp.schema_loader import ActionSchema


class _FakeGateway:
    def __init__(self, available_actions):
        self.available_actions = list(available_actions)
        self.host = "127.0.0.1"
        self.port = 8001

    def ping(self):
        return True

    def send_payload(self, payload, timeout_sec=None):
        _ = timeout_sec
        action = payload.get("action")
        if action in {"get_available_tools", "list_tools"}:
            return {
                "ok": True,
                "available_actions": list(self.available_actions),
            }
        if action == "build_device_chain":
            return {"ok": True, "message": "chain built"}
        if action == "update_device_parameters":
            return {"ok": True, "message": "updated"}
        if action == "inspect_track_chain":
            return {"ok": True, "message": "inspected", "devices": []}
        if action == "health_check":
            return {"ok": True, "message": "healthy"}
        return {"ok": False, "error_code": "ERR_NOT_FOUND", "error": f"Unknown action '{action}'"}


class TestBridgeServerCompatibility(unittest.TestCase):
    def _make_server_with_gateway(self, gateway: _FakeGateway) -> BridgeServer:
        server = BridgeServer(socket_path="/tmp/ableton_chain_bridge_test.sock")
        schema = ActionSchema.from_file(SCHEMA_PATH)
        server._gateway = gateway  # type: ignore[attr-defined]
        server._lom_adapter = LOMAdapter(gateway, schema)  # type: ignore[attr-defined]
        return server

    def test_health_fails_when_gateway_incompatible(self) -> None:
        gateway = _FakeGateway(["inspect_track_chain", "health_check"])
        server = self._make_server_with_gateway(gateway)

        response = server._handle_request_line(json.dumps({"type": "health_check", "correlation_id": "cid"}))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error_code"], ERROR_GATEWAY_INCOMPATIBLE)

    def test_execute_fails_fast_when_gateway_incompatible(self) -> None:
        gateway = _FakeGateway(["inspect_track_chain", "health_check"])
        server = self._make_server_with_gateway(gateway)

        response = server._handle_request_line(
            json.dumps(
                {
                    "type": "execute",
                    "correlation_id": "cid",
                    "action": "build_device_chain",
                    "payload": {"steps": [{"device_name": "Limiter"}]},
                }
            )
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error_code"], ERROR_GATEWAY_INCOMPATIBLE)

    def test_capabilities_and_execute_succeed_when_gateway_compatible(self) -> None:
        gateway = _FakeGateway(["build_device_chain", "update_device_parameters", "inspect_track_chain", "health_check"])
        server = self._make_server_with_gateway(gateway)

        caps = server._handle_request_line(json.dumps({"type": "bridge_capabilities", "correlation_id": "cid"}))
        self.assertTrue(caps["ok"])
        self.assertTrue(caps["payload"]["compatible"])

        execute = server._handle_request_line(
            json.dumps(
                {
                    "type": "execute",
                    "correlation_id": "cid",
                    "action": "build_device_chain",
                    "payload": {"steps": [{"device_name": "Limiter"}]},
                }
            )
        )
        self.assertTrue(execute["ok"])


if __name__ == "__main__":
    unittest.main()
