"""Supervisor for ableton-bridge child process."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

from ..constants import (
    BRIDGE_HEARTBEAT_INTERVAL_SEC,
    BRIDGE_HEARTBEAT_MISSES_BEFORE_RESTART,
    BRIDGE_RESTART_BUDGET_COUNT,
    BRIDGE_RESTART_BUDGET_WINDOW_SEC,
    BRIDGE_RESTART_CIRCUIT_BREAK_SEC,
    DEFAULT_BRIDGE_SOCKET_PATH,
    DEFAULT_GATEWAY_HOST,
    DEFAULT_GATEWAY_PORT,
)
from .bridge_client import BridgeClient


@dataclass
class SupervisorStatus:
    running: bool
    pid: Optional[int]
    missed_heartbeats: int
    restart_count_window: int
    circuit_break_until_epoch: float


class BridgeSupervisor:
    def __init__(
        self,
        *,
        bridge_client: BridgeClient,
        socket_path: str = DEFAULT_BRIDGE_SOCKET_PATH,
        gateway_host: str = DEFAULT_GATEWAY_HOST,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
    ) -> None:
        self._client = bridge_client
        self._socket_path = socket_path
        self._gateway_host = gateway_host
        self._gateway_port = gateway_port

        self._process: Optional[subprocess.Popen[str]] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._missed_heartbeats = 0
        self._restart_events: Deque[float] = deque()
        self._circuit_break_until = 0.0

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._spawn_bridge()
        self._thread = threading.Thread(target=self._loop, name="BridgeSupervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._terminate_bridge()

    def status(self) -> SupervisorStatus:
        with self._lock:
            now = time.time()
            self._prune_restart_window(now)
            pid = self._process.pid if self._process else None
            running = bool(self._process and self._process.poll() is None)
            return SupervisorStatus(
                running=running,
                pid=pid,
                missed_heartbeats=self._missed_heartbeats,
                restart_count_window=len(self._restart_events),
                circuit_break_until_epoch=self._circuit_break_until,
            )

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return

            now = time.time()
            if self._circuit_break_until > now:
                time.sleep(1.0)
                continue

            alive = self._client.ping()
            if alive:
                with self._lock:
                    self._missed_heartbeats = 0
            else:
                with self._lock:
                    self._missed_heartbeats += 1
                    misses = self._missed_heartbeats
                if misses >= BRIDGE_HEARTBEAT_MISSES_BEFORE_RESTART:
                    self._restart_bridge()

            proc = self._process
            if proc and proc.poll() is not None:
                self._restart_bridge()

            time.sleep(BRIDGE_HEARTBEAT_INTERVAL_SEC)

    def _spawn_bridge(self) -> None:
        args = [
            sys.executable,
            "-m",
            "ableton_chain_mcp.bridge.main",
            "--socket-path",
            self._socket_path,
            "--gateway-host",
            self._gateway_host,
            "--gateway-port",
            str(self._gateway_port),
        ]
        env = os.environ.copy()
        proc = subprocess.Popen(args, env=env)
        with self._lock:
            self._process = proc
            self._missed_heartbeats = 0

    def _terminate_bridge(self) -> None:
        proc = self._process
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._process = None

    def _restart_bridge(self) -> None:
        now = time.time()
        with self._lock:
            self._prune_restart_window(now)
            if len(self._restart_events) >= BRIDGE_RESTART_BUDGET_COUNT:
                self._circuit_break_until = now + BRIDGE_RESTART_CIRCUIT_BREAK_SEC
                self._missed_heartbeats = 0
                self._terminate_bridge()
                return
            self._restart_events.append(now)
            self._missed_heartbeats = 0

        self._terminate_bridge()
        time.sleep(0.2)
        self._spawn_bridge()

    def _prune_restart_window(self, now: float) -> None:
        cutoff = now - BRIDGE_RESTART_BUDGET_WINDOW_SEC
        while self._restart_events and self._restart_events[0] < cutoff:
            self._restart_events.popleft()
