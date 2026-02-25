"""Bridge RPC client over Unix domain socket."""

from __future__ import annotations

import json
import socket
import threading
from typing import Any, Dict, Optional


class BridgeClientError(RuntimeError):
    """Bridge RPC error."""


class BridgeClient:
    def __init__(self, socket_path: str, default_timeout_sec: float = 5.0) -> None:
        self.socket_path = socket_path
        self.default_timeout_sec = default_timeout_sec
        self._lock = threading.Lock()

    def request(self, payload: Dict[str, Any], timeout_sec: Optional[float] = None) -> Dict[str, Any]:
        timeout = float(timeout_sec if timeout_sec is not None else self.default_timeout_sec)
        raw = json.dumps(payload, ensure_ascii=True) + "\n"
        with self._lock:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect(self.socket_path)
                sock.sendall(raw.encode("utf-8"))
                response = _recv_line(sock)
                return json.loads(response)
            except socket.timeout as exc:
                raise BridgeClientError(f"bridge timeout after {timeout:.2f}s") from exc
            except OSError as exc:
                raise BridgeClientError(f"bridge io error: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise BridgeClientError(f"bridge invalid json response: {exc}") from exc
            finally:
                try:
                    sock.close()
                except Exception:
                    pass

    def ping(self) -> bool:
        try:
            response = self.request({"type": "ping"}, timeout_sec=1.5)
            return bool(response.get("ok"))
        except BridgeClientError:
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
        raise BridgeClientError("bridge returned empty response")
    payload = b"".join(chunks)
    if b"\n" in payload:
        payload = payload.split(b"\n", 1)[0]
    return payload.decode("utf-8")
