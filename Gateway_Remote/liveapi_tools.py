"""LiveAPI entry surface restricted to deterministic chain operations."""

from __future__ import annotations

from .chain_tools import ChainTools


class LiveAPITools(ChainTools):
    """Gateway surface for chain builder actions only."""

    pass
