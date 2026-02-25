from __future__ import annotations

import unittest

from Gateway_Remote.chain_tools import ChainTools


class _Param:
    def __init__(self, name, value=0.0, p_min=0.0, p_max=1.0, default=0.0, is_quantized=False, unit=None):
        self.name = name
        self.value = value
        self.min = p_min
        self.max = p_max
        self.default_value = default
        self.is_quantized = is_quantized
        self._unit = unit

    def str_for_value(self, value):
        if self._unit == "hz":
            return f"{100.0 + (float(value) * 900.0):.1f} Hz"
        if self._unit == "db":
            return f"{-60.0 + (float(value) * 60.0):.1f} dB"
        if self._unit == "%":
            return f"{float(value) * 100.0:.1f} %"
        if self._unit == "mode":
            labels = {
                0: "Low Pass",
                1: "High Pass",
                2: "Band Pass",
            }
            return labels.get(int(round(float(value))), "Unknown")
        if self._unit == "eq8_type":
            labels = {
                0: "Notch",
                1: "Bell",
                2: "Low Shelf",
                3: "High Shelf",
            }
            return labels.get(int(round(float(value))), "Unknown")
        return f"{float(value):.3f}"


class _Device:
    def __init__(self, name, class_name, parameters):
        self.name = name
        self.class_name = class_name
        self.parameters = parameters


class _Track:
    def __init__(self, name):
        self.name = name
        self.devices = []


class _View:
    def __init__(self, selected_track):
        self.selected_track = selected_track
        self.selected_device = None


class _Song:
    def __init__(self, tracks):
        self.tracks = tracks
        self.view = _View(tracks[0])

    def move_device(self, device, track, index):
        devices = list(track.devices)
        devices.remove(device)
        devices.insert(index, device)
        track.devices = devices


class _CInstance:
    def log_message(self, _):
        return


class _BrowserItem:
    def __init__(self, name, make_device, children=None):
        self.name = name
        self.is_loadable = True
        self.children = children or []
        self._make_device = make_device


class _BrowserGroup:
    def __init__(self, children):
        self.children = children


class _Browser:
    def __init__(self, items, song):
        self.audio_effects = _BrowserGroup(items)
        self.midi_effects = _BrowserGroup([])
        self.instruments = _BrowserGroup([])
        self.sounds = _BrowserGroup([])
        self.max_for_live = _BrowserGroup([])
        self._song = song

    def load_item(self, item):
        track = self._song.view.selected_track
        track.devices.append(item._make_device())


class _TestChainTools(ChainTools):
    def __init__(self, song, browser):
        super().__init__(song, _CInstance())
        self._browser = browser

    def _get_live_browser(self):
        return self._browser


class TestChainTools(unittest.TestCase):
    def _build_tools(self):
        track = _Track("Bass")
        song = _Song([track])

        def make_eq8():
            return _Device(
                "EQ Eight",
                "AudioEffectGroupDevice",
                [
                    _Param("1 Frequency A", value=0.2, unit="hz"),
                    _Param("1 Gain A", value=0.5, unit="db"),
                    _Param("1 Filter Type A", value=0.0, p_min=0.0, p_max=3.0, is_quantized=True, unit="eq8_type"),
                    _Param("8 Frequency A", value=0.6, unit="hz"),
                    _Param("8 Gain A", value=0.4, unit="db"),
                    _Param("Band 8 On A", value=0.0, p_min=0.0, p_max=1.0, is_quantized=True, unit="mode"),
                ],
            )

        def make_limiter():
            return _Device(
                "Limiter",
                "AudioEffectGroupDevice",
                [
                    _Param("Ceiling", value=0.9, unit="db"),
                    _Param("Gain", value=0.0, unit="db"),
                ],
            )

        def make_auto_filter():
            return _Device(
                "Auto Filter",
                "AudioEffectGroupDevice",
                [
                    _Param("Filter Type", value=0.0, p_min=0.0, p_max=2.0, is_quantized=True, unit="mode"),
                    _Param("Frequency", value=0.3, unit="hz"),
                ],
            )

        browser = _Browser(
            [
                _BrowserItem("EQ Eight", make_eq8),
                _BrowserItem("Limiter", make_limiter),
                _BrowserItem("Auto Filter", make_auto_filter),
            ],
            song,
        )
        return _TestChainTools(song, browser), song

    def test_build_device_chain_adds_multiple_devices(self):
        tools, song = self._build_tools()
        result = tools.build_device_chain(
            steps=[
                {"device_name": "eq8"},
                {"device_name": "Limiter"},
            ]
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(song.tracks[0].devices), 2)
        self.assertEqual(result["steps_executed"][0]["device_name"], "EQ Eight")

    def test_build_device_chain_applies_absolute_update(self):
        tools, song = self._build_tools()
        result = tools.build_device_chain(
            steps=[
                {
                    "device_name": "Limiter",
                    "parameter_updates": [
                        {"param_name": "Gain", "value": 0.4},
                    ],
                }
            ]
        )
        self.assertTrue(result["ok"])
        gain_param = song.tracks[0].devices[0].parameters[1]
        self.assertAlmostEqual(gain_param.value, 0.4)

    def test_build_device_chain_applies_display_verify_update(self):
        tools, song = self._build_tools()
        result = tools.build_device_chain(
            steps=[
                {
                    "device_name": "EQ Eight",
                    "parameter_updates": [
                        {"param_name": "1 Frequency A", "target_display_value": 500.0, "target_unit": "hz"},
                    ],
                }
            ]
        )
        self.assertTrue(result["ok"])
        freq_param = song.tracks[0].devices[0].parameters[0]
        display = freq_param.str_for_value(freq_param.value)
        self.assertIn("Hz", display)

    def test_build_device_chain_applies_eq8_low_shelf_gain_alias(self):
        tools, song = self._build_tools()
        result = tools.build_device_chain(
            steps=[
                {
                    "device_name": "EQ Eight",
                    "parameter_updates": [
                        {"param_name": "Low Shelf Gain", "value": 0.25},
                    ],
                }
            ]
        )
        self.assertTrue(result["ok"])
        gain_param = song.tracks[0].devices[0].parameters[1]
        self.assertAlmostEqual(gain_param.value, 0.25)

        step_result = result["steps_executed"][0]
        self.assertEqual(step_result["parameters_applied"][0]["param_name"], "1 Gain A")
        self.assertEqual(step_result["parameters_applied"][0]["resolution"]["matched_by"], "alias")
        self.assertNotIn("Low Shelf Gain", step_result["unmatched_parameters"])

    def test_build_device_chain_applies_eq8_band_type_alias(self):
        tools, song = self._build_tools()
        result = tools.build_device_chain(
            steps=[
                {
                    "device_name": "EQ Eight",
                    "parameter_updates": [
                        {"param_name": "1 Type", "target_display_text": "Bell"},
                    ],
                }
            ]
        )
        self.assertTrue(result["ok"])
        filter_type_param = song.tracks[0].devices[0].parameters[2]
        self.assertEqual(int(round(filter_type_param.value)), 1)

        step_result = result["steps_executed"][0]
        self.assertEqual(step_result["parameters_applied"][0]["param_name"], "1 Filter Type A")
        self.assertEqual(step_result["parameters_applied"][0]["resolution"]["matched_by"], "rule")
        self.assertNotIn("1 Type", step_result["unmatched_parameters"])

    def test_build_device_chain_applies_eq8_band_gain_and_frequency_aliases(self):
        tools, song = self._build_tools()
        result = tools.build_device_chain(
            steps=[
                {
                    "device_name": "EQ Eight",
                    "parameter_updates": [
                        {"param_name": "Band 8 Gain", "value": 0.2},
                        {"param_name": "Band 8 Frequency", "value": 0.75},
                    ],
                }
            ]
        )
        self.assertTrue(result["ok"])
        freq_param = song.tracks[0].devices[0].parameters[3]
        gain_param = song.tracks[0].devices[0].parameters[4]
        self.assertAlmostEqual(gain_param.value, 0.2)
        self.assertAlmostEqual(freq_param.value, 0.75)

        step_result = result["steps_executed"][0]
        self.assertEqual(step_result["parameters_applied"][0]["resolution"]["matched_by"], "rule")
        self.assertEqual(step_result["parameters_applied"][1]["resolution"]["matched_by"], "rule")
        self.assertNotIn("Band 8 Gain", step_result["unmatched_parameters"])
        self.assertNotIn("Band 8 Frequency", step_result["unmatched_parameters"])
        band_active = song.tracks[0].devices[0].parameters[5]
        self.assertEqual(int(round(band_active.value)), 1)

    def test_build_device_chain_applies_display_text_update(self):
        tools, song = self._build_tools()
        result = tools.build_device_chain(
            steps=[
                {
                    "device_name": "Auto Filter",
                    "parameter_updates": [
                        {"param_name": "Filter Type", "target_display_text": "high pass"},
                    ],
                }
            ]
        )
        self.assertTrue(result["ok"])
        mode_param = song.tracks[0].devices[0].parameters[0]
        self.assertEqual(int(mode_param.value), 1)
        self.assertEqual(result["steps_executed"][0]["parameters_applied"][0]["mode"], "display_text")

    def test_build_device_chain_display_text_uses_fallback(self):
        tools, song = self._build_tools()
        result = tools.build_device_chain(
            steps=[
                {
                    "device_name": "Auto Filter",
                    "parameter_updates": [
                        {
                            "param_name": "Filter Type",
                            "target_display_text": "not-a-real-filter-mode",
                            "fallback_value": 2.0,
                        },
                    ],
                }
            ]
        )
        self.assertTrue(result["ok"])
        mode_param = song.tracks[0].devices[0].parameters[0]
        self.assertEqual(int(mode_param.value), 2)
        self.assertEqual(result["steps_executed"][0]["parameters_applied"][0]["mode"], "display_text_fallback")

    def test_build_device_chain_handles_unknown_parameter(self):
        tools, _ = self._build_tools()
        result = tools.build_device_chain(
            steps=[
                {
                    "device_name": "EQ Eight",
                    "parameter_updates": [{"param_name": "DoesNotExist", "value": 0.2}],
                }
            ]
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["steps_executed"][0]["unmatched_parameters"])
        self.assertEqual(result["steps_executed"][0]["unmatched_parameter_details"][0]["reason"], "no_match")

    def test_display_verify_handles_live_style_str_for_value(self):
        tools, _ = self._build_tools()

        class _LiveStyleHzParam(_Param):
            def str_for_value(self, _):
                hz = 20.0 * ((20000.0 / 20.0) ** float(self.value))
                return f"{hz:.1f} Hz"

        param = _LiveStyleHzParam("Frequency", value=0.5, p_min=0.0, p_max=1.0, unit="hz")
        result = tools._set_parameter_with_verify(
            param,
            target_display_value=8000.0,
            target_unit="hz",
            fallback_value=None,
        )
        self.assertTrue(result["ok"])
        parsed = tools._parse_display_number(result["display"])
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertGreater(parsed, 6000.0)

    def test_convert_display_number_for_unit_khz_to_hz(self):
        tools, _ = self._build_tools()
        converted = tools._convert_display_number_for_unit(8.0, "8.00 kHz", "hz")
        self.assertEqual(converted, 8000.0)

    def test_build_device_chain_fails_for_unknown_device(self):
        tools, song = self._build_tools()
        result = tools.build_device_chain(steps=[{"device_name": "Nope Device"}])
        self.assertFalse(result["ok"])
        self.assertEqual(len(song.tracks[0].devices), 0)

    def test_update_device_parameters_by_index(self):
        tools, song = self._build_tools()
        _ = tools.build_device_chain(steps=[{"device_name": "Limiter"}])
        result = tools.update_device_parameters(
            updates=[
                {
                    "device_index": 0,
                    "parameter_updates": [{"param_name": "Gain", "value": 0.4}],
                }
            ]
        )
        self.assertTrue(result["ok"])
        gain_param = song.tracks[0].devices[0].parameters[1]
        self.assertAlmostEqual(gain_param.value, 0.4)

    def test_update_device_parameters_by_name_and_occurrence(self):
        tools, song = self._build_tools()
        _ = tools.build_device_chain(
            steps=[
                {"device_name": "Auto Filter"},
                {"device_name": "Auto Filter"},
            ]
        )
        result = tools.update_device_parameters(
            updates=[
                {
                    "device_name": "Auto Filter",
                    "device_occurrence": 1,
                    "parameter_updates": [{"param_name": "Filter Type", "target_display_text": "high pass"}],
                }
            ]
        )
        self.assertTrue(result["ok"])
        first_mode = song.tracks[0].devices[0].parameters[0]
        second_mode = song.tracks[0].devices[1].parameters[0]
        self.assertEqual(int(first_mode.value), 0)
        self.assertEqual(int(second_mode.value), 1)

    def test_inspect_track_chain_returns_parameters(self):
        tools, _ = self._build_tools()
        _ = tools.build_device_chain(steps=[{"device_name": "Limiter"}])
        inspect = tools.inspect_track_chain(include_parameters=True)
        self.assertTrue(inspect["ok"])
        self.assertEqual(len(inspect["devices"]), 1)
        self.assertIn("parameters", inspect["devices"][0])


if __name__ == "__main__":
    unittest.main()
