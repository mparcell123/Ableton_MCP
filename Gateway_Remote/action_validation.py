from __future__ import annotations

from typing import Any, Dict, Optional

ERROR_INVALID_PARAMS = "ERR_INVALID_PARAMS"


def validate_payload(spec: Any, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error_code": ERROR_INVALID_PARAMS,
            "error": "Action payload must be a JSON object",
        }

    missing = sorted(k for k in spec.required if k not in payload)
    if missing:
        return {
            "ok": False,
            "error_code": ERROR_INVALID_PARAMS,
            "error": "Missing required fields: {}".format(missing),
        }

    if not getattr(spec, "allow_extra", False):
        allowed = set(spec.required) | set(spec.optional) | {"action"}
        extras = sorted(k for k in payload if k not in allowed)
        if extras:
            return {
                "ok": False,
                "error_code": ERROR_INVALID_PARAMS,
                "error": "Unexpected fields: {}".format(extras),
            }

    return None
