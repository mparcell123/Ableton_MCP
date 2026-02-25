"""SSE + HTTP JSON-RPC transport."""

from __future__ import annotations

import json
import queue
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..server import MCPServer


class _Handler(BaseHTTPRequestHandler):
    server_version = "AbletonMCP/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json({"ok": True})
            return

        if self.path != "/sse":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return

        q = self.server.mcp_events.subscribe()  # type: ignore[attr-defined]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            while True:
                try:
                    event = q.get(timeout=2.0)
                    blob = f"event: {event.get('event', 'message')}\ndata: {json.dumps(event, ensure_ascii=True)}\n\n"
                except queue.Empty:
                    blob = ": keepalive\n\n"
                self.wfile.write(blob.encode("utf-8"))
                self.wfile.flush()
        except BrokenPipeError:
            pass
        except ConnectionResetError:
            pass
        finally:
            self.server.mcp_events.unsubscribe(q)  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/rpc":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return

        content_len = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_len)
        try:
            request = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._send_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}, status=HTTPStatus.BAD_REQUEST)
            return

        response = self.server.mcp_server.handle_jsonrpc(request)  # type: ignore[attr-defined]
        self._send_json(response)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _SSEServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], mcp_server: MCPServer) -> None:
        super().__init__(server_address, _Handler)
        self.mcp_server = mcp_server
        self.mcp_events = mcp_server.events


def run_sse(server: MCPServer, host: str, port: int) -> int:
    server.start()
    httpd = _SSEServer((host, int(port)), server)

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        server.stop()
    return 0
