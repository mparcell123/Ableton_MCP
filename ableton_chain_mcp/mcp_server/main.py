"""CLI entrypoint for ableton-mcp-server."""

from __future__ import annotations

import argparse

from ..constants import DEFAULT_BRIDGE_SOCKET_PATH, DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_PORT
from ..feature_flags import FeatureFlags
from .server import MCPServer
from .transports.sse import run_sse
from .transports.stdio import run_stdio


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bridge-socket", default=DEFAULT_BRIDGE_SOCKET_PATH)
    parser.add_argument("--gateway-host", default=DEFAULT_GATEWAY_HOST)
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_PORT)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    flags = FeatureFlags.from_env()
    server = MCPServer(
        bridge_socket_path=args.bridge_socket,
        gateway_host=args.gateway_host,
        gateway_port=args.gateway_port,
        log_level=args.log_level,
    )

    if args.transport == "stdio":
        return run_stdio(server)
    if not flags.enable_sse_transport:
        raise SystemExit("SSE transport disabled by FF_ENABLE_SSE_TRANSPORT")
    return run_sse(server, args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
