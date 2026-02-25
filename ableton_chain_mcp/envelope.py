"""Deterministic execution envelope."""

from __future__ import annotations

from typing import Any, Dict, Optional


def envelope_ok(
    *,
    message: str,
    route_used: str,
    duration_ms: float,
    correlation_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "ok": True,
        "error_code": None,
        "message": str(message),
        "route_used": route_used,
        "duration_ms": float(duration_ms),
        "correlation_id": str(correlation_id),
        "payload": payload or {},
    }


def envelope_error(
    *,
    error_code: str,
    message: str,
    route_used: str,
    duration_ms: float,
    correlation_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error_code": str(error_code),
        "message": str(message),
        "route_used": route_used,
        "duration_ms": float(duration_ms),
        "correlation_id": str(correlation_id),
        "payload": payload or {},
    }


def ensure_normalized_envelope(result: Any, *, fallback_route: str, correlation_id: str) -> Dict[str, Any]:
    if isinstance(result, dict) and {
        "ok",
        "error_code",
        "message",
        "route_used",
        "duration_ms",
        "correlation_id",
        "payload",
    }.issubset(result.keys()):
        return {
            "ok": bool(result["ok"]),
            "error_code": result.get("error_code"),
            "message": str(result.get("message", "")),
            "route_used": str(result.get("route_used", fallback_route)),
            "duration_ms": float(result.get("duration_ms", 0.0)),
            "correlation_id": str(result.get("correlation_id", correlation_id)),
            "payload": result.get("payload") or {},
        }

    if isinstance(result, dict):
        ok = bool(result.get("ok", True))
        return {
            "ok": ok,
            "error_code": None if ok else str(result.get("error_code") or "INTERNAL_ERROR"),
            "message": str(result.get("message") or result.get("error") or ("ok" if ok else "failed")),
            "route_used": fallback_route,
            "duration_ms": float(result.get("duration_ms", 0.0)),
            "correlation_id": correlation_id,
            "payload": {
                k: v
                for k, v in result.items()
                if k
                not in {"ok", "error", "error_code", "message", "duration_ms"}
            },
        }

    return {
        "ok": True,
        "error_code": None,
        "message": "ok",
        "route_used": fallback_route,
        "duration_ms": 0.0,
        "correlation_id": correlation_id,
        "payload": {"value": result},
    }
