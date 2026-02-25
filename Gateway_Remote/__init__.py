"""
Ableton Gateway Remote Script
=============================

This Remote Script exposes the `LiveAPITools` command set over a lightweight
TCP gateway so any external client can issue JSON commands to Ableton Live.
It keeps the gateway logic isolated so you can tailor transport/authorization
independently from the original project.

Protocol
--------
- Transport: TCP (newline-delimited JSON)
- Host: 127.0.0.1
- Port: 8001 (configurable via constructor kwargs)
- Request schema: {"action": "<tool_name>", ...params}
- Response schema: {"ok": bool, ...payload}
"""

try:
    import Live  # type: ignore
except ModuleNotFoundError:
    Live = None
import json
import socket
import threading
import traceback
import inspect

try:
    import Queue as queue  # Python 2 fallback
except ImportError:
    import queue

from .liveapi_tools import LiveAPITools
from .action_registry import build_registry
from .action_validation import validate_payload

try:
    JSONDecodeError = json.JSONDecodeError
except AttributeError:  # Python 2 compatibility
    JSONDecodeError = ValueError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001
SOCKET_TIMEOUT = 30.0
COMMAND_TIMEOUT = 25.0

ERROR_INVALID_PARAMS = "ERR_INVALID_PARAMS"
ERROR_NOT_FOUND = "ERR_NOT_FOUND"
ERROR_EXECUTION_FAILED = "ERR_EXECUTION_FAILED"
BUILTIN_ACTIONS = ("ping", "health_check", "list_tools", "get_available_tools")


class GatewayRemote:
    """
    Standalone gateway that routes JSON commands to LiveAPITools.

    The socket listener runs on a worker thread, while Ableton invokes
    `update_display()` on the main thread. We leverage a pair of queues
    (command + response) to keep all Live API calls on the main thread.
    """

    def __init__(self, c_instance, host=DEFAULT_HOST, port=DEFAULT_PORT):
        self.c_instance = c_instance
        self.song = c_instance.song()

        self.host = host
        self.port = port

        self.tools = LiveAPITools(self.song, self.c_instance)
        self.action_registry = build_registry(self.tools)

        self.command_queue = queue.Queue()
        self.response_queues = {}
        self.request_lock = threading.Lock()
        self.request_counter = 0

        self.server_socket = None
        self.listener_thread = None
        self.running = False

        self._start_gateway()
        self.log("GatewayRemote initialized on {}:{}".format(self.host, self.port))

    # --------------------------------------------------------------------- #
    # Ableton logging helper
    # --------------------------------------------------------------------- #
    def log(self, message):
        """Write a namespaced entry to Ableton's Log.txt"""
        try:
            self.c_instance.log_message("[GatewayRemote] {}".format(message))
        except Exception:
            # During shutdown Ableton might already dispose the logger
            pass

    # --------------------------------------------------------------------- #
    # Socket server lifecycle
    # --------------------------------------------------------------------- #
    def _start_gateway(self):
        """Create the TCP listener and spawn its background thread."""
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)

            self.running = True
            self.listener_thread = threading.Thread(
                target=self._socket_listener, name="GatewayRemoteListener", daemon=True
            )
            self.listener_thread.start()
        except Exception as exc:
            self.log("Failed to start gateway: {}".format(exc))
            self.log(traceback.format_exc())
            raise

    def _socket_listener(self):
        """Accept incoming client connections."""
        while self.running:
            try:
                client_socket, address = self.server_socket.accept()
                self.log("Client connected from {}".format(address))
                handler = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket,),
                    name="GatewayRemoteClient",
                    daemon=True,
                )
                handler.start()
            except OSError:
                # Socket closed as part of shutdown sequence
                break
            except Exception as exc:
                if self.running:
                    self.log("Listener error: {}".format(exc))

    def _handle_client(self, client_socket):
        """Read newline-delimited JSON messages from a socket."""
        buffer = ""
        try:
            client_socket.settimeout(SOCKET_TIMEOUT)
            while self.running:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break

                buffer += chunk.decode("utf-8")

                while "\n" in buffer:
                    raw_message, buffer = buffer.split("\n", 1)
                    raw_message = raw_message.strip()
                    if not raw_message:
                        continue

                    response = self._enqueue_command(raw_message)
                    client_socket.sendall((json.dumps(response) + "\n").encode("utf-8"))

        except socket.timeout:
            pass
        except Exception as exc:
            self.log("Client handler error: {}".format(exc))
        finally:
            try:
                client_socket.close()
            except Exception:
                pass

    def _enqueue_command(self, raw_message):
        """Parse a JSON command and wait for the response from the main thread."""
        try:
            command = json.loads(raw_message)
        except JSONDecodeError as exc:
            return {
                "ok": False,
                "error_code": ERROR_INVALID_PARAMS,
                "error": "Invalid JSON: {}".format(exc),
            }

        request_id, response_queue = self._register_request()
        self.command_queue.put((request_id, command))

        try:
            response = response_queue.get(timeout=COMMAND_TIMEOUT)
        except queue.Empty:
            response = {
                "ok": False,
                "error_code": ERROR_EXECUTION_FAILED,
                "error": "Gateway timeout while waiting for Ableton main thread",
            }
        finally:
            self._release_request(request_id)

        return response

    def _register_request(self):
        """Reserve a response queue for one request."""
        with self.request_lock:
            request_id = self.request_counter
            self.request_counter += 1
            response_queue = queue.Queue()
            self.response_queues[request_id] = response_queue
            return request_id, response_queue

    def _release_request(self, request_id):
        """Remove the response queue once a response is delivered."""
        with self.request_lock:
            self.response_queues.pop(request_id, None)

    # --------------------------------------------------------------------- #
    # Command routing
    # --------------------------------------------------------------------- #
    def _route_command(self, command):
        """
        Dispatch a parsed command dict.

        Built-in actions are handled here, and external actions dispatch through
        an explicit action registry.
        """
        if not isinstance(command, dict):
            return {
                "ok": False,
                "error_code": ERROR_INVALID_PARAMS,
                "error": "Action payload must be a JSON object",
            }

        action = command.get("action")
        if not action:
            return {
                "ok": False,
                "error_code": ERROR_INVALID_PARAMS,
                "error": "Missing 'action' field",
            }

        action = str(action).strip()
        if not action:
            return {
                "ok": False,
                "error_code": ERROR_INVALID_PARAMS,
                "error": "Action must be a non-empty string",
            }

        if action == "ping":
            return {
                "ok": True,
                "message": "GatewayRemote online",
                "script": "Gateway_Remote",
            }

        if action == "health_check":
            return self._health_payload()

        if action in ("list_tools", "get_available_tools"):
            return {"ok": True, "tools": self._available_actions()}

        return self._invoke_registry_action(action, command)

    def _health_payload(self):
        """Gather runtime information for diagnostics."""
        try:
            app = Live.Application.get_application()
            ableton_version = "{}".format(app.get_major_version())
        except Exception:
            ableton_version = "unknown"

        return {
            "ok": True,
            "message": "GatewayRemote running",
            "tool_count": len(self.action_registry),
            "queue_depth": self.command_queue.qsize(),
            "ableton_version": ableton_version,
        }

    def _invoke_registry_action(self, action, command):
        """Call a registered LiveAPI action with payload validation."""
        spec = self.action_registry.get(action)
        if spec is None:
            return {
                "ok": False,
                "error_code": ERROR_NOT_FOUND,
                "error": "Unknown action '{}'".format(action),
                "available_actions": self._available_actions(),
            }

        validation_error = validate_payload(spec, command)
        if validation_error:
            return validation_error

        kwargs = {k: v for k, v in command.items() if k != "action"}

        try:
            response = spec.handler(**kwargs)
        except TypeError as exc:
            return {
                "ok": False,
                "error_code": ERROR_INVALID_PARAMS,
                "error": "Invalid parameters for '{}': {}".format(action, exc),
                "expected_signature": self._tool_signature(spec.handler),
            }
        except Exception as exc:
            self.log("Tool '{}' raised: {}".format(action, exc))
            self.log(traceback.format_exc())
            return {
                "ok": False,
                "error_code": ERROR_EXECUTION_FAILED,
                "error": str(exc),
            }

        return self._normalize_action_response(action, response)

    def _normalize_action_response(self, action, response):
        """Ensure every action response conforms to FlowState's error envelope."""
        if isinstance(response, dict):
            normalized = dict(response)
        else:
            normalized = {"ok": True, "result": response}

        if "ok" not in normalized:
            normalized["ok"] = True

        if normalized.get("ok") is False:
            if not normalized.get("error"):
                normalized["error"] = normalized.get(
                    "message", "Action '{}' failed".format(action)
                )
            normalized.setdefault("error_code", ERROR_EXECUTION_FAILED)
        return normalized

    def _available_actions(self):
        actions = set(BUILTIN_ACTIONS)
        actions.update(self.action_registry.keys())
        return sorted(actions)

    # --------------------------------------------------------------------- #
    # Ableton Remote Script hooks
    # --------------------------------------------------------------------- #
    def update_display(self):
        """
        Called once per frame on Ableton's main thread.
        We process a small batch of queued commands here.
        """
        processed = 0
        max_per_tick = 8

        while processed < max_per_tick:
            try:
                request_id, command = self.command_queue.get_nowait()
            except queue.Empty:
                break

            response = self._route_command(command)
            response_queue = self.response_queues.get(request_id)
            if response_queue:
                response_queue.put(response)
            processed += 1

    def connect_script_instances(self, instantiated_scripts):
        """Required by Ableton Remote Script API."""
        return

    def can_lock_to_devices(self):
        """This script doesn't lock to devices."""
        return False

    def refresh_state(self):
        """Called when Live refreshes the control surface."""
        return

    def build_midi_map(self, midi_map_handle):
        """Not used – gateway is network control only."""
        return

    def disconnect(self):
        """Stop the TCP server when Ableton unloads the script."""
        self.log("Shutting down GatewayRemote")
        self.running = False

        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass

        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=1.0)

        self.log("GatewayRemote stopped")

    def _tool_signature(self, tool):
        """Best-effort textual representation of a callable signature."""
        try:
            return str(inspect.signature(tool))
        except Exception:
            try:
                return str(inspect.getfullargspec(tool))
            except Exception:
                return "unknown"


def create_instance(c_instance):
    """Ableton entry point."""
    return GatewayRemote(c_instance)
