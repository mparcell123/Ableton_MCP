from __future__ import annotations

import unittest

from ableton_chain_mcp.constants import SCHEMA_PATH
from ableton_chain_mcp.schema_loader import ActionSchema
from ableton_chain_mcp.tool_schemas import build_all_tool_schemas


class TestToolSchemas(unittest.TestCase):
    def test_expected_tool_count(self) -> None:
        schema = ActionSchema.from_file(SCHEMA_PATH)
        tools = build_all_tool_schemas(schema)

        action_tools = [t for t in tools if t["name"].startswith("action.")]
        self.assertEqual(len(action_tools), 3)
        self.assertEqual(len(tools), 5)

    def test_contains_required_tools(self) -> None:
        schema = ActionSchema.from_file(SCHEMA_PATH)
        tools = {t["name"] for t in build_all_tool_schemas(schema)}
        self.assertEqual(
            tools,
            {
                "action.build_device_chain",
                "action.update_device_parameters",
                "action.inspect_track_chain",
                "bridge.health_check",
                "bridge.capabilities",
            },
        )


if __name__ == "__main__":
    unittest.main()
