from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "seed_actions.json"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    handler: Callable[..., Dict[str, Any]]
    required: Tuple[str, ...] = ()
    optional: Tuple[str, ...] = ()
    route: str = "api"
    destructive: bool = False
    allow_extra: bool = False


def _load_schema_actions() -> Dict[str, Dict[str, Any]]:
    if not SCHEMA_PATH.exists():
        return {}
    try:
        payload = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    actions = payload.get("actions")
    if not isinstance(actions, dict):
        return {}
    return {str(k): v for k, v in actions.items() if isinstance(v, dict)}


def _signature_contract(handler: Callable[..., Dict[str, Any]]) -> Tuple[Tuple[str, ...], Tuple[str, ...], bool]:
    required = []
    optional = []
    allow_extra = False
    signature = inspect.signature(handler)
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            allow_extra = True
            continue
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter.name)
            else:
                optional.append(parameter.name)
    return tuple(required), tuple(optional), allow_extra


def _schema_contract(schema_entry: Mapping[str, Any]) -> Optional[Tuple[Tuple[str, ...], Tuple[str, ...], bool, str, bool]]:
    required_raw = schema_entry.get("required", [])
    properties_raw = schema_entry.get("properties", {})
    if not isinstance(required_raw, list) or not isinstance(properties_raw, dict):
        return None

    required = tuple(str(item) for item in required_raw)
    optional = tuple(str(name) for name in properties_raw.keys() if str(name) not in required)
    route = str(schema_entry.get("route", "api"))
    destructive = bool(schema_entry.get("destructive", False))
    return required, optional, False, route, destructive


def _public_callable_names(tools: Any) -> Tuple[str, ...]:
    names = []
    for attr in dir(tools):
        if attr.startswith("_"):
            continue
        candidate = getattr(tools, attr, None)
        if callable(candidate):
            names.append(attr)
    return tuple(sorted(names))


def build_registry(tools: Any) -> Dict[str, ActionSpec]:
    schema_actions = _load_schema_actions()
    registry: Dict[str, ActionSpec] = {}

    for name in _public_callable_names(tools):
        handler = getattr(tools, name, None)
        if not callable(handler):
            continue

        required, optional, allow_extra = _signature_contract(handler)
        route = "api"
        destructive = False

        schema_entry = schema_actions.get(name)
        if schema_entry is not None:
            schema_contract = _schema_contract(schema_entry)
            if schema_contract is not None:
                required, optional, allow_extra, route, destructive = schema_contract

        registry[name] = ActionSpec(
            name=name,
            handler=handler,
            required=required,
            optional=optional,
            route=route,
            destructive=destructive,
            allow_extra=allow_extra,
        )

    return registry
