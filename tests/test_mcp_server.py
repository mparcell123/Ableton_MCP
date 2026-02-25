from __future__ import annotations

import unittest

from ableton_chain_mcp.mcp_server.server import MCPServer


class TestMcpServer(unittest.TestCase):
    def test_tools_list_is_chain_only(self) -> None:
        server = MCPServer(log_level="ERROR")
        response = server.handle_jsonrpc({"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}})
        tools = response.get("result", {}).get("tools", [])
        names = {tool.get("name") for tool in tools}
        self.assertEqual(
            names,
            {
                "action.build_device_chain",
                "action.update_device_parameters",
                "action.inspect_track_chain",
                "bridge.health_check",
                "bridge.capabilities",
            },
        )

    def test_tools_call_returns_envelope(self) -> None:
        server = MCPServer(log_level="ERROR")
        response = server.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tools/call",
                "params": {
                    "name": "action.build_device_chain",
                    "arguments": {"dry_run": True, "steps": [{"device_name": "EQ Eight"}]},
                    "correlation_id": "cid",
                },
            }
        )
        result = response.get("result", {})
        content = result.get("content", [{}])[0].get("json", {})
        self.assertIn("ok", content)
        self.assertEqual(content.get("correlation_id"), "cid")


if __name__ == "__main__":
    unittest.main()
