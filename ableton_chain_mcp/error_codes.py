"""Error mapping helpers."""

from __future__ import annotations

from typing import Any

from .constants import (
    ERROR_ABLETON_UNAVAILABLE,
    ERROR_INTERNAL,
    ERROR_INVALID_ACTION_PAYLOAD,
    ERROR_TRANSPORT_TIMEOUT,
    ERROR_UNSUPPORTED_ACTION,
)


def map_gateway_error_code(code: Any) -> str:
    """Map gateway error codes to canonical bridge codes."""
    raw = str(code or "").strip().upper()
    if raw in {"ERR_INVALID_PARAMS", "ERR_PRECONDITION"}:
        return ERROR_INVALID_ACTION_PAYLOAD
    if raw in {"ERR_NOT_FOUND"}:
        return ERROR_UNSUPPORTED_ACTION
    if raw in {"ERR_API_UNAVAILABLE"}:
        return ERROR_ABLETON_UNAVAILABLE
    if raw in {"ERR_TIMEOUT", "TIMEOUT"}:
        return ERROR_TRANSPORT_TIMEOUT
    if raw in {"ERR_EXECUTION_FAILED"}:
        return ERROR_INTERNAL
    return ERROR_INTERNAL
