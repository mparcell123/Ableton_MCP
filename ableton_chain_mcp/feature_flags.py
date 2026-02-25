"""Runtime feature flags."""

from __future__ import annotations

from dataclasses import dataclass

from .logging_utils import env_flag


@dataclass(frozen=True)
class FeatureFlags:
    bridge_enabled: bool = True
    enable_sse_transport: bool = True
    strict_gateway_compat: bool = True

    @classmethod
    def from_env(cls) -> "FeatureFlags":
        return cls(
            bridge_enabled=env_flag("FF_BRIDGE_ENABLED", True),
            enable_sse_transport=env_flag("FF_ENABLE_SSE_TRANSPORT", True),
            strict_gateway_compat=env_flag("FF_STRICT_GATEWAY_COMPAT", True),
        )
