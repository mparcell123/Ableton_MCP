from __future__ import annotations

import unittest

from ableton_chain_mcp.constants import SCHEMA_PATH
from ableton_chain_mcp.schema_loader import ActionSchema


class TestActionSchema(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = ActionSchema.from_file(SCHEMA_PATH)

    def test_total_actions(self) -> None:
        self.assertEqual(len(self.schema.actions()), 3)
        self.assertIn("build_device_chain", self.schema.actions())
        self.assertIn("update_device_parameters", self.schema.actions())
        self.assertIn("inspect_track_chain", self.schema.actions())

    def test_build_device_chain_validation(self) -> None:
        self.schema.validate(
            "build_device_chain",
            {
                "target": {"use_selected_track": True},
                "steps": [
                    {
                        "device_name": "EQ Eight",
                        "parameter_updates": [
                            {"param_name": "1 Frequency A", "target_display_value": 500.0, "target_unit": "hz"}
                        ],
                    }
                ],
            },
            strict=True,
        )

    def test_parameter_update_requires_exactly_one_identifier(self) -> None:
        with self.assertRaises(ValueError):
            self.schema.validate(
                "build_device_chain",
                {
                    "steps": [
                        {
                            "device_name": "Limiter",
                            "parameter_updates": [
                                {"value": 0.5},
                            ],
                        }
                    ]
                },
                strict=True,
            )

    def test_parameter_update_rejects_both_value_modes(self) -> None:
        with self.assertRaises(ValueError):
            self.schema.validate(
                "build_device_chain",
                {
                    "steps": [
                        {
                            "device_name": "Limiter",
                            "parameter_updates": [
                                {
                                    "param_name": "Gain",
                                    "value": 0.4,
                                    "target_display_value": -6.0,
                                    "target_display_text": "low pass",
                                },
                            ],
                        }
                    ]
                },
                strict=True,
            )

    def test_update_device_parameters_validation(self) -> None:
        self.schema.validate(
            "update_device_parameters",
            {
                "target": {"use_selected_track": True},
                "updates": [
                    {
                        "device_name": "Auto Filter",
                        "device_occurrence": 0,
                        "parameter_updates": [
                            {"param_name": "Filter Type", "target_display_text": "high pass"},
                        ],
                    }
                ],
            },
            strict=True,
        )

    def test_update_device_parameters_rejects_multiple_selectors(self) -> None:
        with self.assertRaises(ValueError):
            self.schema.validate(
                "update_device_parameters",
                {
                    "updates": [
                        {
                            "device_name": "EQ Eight",
                            "device_index": 0,
                            "parameter_updates": [{"param_name": "Gain", "value": 0.2}],
                        }
                    ]
                },
                strict=True,
            )

    def test_update_device_parameters_rejects_multiple_value_modes(self) -> None:
        with self.assertRaises(ValueError):
            self.schema.validate(
                "update_device_parameters",
                {
                    "updates": [
                        {
                            "device_index": 0,
                            "parameter_updates": [
                                {
                                    "param_name": "Filter Type",
                                    "target_display_value": 200.0,
                                    "target_display_text": "high pass",
                                }
                            ],
                        }
                    ]
                },
                strict=True,
            )

    def test_step_rejects_position_and_insert_index_together(self) -> None:
        with self.assertRaises(ValueError):
            self.schema.validate(
                "build_device_chain",
                {
                    "steps": [
                        {
                            "device_name": "Limiter",
                            "position": {"placement": "after", "relative_device_name": "EQ Eight"},
                            "insert_index": 1,
                        }
                    ]
                },
                strict=True,
            )

    def test_additional_properties_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.schema.validate("inspect_track_chain", {"include_parameters": True, "extra": 1}, strict=True)


if __name__ == "__main__":
    unittest.main()
