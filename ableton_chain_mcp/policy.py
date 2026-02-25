"""Safety and mutability policy helpers."""

from __future__ import annotations

from .constants import READ_ONLY_ACTIONS
from .schema_loader import ActionSpec


def is_action_read_only(action_name: str, spec: ActionSpec | None = None) -> bool:
    if action_name in READ_ONLY_ACTIONS:
        return True
    if spec is not None and spec.destructive:
        return False
    return False
