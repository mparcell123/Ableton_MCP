"""Load and validate strict high-level action schema from seed_actions.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ConstraintSpec:
    require_any: List[List[str]]
    require_exactly_one: List[List[str]]
    forbid_together: List[List[str]]


@dataclass
class PropertySpec:
    type: str
    min: Optional[float] = None
    max: Optional[float] = None
    optional: bool = False
    enum: Optional[List[Any]] = None
    items: Optional["PropertySpec"] = None
    required: Optional[List[str]] = None
    properties: Optional[Dict[str, "PropertySpec"]] = None
    constraints: Optional[ConstraintSpec] = None


@dataclass
class ActionSpec:
    name: str
    description: str
    required: Tuple[str, ...]
    properties: Dict[str, PropertySpec]
    route: str
    destructive: bool
    constraints: Optional[ConstraintSpec] = None

    @property
    def optional(self) -> Tuple[str, ...]:
        return tuple(k for k in self.properties.keys() if k not in self.required)


class ActionSchema:
    def __init__(self, actions: Dict[str, ActionSpec]) -> None:
        self._actions = dict(actions)

    @classmethod
    def from_file(cls, path: Path) -> "ActionSchema":
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_actions = data.get("actions") or {}
        actions: Dict[str, ActionSpec] = {}

        for name, raw in raw_actions.items():
            if not isinstance(raw, dict):
                continue
            properties = {
                prop_name: _parse_property(prop_name, prop_spec)
                for prop_name, prop_spec in (raw.get("properties") or {}).items()
                if isinstance(prop_spec, dict)
            }
            constraints = _parse_constraints(f"Action '{name}'", raw.get("constraints"))
            actions[str(name)] = ActionSpec(
                name=str(name),
                description=str(raw.get("description") or ""),
                required=tuple(str(v) for v in (raw.get("required") or [])),
                properties=properties,
                route=str(raw.get("route") or "api"),
                destructive=bool(raw.get("destructive", False)),
                constraints=constraints,
            )

        return cls(actions)

    def actions(self) -> Dict[str, ActionSpec]:
        return dict(self._actions)

    def get(self, action_name: str) -> Optional[ActionSpec]:
        return self._actions.get(action_name)

    def validate(self, action_name: str, payload: Dict[str, Any], *, strict: bool = True) -> None:
        if not isinstance(payload, dict):
            raise ValueError(f"Action '{action_name}' payload must be an object")

        spec = self._actions.get(action_name)
        if spec is None:
            raise ValueError(f"Action '{action_name}' not defined")

        for field in spec.required:
            if field not in payload:
                raise ValueError(f"Action '{action_name}' missing required field '{field}'")

        for key, value in payload.items():
            pspec = spec.properties.get(key)
            if pspec is None:
                if strict:
                    raise ValueError(f"Action '{action_name}' includes unknown field '{key}'")
                continue
            _validate_value(key, value, pspec)

        _validate_constraints_for_payload(f"Action '{spec.name}'", spec.constraints, payload)

    def to_json(self) -> Dict[str, Any]:
        return {
            "actions": {
                name: {
                    "description": spec.description,
                    "required": list(spec.required),
                    "route": spec.route,
                    "destructive": spec.destructive,
                    "properties": {k: _property_to_json(v) for k, v in spec.properties.items()},
                }
                for name, spec in self._actions.items()
            }
        }


def _parse_property(name: str, data: Dict[str, Any]) -> PropertySpec:
    ptype = str(data.get("type") or "")
    if not ptype:
        raise ValueError(f"Property '{name}' missing type")

    items = None
    if ptype == "array" and isinstance(data.get("items"), dict):
        items = _parse_property(f"{name}[]", data["items"])

    props = None
    if ptype == "object":
        props = {
            child_name: _parse_property(child_name, child_spec)
            for child_name, child_spec in (data.get("properties") or {}).items()
            if isinstance(child_spec, dict)
        }

    enum_values = None
    if isinstance(data.get("enum"), list):
        enum_values = list(data["enum"])

    constraints = _parse_constraints(f"Property '{name}'", data.get("constraints"))

    return PropertySpec(
        type=ptype,
        min=data.get("min"),
        max=data.get("max"),
        optional=bool(data.get("optional", False)),
        enum=enum_values,
        items=items,
        required=list(data.get("required") or []),
        properties=props,
        constraints=constraints,
    )


def _parse_constraints(context: str, raw: Any) -> Optional[ConstraintSpec]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{context} constraints must be an object")

    def _parse_group(key: str) -> List[List[str]]:
        groups = raw.get(key) or []
        if not isinstance(groups, list):
            raise ValueError(f"{context} constraints.{key} must be list")
        out: List[List[str]] = []
        for i, group in enumerate(groups):
            if not isinstance(group, list):
                raise ValueError(f"{context} constraints.{key}[{i}] must be list")
            clean = [str(v) for v in group if str(v).strip()]
            if len(clean) < 2:
                raise ValueError(f"{context} constraints.{key}[{i}] must have >=2 fields")
            out.append(clean)
        return out

    return ConstraintSpec(
        require_any=_parse_group("require_any"),
        require_exactly_one=_parse_group("require_exactly_one"),
        forbid_together=_parse_group("forbid_together"),
    )


def _validate_constraints_for_payload(context: str, constraints: ConstraintSpec | None, payload: Dict[str, Any]) -> None:
    if constraints is None:
        return

    present = set(payload.keys())

    for group in constraints.require_any:
        if not any(field in present for field in group):
            raise ValueError(f"{context} failed require_any: expected at least one of {group}")

    for group in constraints.require_exactly_one:
        count = sum(1 for field in group if field in present)
        if count != 1:
            raise ValueError(f"{context} failed require_exactly_one: expected exactly one of {group}, got {count}")

    for group in constraints.forbid_together:
        count = sum(1 for field in group if field in present)
        if count > 1:
            raise ValueError(f"{context} failed forbid_together: fields cannot co-exist {group}")


def _validate_value(field: str, value: Any, spec: PropertySpec) -> None:
    type_checks = {
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }

    checker = type_checks.get(spec.type)
    if checker and not checker(value):
        raise ValueError(f"Field '{field}' expected {spec.type}, got {type(value).__name__}")

    if spec.enum is not None and value not in spec.enum:
        raise ValueError(f"Field '{field}' must be one of {spec.enum}")

    if spec.type in {"number", "integer"}:
        if spec.min is not None and value < spec.min:
            raise ValueError(f"Field '{field}' below minimum {spec.min}")
        if spec.max is not None and value > spec.max:
            raise ValueError(f"Field '{field}' above maximum {spec.max}")

    if spec.type == "array" and spec.items is not None:
        for i, item in enumerate(value):
            _validate_value(f"{field}[{i}]", item, spec.items)

    if spec.type == "object" and spec.properties is not None:
        for req in spec.required or []:
            if req not in value:
                raise ValueError(f"Field '{field}' missing required key '{req}'")

        for child_name, child_spec in spec.properties.items():
            if child_name in value:
                _validate_value(f"{field}.{child_name}", value[child_name], child_spec)

        extras = [k for k in value.keys() if k not in spec.properties]
        if extras:
            raise ValueError(f"Field '{field}' has unknown keys {extras}")

        _validate_constraints_for_payload(f"Field '{field}'", spec.constraints, value)


def _property_to_json(spec: PropertySpec) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": spec.type,
        "optional": spec.optional,
    }
    if spec.min is not None:
        payload["min"] = spec.min
    if spec.max is not None:
        payload["max"] = spec.max
    if spec.enum is not None:
        payload["enum"] = list(spec.enum)
    if spec.items is not None:
        payload["items"] = _property_to_json(spec.items)
    if spec.required:
        payload["required"] = list(spec.required)
    if spec.properties is not None:
        payload["properties"] = {k: _property_to_json(v) for k, v in spec.properties.items()}
    if spec.constraints is not None:
        payload["constraints"] = {
            "require_any": list(spec.constraints.require_any),
            "require_exactly_one": list(spec.constraints.require_exactly_one),
            "forbid_together": list(spec.constraints.forbid_together),
        }
    return payload
