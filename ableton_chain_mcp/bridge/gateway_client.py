"""TCP client for Ableton Remote Script gateway."""

from __future__ import annotations

import json
import socket
from typing import Any, Dict, Optional


class GatewayClientError(RuntimeError):
    """Gateway client failure."""


class GatewayTimeoutError(GatewayClientError):
    """Gateway timeout failure."""


class GatewayTCPClient:
    def __init__(self, host: str, port: int, timeout_sec: float = 3.0) -> None:
        self.host = host
        self.port = int(port)
        self.timeout_sec = float(timeout_sec)

    def send_payload(self, payload: Dict[str, Any], timeout_sec: Optional[float] = None) -> Dict[str, Any]:
        timeout = float(timeout_sec if timeout_sec is not None else self.timeout_sec)
        raw = json.dumps(payload, ensure_ascii=True) + "\n"

        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
        except OSError as exc:
            raise GatewayClientError(f"unable to connect to gateway {self.host}:{self.port}: {exc}")

        try:
            sock.settimeout(timeout)
            sock.sendall(raw.encode("utf-8"))
            response = _recv_line(sock)
            return json.loads(response)
        except socket.timeout as exc:
            raise GatewayTimeoutError(f"gateway request timed out after {timeout:.2f}s") from exc
        except json.JSONDecodeError as exc:
            raise GatewayClientError(f"gateway returned invalid json: {exc}") from exc
        except OSError as exc:
            raise GatewayClientError(f"gateway io error: {exc}") from exc
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def ping(self) -> bool:
        try:
            resp = self.send_payload({"action": "ping"}, timeout_sec=1.5)
            return bool(resp.get("ok"))
        except GatewayClientError:
            return False


def _recv_line(sock: socket.socket) -> str:
    chunks = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    if not chunks:
        raise GatewayClientError("gateway returned empty response")
    payload = b"".join(chunks)
    if b"\n" in payload:
        payload = payload.split(b"\n", 1)[0]
    return payload.decode("utf-8")
