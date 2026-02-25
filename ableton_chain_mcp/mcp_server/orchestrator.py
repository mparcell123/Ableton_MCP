"""Execution orchestrator for MCP tool calls."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from ..constants import (
    ERROR_ABLETON_UNAVAILABLE,
    ERROR_BRIDGE_UNAVAILABLE,
    ERROR_GATEWAY_INCOMPATIBLE,
    ERROR_INVALID_ACTION_PAYLOAD,
    ERROR_INTERNAL,
    ROUTE_API,
    ROUTE_BRIDGE,
)
from ..envelope import envelope_error, ensure_normalized_envelope
from ..feature_flags import FeatureFlags
from ..observability import MetricsStore, SpanTimer, TraceStore
from ..policy import is_action_read_only
from ..schema_loader import ActionSchema
from .bridge_client import BridgeClient, BridgeClientError
from .supervisor import BridgeSupervisor


class ExecutionOrchestrator:
    def __init__(
        self,
        *,
        schema: ActionSchema,
        bridge_client: BridgeClient,
        bridge_supervisor: BridgeSupervisor,
        metrics: MetricsStore,
        traces: TraceStore,
        feature_flags: FeatureFlags | None = None,
    ) -> None:
        self.schema = schema
        self.bridge_client = bridge_client
        self.bridge_supervisor = bridge_supervisor
        self.metrics = metrics
        self.traces = traces
        self.flags = feature_flags or FeatureFlags.from_env()
        self.logger = logging.getLogger("ableton_chain_mcp.orchestrator")

    def execute_action(self, *, action_name: str, arguments: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
        start = time.perf_counter() * 1000.0
        route = ROUTE_API
        payload = dict(arguments)
        try:
            dry_run = bool(payload.pop("dry_run", False))

            with SpanTimer(self.traces, correlation_id, "schema_validate", action=action_name):
                self.schema.validate(action_name, payload, strict=True)

            spec = self.schema.get(action_name)
            if spec is None:
                raise ValueError(f"unknown action '{action_name}'")

            if dry_run:
                return {
                    "ok": True,
                    "error_code": None,
                    "message": "dry_run validation passed",
                    "route_used": route,
                    "duration_ms": (time.perf_counter() * 1000.0 - start),
                    "correlation_id": correlation_id,
                    "payload": {
                        "action": action_name,
                        "route": route,
                        "validated": True,
                    },
                }

            if not self.flags.bridge_enabled:
                return envelope_error(
                    error_code=ERROR_ABLETON_UNAVAILABLE,
                    message="bridge disabled by FF_BRIDGE_ENABLED",
                    route_used=route,
                    duration_ms=(time.perf_counter() * 1000.0 - start),
                    correlation_id=correlation_id,
                    payload={},
                )

            if not is_action_read_only(action_name, spec):
                readiness = self._ensure_bridge_ready(correlation_id)
                if readiness is not None:
                    return readiness

            with SpanTimer(self.traces, correlation_id, "bridge_execute", action=action_name, route=route):
                response = self.bridge_client.request(
                    {
                        "type": "execute",
                        "correlation_id": correlation_id,
                        "action": action_name,
                        "payload": payload,
                        "route": route,
                    },
                    timeout_sec=12.0,
                )

            result = ensure_normalized_envelope(response, fallback_route=route, correlation_id=correlation_id)
            self._record_metrics(
                action=f"action.{action_name}",
                route=route,
                ok=result.get("ok", False),
                duration_ms=float(result.get("duration_ms", 0.0)),
            )
            return result
        except ValueError as exc:
            result = envelope_error(
                error_code=ERROR_INVALID_ACTION_PAYLOAD,
                message=str(exc),
                route_used=route,
                duration_ms=(time.perf_counter() * 1000.0 - start),
                correlation_id=correlation_id,
                payload={},
            )
            self._record_metrics(action=f"action.{action_name}", route=route, ok=False, duration_ms=float(result["duration_ms"]))
            return result
        except BridgeClientError as exc:
            result = envelope_error(
                error_code=ERROR_BRIDGE_UNAVAILABLE,
                message=str(exc),
                route_used=route,
                duration_ms=(time.perf_counter() * 1000.0 - start),
                correlation_id=correlation_id,
                payload={},
            )
            self._record_metrics(action=f"action.{action_name}", route=route, ok=False, duration_ms=float(result["duration_ms"]))
            return result
        except Exception as exc:
            self.logger.exception("execute_action failed")
            result = envelope_error(
                error_code=ERROR_INTERNAL,
                message=str(exc),
                route_used=route,
                duration_ms=(time.perf_counter() * 1000.0 - start),
                correlation_id=correlation_id,
                payload={},
            )
            self._record_metrics(action=f"action.{action_name}", route=route, ok=False, duration_ms=float(result["duration_ms"]))
            return result

    def execute_bridge_request(self, *, request_type: str, correlation_id: str) -> Dict[str, Any]:
        start = time.perf_counter() * 1000.0
        if not self.flags.bridge_enabled:
            return envelope_error(
                error_code=ERROR_ABLETON_UNAVAILABLE,
                message="bridge disabled by FF_BRIDGE_ENABLED",
                route_used=ROUTE_BRIDGE,
                duration_ms=(time.perf_counter() * 1000.0 - start),
                correlation_id=correlation_id,
                payload={},
            )
        try:
            response = self.bridge_client.request({"type": request_type, "correlation_id": correlation_id}, timeout_sec=5.0)
            result = ensure_normalized_envelope(response, fallback_route=ROUTE_BRIDGE, correlation_id=correlation_id)
            self._record_metrics(
                action=f"bridge.{request_type}",
                route=ROUTE_BRIDGE,
                ok=result.get("ok", False),
                duration_ms=float(result.get("duration_ms", 0.0)),
            )
            return result
        except BridgeClientError as exc:
            result = envelope_error(
                error_code=ERROR_BRIDGE_UNAVAILABLE,
                message=str(exc),
                route_used=ROUTE_BRIDGE,
                duration_ms=(time.perf_counter() * 1000.0 - start),
                correlation_id=correlation_id,
                payload={},
            )
            self._record_metrics(action=f"bridge.{request_type}", route=ROUTE_BRIDGE, ok=False, duration_ms=float(result["duration_ms"]))
            return result

    def _ensure_bridge_ready(self, correlation_id: str) -> Dict[str, Any] | None:
        status = self.bridge_supervisor.status()
        if not status.running:
            return envelope_error(
                error_code=ERROR_ABLETON_UNAVAILABLE,
                message="bridge not running",
                route_used=ROUTE_BRIDGE,
                duration_ms=0.0,
                correlation_id=correlation_id,
                payload={"supervisor": status.__dict__},
            )

        health = self.execute_bridge_request(request_type="health_check", correlation_id=correlation_id)
        if not health.get("ok"):
            code = str(health.get("error_code") or ERROR_ABLETON_UNAVAILABLE)
            if code not in {ERROR_ABLETON_UNAVAILABLE, ERROR_GATEWAY_INCOMPATIBLE}:
                code = ERROR_ABLETON_UNAVAILABLE
            return envelope_error(
                error_code=code,
                message="bridge not ready for operation",
                route_used=ROUTE_BRIDGE,
                duration_ms=float(health.get("duration_ms", 0.0)),
                correlation_id=correlation_id,
                payload={"health": health.get("payload", {})},
            )
        return None

    def _record_metrics(self, *, action: str, route: str, ok: bool, duration_ms: float) -> None:
        status = "ok" if ok else "error"
        self.metrics.inc(f"tool_calls_total|tool={action}|route={route}|result={status}", 1)
        self.metrics.inc(f"tool_duration_ms|tool={action}|route={route}", float(duration_ms))
