"""Chain-focused Ableton Live toolset used by Gateway Remote."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple


class ChainTools:
    """Deterministic chain builder/inspector surface for Ableton Live."""

    _DEVICE_ALIASES = {
        "eq8": "EQ Eight",
        "eq eight": "EQ Eight",
        "eq-8": "EQ Eight",
        "compressor": "Compressor",
        "glue compressor": "Glue Compressor",
        "limiter": "Limiter",
        "auto filter": "Auto Filter",
        "utility": "Utility",
        "reverb": "Reverb",
        "delay": "Delay",
    }

    _EQ8_PARAMETER_ALIASES: Dict[str, Tuple[str, ...]] = {
        "1type": ("1 Filter Type A", "1 Filter Type", "1 Mode A", "1 Mode"),
        "1filtertype": ("1 Filter Type A", "1 Filter Type", "1 Mode A", "1 Mode"),
        "band1type": ("1 Filter Type A", "1 Filter Type", "1 Mode A", "1 Mode"),
        "8type": ("8 Filter Type A", "8 Filter Type", "8 Mode A", "8 Mode"),
        "8filtertype": ("8 Filter Type A", "8 Filter Type", "8 Mode A", "8 Mode"),
        "band8type": ("8 Filter Type A", "8 Filter Type", "8 Mode A", "8 Mode"),
        "lowshelfgain": ("1 Gain A", "1 Gain"),
        "lowshelffrequency": ("1 Frequency A", "1 Frequency"),
        "lowshelffreq": ("1 Frequency A", "1 Frequency"),
        "bassfrequency": ("1 Frequency A", "1 Frequency"),
        "bassfreq": ("1 Frequency A", "1 Frequency"),
        "lowshelfq": ("1 Q A", "1 Q"),
        "bassq": ("1 Q A", "1 Q"),
        "highshelfgain": ("8 Gain A", "8 Gain"),
        "highshelffrequency": ("8 Frequency A", "8 Frequency"),
        "highshelffreq": ("8 Frequency A", "8 Frequency"),
        "treblefrequency": ("8 Frequency A", "8 Frequency"),
        "treblefreq": ("8 Frequency A", "8 Frequency"),
        "highshelfq": ("8 Q A", "8 Q"),
        "trebleq": ("8 Q A", "8 Q"),
    }

    _TOKEN_RE = re.compile(r"[a-z0-9]+")

    def __init__(self, song: Any, c_instance: Any) -> None:
        self.song = song
        self.c_instance = c_instance

    def _log(self, message: str) -> None:
        try:
            self.c_instance.log_message("[ChainTools] {}".format(str(message)))
        except Exception:
            pass

    def build_device_chain(self, steps: list, target: dict | None = None) -> Dict[str, Any]:
        """Add one or more devices and apply parameter updates for each step."""
        start = time.perf_counter()

        if not isinstance(steps, list) or not steps:
            return self._error("steps must be a non-empty array", elapsed_ms=start)

        track, track_index, resolve_error = self._resolve_track_target(target)
        if resolve_error:
            return self._error(resolve_error, elapsed_ms=start)

        results: List[Dict[str, Any]] = []
        warnings: List[str] = []

        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                return self._error(
                    "step {} must be an object".format(idx),
                    elapsed_ms=start,
                    target_track=self._track_payload(track, track_index),
                    steps_executed=results,
                )

            device_name = str(step.get("device_name") or "").strip()
            if not device_name:
                return self._error(
                    "step {} missing device_name".format(idx),
                    elapsed_ms=start,
                    target_track=self._track_payload(track, track_index),
                    steps_executed=results,
                )

            inserted, insert_error = self._insert_device(track, track_index, step)
            if insert_error:
                return self._error(
                    "step {} failed: {}".format(idx, insert_error),
                    elapsed_ms=start,
                    target_track=self._track_payload(track, track_index),
                    steps_executed=results,
                )

            device = inserted["device"]
            device_result = {
                "step_index": idx,
                "device_name": str(getattr(device, "name", "")),
                "device_class": self._device_class_name(device),
                "device_index": int(inserted["device_index"]),
                "position_applied": bool(inserted.get("position_applied", False)),
                "position_message": inserted.get("position_message"),
                "parameters_applied": [],
                "unmatched_parameters": [],
            }

            apply_result = self._apply_parameter_updates(device, step.get("parameter_updates"))
            if not apply_result.get("ok"):
                return self._error(
                    "step {} failed: {}".format(idx, apply_result.get("error") or "parameter update failed"),
                    elapsed_ms=start,
                    target_track=self._track_payload(track, track_index),
                    steps_executed=results,
                )

            device_result["parameters_applied"] = list(apply_result.get("parameters_applied") or [])
            device_result["unmatched_parameters"] = list(apply_result.get("unmatched_parameters") or [])
            warnings.extend(str(w) for w in (apply_result.get("warnings") or []))

            results.append(device_result)

        payload = {
            "ok": True,
            "message": "chain built",
            "target_track": self._track_payload(track, track_index),
            "steps_executed": results,
            "warnings": warnings,
            "elapsed_ms": round((time.perf_counter() - start) * 1000.0, 2),
        }
        return payload

    def update_device_parameters(self, updates: list, target: dict | None = None) -> Dict[str, Any]:
        """Apply parameter updates to existing devices on a target track."""
        start = time.perf_counter()

        if not isinstance(updates, list) or not updates:
            return self._error("updates must be a non-empty array", elapsed_ms=start)

        track, track_index, resolve_error = self._resolve_track_target(target)
        if resolve_error:
            return self._error(resolve_error, elapsed_ms=start)

        results: List[Dict[str, Any]] = []
        warnings: List[str] = []

        for idx, item in enumerate(updates):
            if not isinstance(item, dict):
                return self._error(
                    "update {} must be an object".format(idx),
                    elapsed_ms=start,
                    target_track=self._track_payload(track, track_index),
                    updates_executed=results,
                )

            device, device_index, device_error = self._resolve_existing_device(track, item)
            if device_error:
                return self._error(
                    "update {} failed: {}".format(idx, device_error),
                    elapsed_ms=start,
                    target_track=self._track_payload(track, track_index),
                    updates_executed=results,
                )

            item_result = {
                "update_index": idx,
                "device_name": str(getattr(device, "name", "")),
                "device_class": self._device_class_name(device),
                "device_index": int(device_index),
                "parameters_applied": [],
                "unmatched_parameters": [],
            }

            apply_result = self._apply_parameter_updates(device, item.get("parameter_updates"))
            if not apply_result.get("ok"):
                return self._error(
                    "update {} failed: {}".format(idx, apply_result.get("error") or "parameter update failed"),
                    elapsed_ms=start,
                    target_track=self._track_payload(track, track_index),
                    updates_executed=results,
                )

            item_result["parameters_applied"] = list(apply_result.get("parameters_applied") or [])
            item_result["unmatched_parameters"] = list(apply_result.get("unmatched_parameters") or [])
            warnings.extend(str(w) for w in (apply_result.get("warnings") or []))

            results.append(item_result)

        return {
            "ok": True,
            "message": "device parameters updated",
            "target_track": self._track_payload(track, track_index),
            "updates_executed": results,
            "warnings": warnings,
            "elapsed_ms": round((time.perf_counter() - start) * 1000.0, 2),
        }

    def inspect_track_chain(self, target: dict | None = None, include_parameters: bool = True) -> Dict[str, Any]:
        """Return the device chain for a target track with optional parameter details."""
        start = time.perf_counter()
        track, track_index, resolve_error = self._resolve_track_target(target)
        if resolve_error:
            return self._error(resolve_error, elapsed_ms=start)

        devices = []
        for idx, device in enumerate(list(getattr(track, "devices", []) or [])):
            item = {
                "device_index": idx,
                "device_name": str(getattr(device, "name", "")),
                "device_class": self._device_class_name(device),
            }
            if bool(include_parameters):
                params = []
                for pidx, param in enumerate(list(getattr(device, "parameters", []) or [])):
                    params.append(self._parameter_payload(param, pidx, include_value=True))
                item["parameters"] = params
            devices.append(item)

        return {
            "ok": True,
            "message": "chain inspected",
            "target_track": self._track_payload(track, track_index),
            "devices": devices,
            "elapsed_ms": round((time.perf_counter() - start) * 1000.0, 2),
        }

    # ------------------------------------------------------------------
    # Track resolution helpers
    # ------------------------------------------------------------------
    def _resolve_track_target(self, target: Optional[Dict[str, Any]]) -> Tuple[Any, int, Optional[str]]:
        target = target or {}
        if not isinstance(target, dict):
            return None, -1, "target must be an object"

        use_selected_track = target.get("use_selected_track")
        if use_selected_track is None:
            use_selected_track = True

        track_index = target.get("track_index")
        track_name = target.get("track_name")

        if bool(use_selected_track) and track_index is None and not track_name:
            track = getattr(self.song.view, "selected_track", None)
            if track is None:
                return None, -1, "No selected track"
            return track, self._track_index(track), None

        if track_index is not None:
            try:
                idx = int(track_index)
            except Exception:
                return None, -1, "Invalid track_index"
            tracks = list(getattr(self.song, "tracks", []) or [])
            if idx < 0 or idx >= len(tracks):
                return None, -1, "Invalid track_index"
            return tracks[idx], idx, None

        if track_name:
            track, idx = self._resolve_track_by_name(str(track_name))
            if track is None:
                return None, -1, "Track not found"
            return track, idx, None

        # Explicit target object but no identifying fields; default to selected track.
        track = getattr(self.song.view, "selected_track", None)
        if track is None:
            return None, -1, "No selected track"
        return track, self._track_index(track), None

    def _resolve_track_by_name(self, query: str) -> Tuple[Any, int]:
        query = str(query or "").strip().lower()
        if not query:
            return None, -1
        tracks = list(getattr(self.song, "tracks", []) or [])

        exact = [
            (idx, tr)
            for idx, tr in enumerate(tracks)
            if str(getattr(tr, "name", "")).strip().lower() == query
        ]
        if len(exact) == 1:
            idx, tr = exact[0]
            return tr, idx
        if len(exact) > 1:
            return None, -1

        scored: List[Tuple[float, int, Any]] = []
        query_norm = "".join(self._normalize_track_tokens(query))
        for idx, tr in enumerate(tracks):
            name = str(getattr(tr, "name", ""))
            score = self._score_track_name_match(query_norm, name)
            if score > 0:
                scored.append((score, idx, tr))

        if not scored:
            return None, -1
        scored.sort(key=lambda item: (-item[0], item[1]))
        best = scored[0]
        if len(scored) > 1 and abs(best[0] - scored[1][0]) < 0.25:
            return None, -1
        return best[2], best[1]

    def _normalize_track_tokens(self, text: str) -> List[str]:
        drop = {"a", "an", "the", "track", "to", "on", "for", "my", "this", "that"}
        tokens = [t for t in self._TOKEN_RE.findall(str(text or "").lower()) if t not in drop]
        return tokens or self._TOKEN_RE.findall(str(text or "").lower())

    def _score_track_name_match(self, query_norm: str, candidate_name: str) -> float:
        candidate_tokens = self._normalize_track_tokens(candidate_name)
        candidate_norm = "".join(candidate_tokens)
        if not candidate_norm:
            return 0.0
        if query_norm == candidate_norm:
            return 5.0
        if query_norm in candidate_norm:
            return 2.5
        if candidate_norm in query_norm:
            return 2.0

        query_set = set(self._TOKEN_RE.findall(query_norm))
        candidate_set = set(candidate_tokens)
        if not query_set or not candidate_set:
            return 0.0
        overlap = len(query_set & candidate_set)
        return float(overlap)

    def _track_index(self, track: Any) -> int:
        try:
            return list(getattr(self.song, "tracks", []) or []).index(track)
        except Exception:
            return -1

    def _track_payload(self, track: Any, track_index: int) -> Dict[str, Any]:
        return {
            "track_index": int(track_index),
            "track_name": str(getattr(track, "name", "")),
        }

    # ------------------------------------------------------------------
    # Device and parameter helpers
    # ------------------------------------------------------------------
    def _resolve_device_alias(self, device_name: str) -> str:
        raw = str(device_name or "").strip()
        return self._DEVICE_ALIASES.get(raw.lower(), raw)

    def _insert_device(self, track: Any, track_index: int, step: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        browser = self._get_live_browser()
        if browser is None:
            return {}, "Browser API not available"

        requested_name = str(step.get("device_name") or "")
        canonical_name = self._resolve_device_alias(requested_name)
        browser_item = self._find_browser_device(browser, canonical_name)
        if browser_item is None:
            return {}, "Device '{}' not found".format(canonical_name)

        try:
            previous_count = len(list(getattr(track, "devices", []) or []))
            if hasattr(self.song.view, "selected_track"):
                self.song.view.selected_track = track
            browser.load_item(browser_item)

            for _ in range(20):
                current_count = len(list(getattr(track, "devices", []) or []))
                if current_count > previous_count:
                    break
                time.sleep(0.05)

            devices = list(getattr(track, "devices", []) or [])
            if len(devices) <= previous_count:
                return {}, "Device load timed out"

            device = devices[-1]
            current_index = len(devices) - 1
            final_index = current_index
            applied = False
            message = None

            if step.get("position") or step.get("insert_index") is not None:
                final_index, applied, message = self._apply_device_position(
                    track,
                    device,
                    current_index,
                    step.get("position"),
                    step.get("insert_index"),
                )

            return {
                "device": device,
                "device_index": final_index,
                "track_index": track_index,
                "position_applied": applied,
                "position_message": message,
            }, None
        except Exception as exc:
            return {}, str(exc)

    def _apply_device_position(
        self,
        track: Any,
        device: Any,
        current_index: int,
        position: Optional[Dict[str, Any]],
        insert_index: Optional[int],
    ) -> Tuple[int, bool, Optional[str]]:
        devices = list(getattr(track, "devices", []) or [])
        if not devices:
            return current_index, False, "No devices on track"

        desired = current_index
        msg = None

        if insert_index is not None:
            try:
                desired = int(insert_index)
            except Exception:
                return current_index, False, "invalid insert_index"
            desired = max(0, min(desired, len(devices) - 1))
            msg = "insert_index={}".format(insert_index)
        elif isinstance(position, dict):
            placement = str(position.get("placement") or "").lower()
            anchor_name = position.get("relative_device_name")
            anchor_occurrence = position.get("relative_device_index")
            anchor = self._find_anchor_device_index(devices, anchor_name, anchor_occurrence)
            if anchor is None:
                return current_index, False, "anchor not found"
            desired = anchor if placement == "before" else anchor + 1
            desired = max(0, min(desired, len(devices) - 1))
            msg = "{} {}".format(placement, anchor_name)

        if desired == current_index:
            return current_index, False, msg

        try:
            self.song.move_device(device, track, desired)
            return desired, True, msg
        except Exception as exc:
            return current_index, False, "positioning failed: {}".format(exc)

    def _find_anchor_device_index(
        self,
        devices: Sequence[Any],
        anchor_name: Optional[str],
        occurrence: Optional[int],
    ) -> Optional[int]:
        if not anchor_name:
            return None
        target = self._normalize_name(anchor_name)
        matches = []
        for idx, dev in enumerate(devices):
            candidate = self._normalize_name(getattr(dev, "name", ""))
            if target and (target == candidate or target in candidate or candidate in target):
                matches.append(idx)
        if not matches:
            return None

        if occurrence is not None:
            try:
                pick = int(occurrence)
                if 0 <= pick < len(matches):
                    return matches[pick]
            except Exception:
                pass
        return matches[0]

    def _resolve_existing_device(self, track: Any, item: Dict[str, Any]) -> Tuple[Any, int, Optional[str]]:
        devices = list(getattr(track, "devices", []) or [])
        if not devices:
            return None, -1, "No devices on track"

        has_name = item.get("device_name") is not None
        has_index = item.get("device_index") is not None
        if has_name and has_index:
            return None, -1, "device_name and device_index cannot be used together"
        if not has_name and not has_index:
            return None, -1, "missing device selector"

        if has_index:
            try:
                idx = int(item.get("device_index"))
            except Exception:
                return None, -1, "invalid device_index"
            if idx < 0 or idx >= len(devices):
                return None, -1, "invalid device_index"
            return devices[idx], idx, None

        query = self._normalize_name(item.get("device_name"))
        if not query:
            return None, -1, "invalid device_name"

        matches = []
        for idx, device in enumerate(devices):
            candidate = self._normalize_name(getattr(device, "name", ""))
            if query == candidate or query in candidate or candidate in query:
                matches.append((idx, device))

        if not matches:
            return None, -1, "device '{}' not found".format(item.get("device_name"))

        occurrence_raw = item.get("device_occurrence", 0)
        try:
            occurrence = int(occurrence_raw)
        except Exception:
            return None, -1, "invalid device_occurrence"
        if occurrence < 0 or occurrence >= len(matches):
            return None, -1, "invalid device_occurrence"

        index, device = matches[occurrence]
        return device, index, None

    def _apply_parameter_updates(self, device: Any, parameter_updates: Any) -> Dict[str, Any]:
        if parameter_updates is None:
            parameter_updates = []
        if not isinstance(parameter_updates, list):
            return {"ok": False, "error": "parameter_updates must be an array"}

        applied = []
        unmatched = []
        warnings = []

        for update in parameter_updates:
            if not isinstance(update, dict):
                unmatched.append("invalid update payload")
                continue

            target_param = self._resolve_parameter(device, update)
            if target_param is None:
                hint = update.get("param_name")
                if hint is None and update.get("param_index") is not None:
                    hint = "index:{}".format(update.get("param_index"))
                unmatched.append(str(hint or "unknown"))
                continue

            if "target_display_text" in update:
                apply_res = self._set_parameter_by_display_text(
                    target_param,
                    target_display_text=update.get("target_display_text"),
                    fallback_value=update.get("fallback_value"),
                )
            elif "target_display_value" in update:
                apply_res = self._set_parameter_with_verify(
                    target_param,
                    target_display_value=update.get("target_display_value"),
                    target_unit=update.get("target_unit"),
                    fallback_value=update.get("fallback_value"),
                )
            elif "value" in update:
                apply_res = self._set_parameter_absolute(target_param, update.get("value"))
            else:
                unmatched.append(str(update.get("param_name") or "unknown"))
                continue

            if not apply_res.get("ok"):
                unmatched.append(str(getattr(target_param, "name", "unknown")))
                warnings.append(str(apply_res.get("error") or "parameter update failed"))
                continue

            applied.append(
                {
                    "param_name": str(getattr(target_param, "name", "")),
                    "param_value": apply_res.get("value"),
                    "display_value": apply_res.get("display"),
                    "mode": apply_res.get("mode"),
                    "exact_match": apply_res.get("exact_match"),
                }
            )

        return {
            "ok": True,
            "parameters_applied": applied,
            "unmatched_parameters": unmatched,
            "warnings": warnings,
        }

    def _resolve_parameter(self, device: Any, update: Dict[str, Any]) -> Any:
        parameters = list(getattr(device, "parameters", []) or [])
        if not parameters:
            return None

        param_index = update.get("param_index")
        if param_index is not None:
            try:
                idx = int(param_index)
                if 0 <= idx < len(parameters):
                    return parameters[idx]
            except Exception:
                return None

        for query in self._parameter_query_candidates(device, update.get("param_name")):
            matched = self._match_parameter_by_name(parameters, query)
            if matched is not None:
                return matched
        return None

    def _parameter_query_candidates(self, device: Any, query: Any) -> List[str]:
        if query is None:
            return []

        query_text = str(query)
        query_norm = self._normalize_name(query_text)

        candidates: List[str] = []
        if self._is_eq8_device(device):
            candidates.extend(list(self._EQ8_PARAMETER_ALIASES.get(query_norm, ())))
        candidates.append(query_text)

        deduped: List[str] = []
        seen = set()
        for item in candidates:
            key = self._normalize_name(item)
            if not key:
                key = str(item).strip().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _is_eq8_device(self, device: Any) -> bool:
        device_name = self._normalize_name(getattr(device, "name", ""))
        device_class = self._normalize_name(getattr(device, "class_name", ""))
        return device_name == "eqeight" or device_class in {"eq8", "eqeight"}

    def _match_parameter_by_name(self, parameters: Sequence[Any], query: Any) -> Any:
        if query is None:
            return None
        query_norm = self._normalize_name(str(query))
        if not query_norm:
            return None

        best = None
        best_score = 0.0
        query_tokens = set(self._TOKEN_RE.findall(query_norm))

        for param in parameters:
            pname = str(getattr(param, "name", ""))
            pnorm = self._normalize_name(pname)
            if not pnorm:
                continue
            if query_norm == pnorm:
                return param

            score = 0.0
            if query_norm in pnorm or pnorm in query_norm:
                score += 2.0

            p_tokens = set(self._TOKEN_RE.findall(pnorm))
            score += float(len(query_tokens & p_tokens))

            if score > best_score:
                best = param
                best_score = score

        return best

    def _set_parameter_absolute(self, param: Any, value: Any) -> Dict[str, Any]:
        try:
            v = float(value)
        except Exception:
            return {"ok": False, "error": "value must be numeric"}

        p_min = getattr(param, "min", None)
        p_max = getattr(param, "max", None)
        if p_min is not None:
            v = max(float(p_min), v)
        if p_max is not None:
            v = min(float(p_max), v)

        try:
            param.value = v
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "mode": "absolute",
            "value": float(getattr(param, "value", v)),
            "display": self._safe_str_for_value(param, getattr(param, "value", v)),
            "exact_match": None,
        }

    def _set_parameter_by_display_text(
        self,
        param: Any,
        *,
        target_display_text: Any,
        fallback_value: Any,
    ) -> Dict[str, Any]:
        target_norm = self._normalize_display_text(target_display_text)
        if not target_norm:
            return {"ok": False, "error": "target_display_text must be a non-empty string"}

        if not bool(getattr(param, "is_quantized", False)):
            return {"ok": False, "error": "target_display_text is only supported for quantized parameters"}

        p_min = float(getattr(param, "min", 0.0) or 0.0)
        p_max = float(getattr(param, "max", 1.0) or 1.0)
        steps = int(max(1, (p_max - p_min) + 1))

        best_val = None
        best_score = 0.0
        best_exact = False
        for i in range(steps):
            candidate = p_min + i
            display = self._safe_str_for_value(param, candidate)
            score, exact = self._score_display_text_match(target_norm, self._normalize_display_text(display))
            if score > best_score:
                best_score = score
                best_val = candidate
                best_exact = exact
                if exact:
                    break

        if best_val is not None and best_score > 0.0:
            try:
                param.value = best_val
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
            current = float(getattr(param, "value", best_val))
            return {
                "ok": True,
                "mode": "display_text",
                "value": current,
                "display": self._safe_str_for_value(param, current),
                "exact_match": bool(best_exact),
            }

        if fallback_value is not None:
            fallback = self._set_parameter_absolute(param, fallback_value)
            if not fallback.get("ok"):
                return fallback
            return {
                "ok": True,
                "mode": "display_text_fallback",
                "value": fallback.get("value"),
                "display": fallback.get("display"),
                "exact_match": False,
            }

        return {"ok": False, "error": "target_display_text did not match any quantized value"}

    def _set_parameter_with_verify(
        self,
        param: Any,
        *,
        target_display_value: Any,
        target_unit: Optional[str],
        fallback_value: Any,
        max_iterations: int = 15,
        tolerance: float = 0.001,
    ) -> Dict[str, Any]:
        try:
            target = float(target_display_value)
        except Exception:
            return {"ok": False, "error": "target_display_value must be numeric"}

        unit = self._normalize_unit_hint(target_unit)
        p_min = float(getattr(param, "min", 0.0) or 0.0)
        p_max = float(getattr(param, "max", 1.0) or 1.0)

        def read_display(backend: float) -> Tuple[str, Optional[float]]:
            display = self._safe_str_for_value(param, backend)
            parsed = self._parse_display_number(display)
            converted = self._convert_display_number_for_unit(parsed, display, unit)
            return display, converted

        is_quantized = bool(getattr(param, "is_quantized", False))
        used_fallback = False

        if is_quantized:
            best_val = p_min
            best_diff = float("inf")
            steps = int(max(1, (p_max - p_min) + 1))
            for i in range(steps):
                candidate = p_min + i
                _, num = read_display(candidate)
                if num is None:
                    continue
                diff = abs(num - target)
                if diff < best_diff:
                    best_diff = diff
                    best_val = candidate
                    if diff < 0.001:
                        break
            param.value = best_val
            display, final_num = read_display(float(getattr(param, "value", best_val)))
            exact = final_num is not None and abs(final_num - target) < 0.01
        else:
            low, high = p_min, p_max
            best_val = (low + high) / 2.0
            best_diff = float("inf")

            _, low_num = read_display(low)
            _, high_num = read_display(high)
            ascending = True
            if low_num is not None and high_num is not None:
                ascending = high_num > low_num

            for _ in range(max(1, int(max_iterations))):
                mid = (low + high) / 2.0
                _, mid_num = read_display(mid)
                if mid_num is None:
                    break

                diff = abs(mid_num - target)
                if diff < best_diff:
                    best_diff = diff
                    best_val = mid

                if target != 0:
                    if diff / abs(target) < float(tolerance):
                        break
                elif diff < 0.01:
                    break

                if ascending:
                    if mid_num < target:
                        low = mid
                    else:
                        high = mid
                else:
                    if mid_num > target:
                        low = mid
                    else:
                        high = mid

                if abs(high - low) < 0.0001:
                    break

            best_val = max(p_min, min(p_max, best_val))
            param.value = best_val
            display, final_num = read_display(float(getattr(param, "value", best_val)))
            if final_num is None:
                exact = False
            elif target != 0:
                exact = abs(final_num - target) / abs(target) < float(tolerance)
            else:
                exact = abs(final_num - target) < 0.01

        if not exact and fallback_value is not None:
            try:
                fb = max(p_min, min(p_max, float(fallback_value)))
                param.value = fb
                display, final_num = read_display(float(getattr(param, "value", fb)))
                used_fallback = True
            except Exception:
                pass

        return {
            "ok": True,
            "mode": "display_verify",
            "value": float(getattr(param, "value", p_min)),
            "display": display,
            "exact_match": bool(exact) and not used_fallback,
        }

    def _normalize_display_text(self, value: Any) -> str:
        return " ".join(self._TOKEN_RE.findall(str(value or "").lower()))

    def _score_display_text_match(self, target_norm: str, candidate_norm: str) -> Tuple[float, bool]:
        if not target_norm or not candidate_norm:
            return 0.0, False
        if target_norm == candidate_norm:
            return 100.0, True
        if target_norm in candidate_norm or candidate_norm in target_norm:
            return 10.0, False
        target_tokens = set(target_norm.split())
        candidate_tokens = set(candidate_norm.split())
        overlap = target_tokens & candidate_tokens
        return float(len(overlap)), False

    def _parameter_payload(self, param: Any, index: int, include_value: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "index": int(index),
            "name": str(getattr(param, "name", "")),
            "min": self._safe_float(getattr(param, "min", None)),
            "max": self._safe_float(getattr(param, "max", None)),
            "default": self._safe_float(getattr(param, "default_value", None)),
            "is_quantized": bool(getattr(param, "is_quantized", False)),
        }
        if include_value:
            current = self._safe_float(getattr(param, "value", None))
            payload["value"] = current
            payload["display"] = self._safe_str_for_value(param, current)
        return payload

    # ------------------------------------------------------------------
    # Browser helpers
    # ------------------------------------------------------------------
    def _get_live_browser(self) -> Any:
        try:
            import Live  # type: ignore

            app = Live.Application.get_application()
            return getattr(app, "browser", None)
        except Exception:
            return None

    def _find_browser_device(self, browser: Any, device_name: str) -> Any:
        targets = [
            "audio_effects",
            "midi_effects",
            "instruments",
            "sounds",
            "max_for_live",
        ]
        normalized_target = self._normalize_name(device_name)

        for attr in targets:
            try:
                root = getattr(browser, attr)
            except Exception:
                continue
            found = self._search_browser_node(root, normalized_target)
            if found is not None:
                return found
        return None

    def _search_browser_node(self, node: Any, normalized_target: str) -> Any:
        stack = [node]
        visited = set()

        while stack:
            current = stack.pop()
            marker = id(current)
            if marker in visited:
                continue
            visited.add(marker)

            name = self._normalize_name(getattr(current, "name", ""))
            if normalized_target and (normalized_target == name or normalized_target in name):
                if bool(getattr(current, "is_loadable", True)):
                    return current

            children = []
            for child_attr in ("children", "items"):
                try:
                    values = getattr(current, child_attr, None)
                except Exception:
                    values = None
                if values:
                    try:
                        children.extend(list(values))
                    except Exception:
                        pass
            stack.extend(children)

        return None

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------
    def _device_class_name(self, device: Any) -> str:
        return str(getattr(device, "class_name", "") or type(device).__name__)

    def _normalize_name(self, text: Any) -> str:
        return "".join(self._TOKEN_RE.findall(str(text or "").lower()))

    def _safe_float(self, value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _safe_str_for_value(self, param: Any, backend_value: Any) -> Optional[str]:
        if backend_value is None:
            return None
        try:
            if hasattr(param, "str_for_value"):
                return str(param.str_for_value(float(backend_value)))
        except Exception:
            pass
        try:
            return str(float(backend_value))
        except Exception:
            return None

    def _parse_display_number(self, display: Any) -> Optional[float]:
        if display is None:
            return None
        match = re.search(r"[-+]?\d+\.?\d*", str(display))
        if not match:
            return None
        try:
            return float(match.group())
        except Exception:
            return None

    def _normalize_unit_hint(self, unit: Optional[str]) -> Optional[str]:
        if not unit:
            return None
        value = str(unit).strip().lower()
        aliases = {
            "percent": "%",
            "percentage": "%",
            "pct": "%",
            "%": "%",
            "ms": "ms",
            "millisecond": "ms",
            "milliseconds": "ms",
            "s": "s",
            "sec": "s",
            "second": "s",
            "seconds": "s",
        }
        return aliases.get(value, value)

    def _convert_display_number_for_unit(
        self,
        number: Optional[float],
        display_str: Any,
        unit_hint: Optional[str],
    ) -> Optional[float]:
        if number is None or not unit_hint:
            return number
        display = str(display_str or "").lower()

        if unit_hint == "s":
            if "ms" in display:
                return float(number) / 1000.0
            return float(number)

        if unit_hint == "ms":
            if "ms" in display:
                return float(number)
            if "sec" in display or "seconds" in display:
                return float(number) * 1000.0
            return float(number)

        if unit_hint == "%":
            if "%" in display:
                return float(number)
            if -1.0 <= float(number) <= 1.0:
                return float(number) * 100.0
            return float(number)

        return float(number)

    def _error(self, message: str, elapsed_ms: float, **extra: Any) -> Dict[str, Any]:
        payload = {
            "ok": False,
            "error": str(message),
            "elapsed_ms": round((time.perf_counter() - elapsed_ms) * 1000.0, 2),
        }
        payload.update(extra)
        return payload
