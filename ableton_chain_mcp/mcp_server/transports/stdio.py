"""STDIO transport for MCP JSON-RPC."""

from __future__ import annotations

import json
import sys
from typing import Any

from ..server import MCPServer


def run_stdio(server: MCPServer) -> int:
    server.start()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response: dict[str, Any] = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
            else:
                response = server.handle_jsonrpc(request)

            sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
            sys.stdout.flush()
    finally:
        server.stop()
    return 0
