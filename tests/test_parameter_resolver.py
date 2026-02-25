from __future__ import annotations

import unittest

from Gateway_Remote.parameter_resolver import (
    build_parameter_index,
    eq_band_rule_candidates,
    normalize_query,
    resolve_parameter,
)


class _Param:
    def __init__(self, name: str):
        self.name = name


class _Device:
    def __init__(self, name: str, class_name: str):
        self.name = name
        self.class_name = class_name


class TestParameterResolver(unittest.TestCase):
    def test_normalize_query_equivalence(self) -> None:
        self.assertEqual(normalize_query("Band 8 Gain"), "band8gain")
        self.assertEqual(normalize_query("band-8 gain"), "band8gain")
        self.assertEqual(normalize_query("BAND 8 GAIN"), "band8gain")

    def test_build_parameter_index_exact(self) -> None:
        params = [_Param("1 Gain A"), _Param("8 Frequency A")]
        index = build_parameter_index(params)
        self.assertIs(index["1gaina"], params[0])
        self.assertIs(index["8frequencya"], params[1])

    def test_eq_band_rule_candidates(self) -> None:
        self.assertIn("8 Gain A", eq_band_rule_candidates("band8gain"))
        self.assertIn("8 Frequency A", eq_band_rule_candidates("8frequency"))
        self.assertIn("1 Filter Type A", eq_band_rule_candidates("band1type"))

    def test_resolve_exact_precedence_over_rule_and_alias(self) -> None:
        device = _Device("EQ Eight", "Eq8")
        params = [_Param("1 Gain A"), _Param("8 Gain A")]
        aliases = {"1gaina": ("8 Gain A",)}

        param, trace = resolve_parameter(params, device, "1 Gain A", aliases)
        self.assertIs(param, params[0])
        self.assertEqual(trace.matched_by, "exact")
        self.assertEqual(trace.resolved_param_name, "1 Gain A")

    def test_resolve_rule_for_eq_band_names(self) -> None:
        device = _Device("EQ Eight", "Eq8")
        params = [_Param("8 Gain A"), _Param("8 Frequency A"), _Param("1 Filter Type A")]

        gain_param, gain_trace = resolve_parameter(params, device, "Band 8 Gain", {})
        self.assertIs(gain_param, params[0])
        self.assertEqual(gain_trace.matched_by, "rule")

        freq_param, freq_trace = resolve_parameter(params, device, "Band 8 Frequency", {})
        self.assertIs(freq_param, params[1])
        self.assertEqual(freq_trace.matched_by, "rule")

        type_param, type_trace = resolve_parameter(params, device, "1 Type", {})
        self.assertIs(type_param, params[2])
        self.assertEqual(type_trace.matched_by, "rule")

    def test_resolve_alias_fallback(self) -> None:
        device = _Device("EQ Eight", "Eq8")
        params = [_Param("1 Gain A")]
        aliases = {"lowshelfgain": ("1 Gain A",)}

        param, trace = resolve_parameter(params, device, "Low Shelf Gain", aliases)
        self.assertIs(param, params[0])
        self.assertEqual(trace.matched_by, "alias")

    def test_unmatched_returns_trace_with_candidates(self) -> None:
        device = _Device("EQ Eight", "Eq8")
        params = [_Param("1 Gain A")]
        aliases = {"foo": ("Bar",)}

        param, trace = resolve_parameter(params, device, "DoesNotExist", aliases)
        self.assertIsNone(param)
        self.assertIsNone(trace.matched_by)
        self.assertEqual(trace.normalized_query, "doesnotexist")
        self.assertEqual(trace.candidate_chain[0], "DoesNotExist")

    def test_non_eq_device_ignores_eq_rules(self) -> None:
        device = _Device("Auto Filter", "AudioEffectGroupDevice")
        params = [_Param("Frequency")]

        param, trace = resolve_parameter(params, device, "Band 8 Gain", {})
        self.assertIsNone(param)
        self.assertIsNone(trace.matched_by)


if __name__ == "__main__":
    unittest.main()
