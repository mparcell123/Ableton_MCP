"""LOM adapter backed by Ableton Remote Script gateway."""

from __future__ import annotations

import random
import time
from typing import Any, Dict, Optional

from ...constants import (
    API_DEFAULT_TIMEOUT_MS,
    API_MAX_TIMEOUT_MS,
    ERROR_ABLETON_UNAVAILABLE,
    ERROR_INTERNAL,
    ERROR_TRANSPORT_TIMEOUT,
)
from ...error_codes import map_gateway_error_code
from ...policy import is_action_read_only
from ...schema_loader import ActionSchema
from ..gateway_client import GatewayClientError, GatewayTCPClient, GatewayTimeoutError


class LOMAdapter:
    def __init__(self, gateway: GatewayTCPClient, schema: ActionSchema) -> None:
        self._gateway = gateway
        self._schema = schema

    def health(self) -> Dict[str, Any]:
        ok = self._gateway.ping()
        return {
            "ready": bool(ok),
            "gateway_host": self._gateway.host,
            "gateway_port": self._gateway.port,
        }

    def execute_action(
        self,
        *,
        action: str,
        payload: Dict[str, Any],
        timeout_ms: Optional[int],
    ) -> Dict[str, Any]:
        timeout = _clamp_timeout(timeout_ms)
        spec = self._schema.get(action)
        is_read_only = is_action_read_only(action, spec)

        max_attempts = 3 if is_read_only else 1
        attempts = 0
        last_error: Dict[str, Any] | None = None

        while attempts < max_attempts:
            attempts += 1
            try:
                response = self._gateway.send_payload({"action": action, **payload}, timeout_sec=timeout / 1000.0)
                if response.get("ok"):
                    return {
                        "ok": True,
                        "message": str(response.get("message") or f"{action} executed"),
                        "payload": {
                            k: v
                            for k, v in response.items()
                            if k not in {"ok", "message", "error", "error_code"}
                        },
                    }

                code = map_gateway_error_code(response.get("error_code"))
                return {
                    "ok": False,
                    "error_code": code,
                    "message": str(response.get("error") or response.get("message") or "gateway action failed"),
                    "payload": {
                        "gateway_error_code": response.get("error_code"),
                        "gateway_response": response,
                    },
                }
            except GatewayTimeoutError as exc:
                last_error = {
                    "ok": False,
                    "error_code": ERROR_TRANSPORT_TIMEOUT,
                    "message": str(exc),
                    "payload": {"attempt": attempts, "timeout_ms": timeout},
                }
            except GatewayClientError as exc:
                last_error = {
                    "ok": False,
                    "error_code": ERROR_ABLETON_UNAVAILABLE,
                    "message": str(exc),
                    "payload": {"attempt": attempts},
                }

            if attempts < max_attempts:
                delay = (0.2 if attempts == 1 else 0.4) + random.uniform(0.0, 0.05)
                time.sleep(delay)

        return last_error or {
            "ok": False,
            "error_code": ERROR_INTERNAL,
            "message": "Unknown LOM adapter failure",
            "payload": {},
        }

    def live_version(self) -> Dict[str, Any]:
        version = self.execute_action(action="get_application_version", payload={}, timeout_ms=1500)
        if version.get("ok"):
            return version

        # Fallback to health if version action is not available on this gateway.
        health = self.execute_action(action="health_check", payload={}, timeout_ms=1500)
        if health.get("ok"):
            payload = dict(health.get("payload") or {})
            payload["version_action_unavailable"] = True
            return {
                "ok": True,
                "message": "live version unavailable, health check succeeded",
                "payload": payload,
            }

        return {
            "ok": False,
            "error_code": health.get("error_code") or version.get("error_code") or ERROR_INTERNAL,
            "message": health.get("message") or version.get("message") or "Unable to determine Live version",
            "payload": {
                "version_attempt": version.get("payload") or {},
                "health_attempt": health.get("payload") or {},
            },
        }



def _clamp_timeout(timeout_ms: Optional[int]) -> int:
    if timeout_ms is None:
        return API_DEFAULT_TIMEOUT_MS
    return max(1, min(int(timeout_ms), API_MAX_TIMEOUT_MS))
