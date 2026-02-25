"""Bridge daemon: route deterministic chain execution to LOMAdapter."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Any, Dict, Optional, Set, Tuple

from ..constants import (
    DEFAULT_GATEWAY_HOST,
    DEFAULT_GATEWAY_PORT,
    ERROR_ABLETON_UNAVAILABLE,
    ERROR_GATEWAY_INCOMPATIBLE,
    ERROR_INTERNAL,
    ERROR_INVALID_ACTION_PAYLOAD,
    HEALTH_CONNECTING,
    HEALTH_DEGRADED,
    HEALTH_READY,
    HEALTH_STARTING,
    GATEWAY_HEALTH_EQUIVALENTS,
    REQUIRED_GATEWAY_ACTIONS,
    ROUTE_API,
    ROUTE_BRIDGE,
    SCHEMA_PATH,
)
from ..envelope import envelope_error, envelope_ok
from ..feature_flags import FeatureFlags
from ..schema_loader import ActionSchema
from .adapters.lom_adapter import LOMAdapter
from .gateway_client import GatewayClientError, GatewayTCPClient


class BridgeServer:
    def __init__(
        self,
        *,
        socket_path: str,
        gateway_host: str = DEFAULT_GATEWAY_HOST,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
    ) -> None:
        self.socket_path = socket_path
        self.logger = logging.getLogger("ableton_chain_mcp.bridge")
        self.flags = FeatureFlags.from_env()

        self._schema = ActionSchema.from_file(SCHEMA_PATH)
        self._gateway = GatewayTCPClient(gateway_host, gateway_port, timeout_sec=3.0)
        self._lom_adapter = LOMAdapter(self._gateway, self._schema)

        self._state_lock = threading.Lock()
        self._state = HEALTH_STARTING
        self._last_gateway_success_epoch = 0.0
        self._consecutive_gateway_failures = 0
        self._running = False
        self._listener_socket: Optional[socket.socket] = None
        self._monitor_thread: Optional[threading.Thread] = None

        self._compat_lock = threading.Lock()
        self._capabilities: Dict[str, Any] = {
            "checked_at_epoch": 0.0,
            "strict_mode": bool(self.flags.strict_gateway_compat),
            "compatible": False,
            "message": "not yet evaluated",
            "required_actions": sorted(REQUIRED_GATEWAY_ACTIONS),
            "health_equivalents": sorted(GATEWAY_HEALTH_EQUIVALENTS),
            "available_actions": [],
            "missing_actions": sorted(REQUIRED_GATEWAY_ACTIONS),
            "health_equivalent_found": False,
            "discovery_method": None,
        }

    def start(self) -> None:
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_gateway_loop, name="BridgeGatewayMonitor", daemon=True)
        self._monitor_thread.start()

        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.socket_path)
        listener.listen(16)
        os.chmod(self.socket_path, 0o600)
        self._listener_socket = listener

        self.logger.info("bridge listening", extra={"extra_fields": {"socket_path": self.socket_path}})

        while self._running:
            try:
                client, _ = listener.accept()
            except OSError:
                if self._running:
                    self.logger.exception("bridge accept failed")
                break
            t = threading.Thread(target=self._handle_client, args=(client,), daemon=True)
            t.start()

    def stop(self) -> None:
        self._running = False
        sock = self._listener_socket
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except Exception:
                pass

    def _monitor_gateway_loop(self) -> None:
        self._set_state(HEALTH_CONNECTING)
        while self._running:
            ok = self._gateway.ping()
            with self._state_lock:
                if ok:
                    self._last_gateway_success_epoch = time.time()
                    self._consecutive_gateway_failures = 0
                    self._state = HEALTH_READY
                else:
                    self._consecutive_gateway_failures += 1
                    if self._consecutive_gateway_failures >= 3:
                        self._state = HEALTH_DEGRADED
                    else:
                        self._state = HEALTH_CONNECTING

            if ok:
                self._refresh_capabilities(force=False)
            time.sleep(2.0)

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state

    def _handle_client(self, client: socket.socket) -> None:
        buffer = ""
        try:
            client.settimeout(30.0)
            while self._running:
                chunk = client.recv(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    response = self._handle_request_line(line)
                    client.sendall((json.dumps(response, ensure_ascii=True) + "\n").encode("utf-8"))
        except socket.timeout:
            pass
        except Exception:
            self.logger.exception("bridge client handler failed")
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _handle_request_line(self, line: str) -> Dict[str, Any]:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error_code": ERROR_INVALID_ACTION_PAYLOAD, "message": f"invalid json: {exc}"}

        request_type = str(request.get("type") or "")
        correlation_id = str(request.get("correlation_id") or "")

        if request_type == "ping":
            return {"ok": True, "message": "pong", "state": self._state_snapshot()}

        if request_type == "health_check":
            return self._health_check_response(correlation_id)

        if request_type == "bridge_capabilities":
            return self._capabilities_response(correlation_id)

        if request_type == "ableton_connection_status":
            return self._connection_status_response(correlation_id)

        if request_type == "live_version":
            return self._live_version_response(correlation_id)

        if request_type == "execute":
            return self._execute_action_request(request, correlation_id)

        return {
            "ok": False,
            "error_code": ERROR_INVALID_ACTION_PAYLOAD,
            "message": f"unsupported request type '{request_type}'",
        }

    def _execute_action_request(self, request: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
        started = time.perf_counter()
        action = str(request.get("action") or "").strip()
        payload = request.get("payload") or {}

        if not action:
            return envelope_error(
                error_code=ERROR_INVALID_ACTION_PAYLOAD,
                message="missing action",
                route_used=ROUTE_API,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                correlation_id=correlation_id,
                payload={},
            )
        if not isinstance(payload, dict):
            return envelope_error(
                error_code=ERROR_INVALID_ACTION_PAYLOAD,
                message="payload must be an object",
                route_used=ROUTE_API,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                correlation_id=correlation_id,
                payload={},
            )

        if not self._is_gateway_compatible():
            return envelope_error(
                error_code=ERROR_GATEWAY_INCOMPATIBLE,
                message="gateway is incompatible with strict chain-only contract",
                route_used=ROUTE_API,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                correlation_id=correlation_id,
                payload={"capabilities": self._capabilities_snapshot()},
            )

        timeout_ms = request.get("timeout_ms")
        result = self._lom_adapter.execute_action(action=action, payload=payload, timeout_ms=timeout_ms)

        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        if result.get("ok"):
            return envelope_ok(
                message=str(result.get("message") or "ok"),
                route_used=ROUTE_API,
                duration_ms=duration_ms,
                correlation_id=correlation_id,
                payload=result.get("payload") or {},
            )

        code = str(result.get("error_code") or ERROR_INTERNAL)
        return envelope_error(
            error_code=code,
            message=str(result.get("message") or "execution failed"),
            route_used=ROUTE_API,
            duration_ms=duration_ms,
            correlation_id=correlation_id,
            payload=result.get("payload") or {},
        )

    def _health_check_response(self, correlation_id: str) -> Dict[str, Any]:
        started = time.perf_counter()
        self._refresh_capabilities(force=False)

        capabilities = self._capabilities_snapshot()
        payload = {
            "bridge_state": self._state_snapshot(),
            "lom_adapter": self._lom_adapter.health(),
            "compatibility": capabilities,
        }
        ready = bool(payload["lom_adapter"].get("ready"))

        if not ready:
            return envelope_error(
                error_code=ERROR_ABLETON_UNAVAILABLE,
                message="bridge unavailable",
                route_used=ROUTE_BRIDGE,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                correlation_id=correlation_id,
                payload=payload,
            )

        if not self._is_gateway_compatible():
            return envelope_error(
                error_code=ERROR_GATEWAY_INCOMPATIBLE,
                message="gateway is reachable but incompatible with strict chain-only contract",
                route_used=ROUTE_BRIDGE,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                correlation_id=correlation_id,
                payload=payload,
            )

        return envelope_ok(
            message="bridge healthy",
            route_used=ROUTE_BRIDGE,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            correlation_id=correlation_id,
            payload=payload,
        )

    def _capabilities_response(self, correlation_id: str) -> Dict[str, Any]:
        started = time.perf_counter()
        self._refresh_capabilities(force=True)
        payload = self._capabilities_snapshot()

        if payload.get("compatible"):
            return envelope_ok(
                message="gateway compatible",
                route_used=ROUTE_BRIDGE,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                correlation_id=correlation_id,
                payload=payload,
            )

        return envelope_error(
            error_code=ERROR_GATEWAY_INCOMPATIBLE,
            message=str(payload.get("message") or "gateway incompatible"),
            route_used=ROUTE_BRIDGE,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            correlation_id=correlation_id,
            payload=payload,
        )

    def _connection_status_response(self, correlation_id: str) -> Dict[str, Any]:
        started = time.perf_counter()
        ready = self._gateway.ping()
        payload = {
            "state": self._state_snapshot(),
            "gateway_reachable": bool(ready),
            "compatibility": self._capabilities_snapshot(),
        }

        if ready:
            return envelope_ok(
                message="ableton connection reachable",
                route_used=ROUTE_BRIDGE,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                correlation_id=correlation_id,
                payload=payload,
            )

        return envelope_error(
            error_code=ERROR_ABLETON_UNAVAILABLE,
            message="unable to reach gateway",
            route_used=ROUTE_BRIDGE,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            correlation_id=correlation_id,
            payload=payload,
        )

    def _live_version_response(self, correlation_id: str) -> Dict[str, Any]:
        started = time.perf_counter()
        result = self._lom_adapter.live_version()
        if result.get("ok"):
            return envelope_ok(
                message=str(result.get("message") or "live version"),
                route_used=ROUTE_BRIDGE,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                correlation_id=correlation_id,
                payload=result.get("payload") or {},
            )
        return envelope_error(
            error_code=str(result.get("error_code") or ERROR_INTERNAL),
            message=str(result.get("message") or "failed to query live version"),
            route_used=ROUTE_BRIDGE,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            correlation_id=correlation_id,
            payload=result.get("payload") or {},
        )

    def _state_snapshot(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "state": self._state,
                "last_gateway_success_epoch": self._last_gateway_success_epoch,
                "consecutive_gateway_failures": self._consecutive_gateway_failures,
            }

    def _is_gateway_compatible(self) -> bool:
        caps = self._capabilities_snapshot()
        if not self.flags.strict_gateway_compat:
            return True
        return bool(caps.get("compatible"))

    def _capabilities_snapshot(self) -> Dict[str, Any]:
        with self._compat_lock:
            return dict(self._capabilities)

    def _refresh_capabilities(self, *, force: bool) -> None:
        now = time.time()
        with self._compat_lock:
            last = float(self._capabilities.get("checked_at_epoch", 0.0) or 0.0)
        if not force and (now - last) < 5.0:
            return

        available_actions, discovery_method, discovery_error = self._discover_gateway_actions()
        missing_actions = sorted(REQUIRED_GATEWAY_ACTIONS - available_actions)
        health_found = bool(available_actions & GATEWAY_HEALTH_EQUIVALENTS)
        compatible = (not missing_actions) and health_found

        if discovery_error:
            message = f"capability discovery failed: {discovery_error}"
        elif compatible:
            message = "gateway supports strict chain-only contract"
        else:
            message = "gateway missing required actions for strict chain-only contract"

        payload = {
            "checked_at_epoch": now,
            "strict_mode": bool(self.flags.strict_gateway_compat),
            "compatible": bool(compatible),
            "message": message,
            "required_actions": sorted(REQUIRED_GATEWAY_ACTIONS),
            "health_equivalents": sorted(GATEWAY_HEALTH_EQUIVALENTS),
            "available_actions": sorted(available_actions),
            "missing_actions": missing_actions,
            "health_equivalent_found": health_found,
            "discovery_method": discovery_method,
        }

        with self._compat_lock:
            self._capabilities = payload

    def _discover_gateway_actions(self) -> Tuple[Set[str], Optional[str], Optional[str]]:
        for method in ("get_available_tools", "list_tools"):
            try:
                response = self._gateway.send_payload({"action": method}, timeout_sec=2.0)
            except GatewayClientError as exc:
                return set(), method, str(exc)

            if not isinstance(response, dict):
                continue
            if not response.get("ok"):
                continue

            names = self._extract_action_names(response)
            if names:
                return names, method, None

        return set(), None, "no supported capability discovery action succeeded"

    def _extract_action_names(self, response: Dict[str, Any]) -> Set[str]:
        candidates = [
            response.get("available_actions"),
            response.get("actions"),
            response.get("tools"),
            (response.get("payload") or {}).get("available_actions") if isinstance(response.get("payload"), dict) else None,
            (response.get("payload") or {}).get("actions") if isinstance(response.get("payload"), dict) else None,
            (response.get("payload") or {}).get("tools") if isinstance(response.get("payload"), dict) else None,
        ]

        for candidate in candidates:
            if isinstance(candidate, list):
                names = {str(item).strip() for item in candidate if str(item).strip()}
                if names:
                    return names
        return set()
