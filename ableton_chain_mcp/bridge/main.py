"""CLI entrypoint for ableton-bridge."""

from __future__ import annotations

import argparse
import signal

from ..constants import DEFAULT_BRIDGE_SOCKET_PATH, DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_PORT
from ..logging_utils import configure_logging
from .server import BridgeServer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-path", default=DEFAULT_BRIDGE_SOCKET_PATH)
    parser.add_argument("--gateway-host", default=DEFAULT_GATEWAY_HOST)
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_PORT)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)

    server = BridgeServer(
        socket_path=args.socket_path,
        gateway_host=args.gateway_host,
        gateway_port=args.gateway_port,
    )

    def _shutdown(_signum: int, _frame: object) -> None:
        server.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        server.start()
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
