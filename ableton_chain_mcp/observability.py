"""Minimal in-process metrics, tracing, and event streaming support."""

from __future__ import annotations

import queue
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List


class MetricsStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, float] = defaultdict(float)

    def inc(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += float(value)

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._counters)


class TraceStore:
    def __init__(self, max_traces: int = 2000) -> None:
        self._max_traces = max_traces
        self._lock = threading.Lock()
        self._spans: List[Dict[str, Any]] = []

    def add_span(self, *, correlation_id: str, name: str, start_ms: float, end_ms: float, attrs: Dict[str, Any]) -> None:
        span = {
            "correlation_id": correlation_id,
            "name": name,
            "start_ms": float(start_ms),
            "end_ms": float(end_ms),
            "duration_ms": float(max(0.0, end_ms - start_ms)),
            "attrs": dict(attrs),
        }
        with self._lock:
            self._spans.append(span)
            if len(self._spans) > self._max_traces:
                self._spans = self._spans[-self._max_traces :]

    def list_by_correlation(self, correlation_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [s for s in self._spans if s["correlation_id"] == correlation_id]


class SpanTimer:
    def __init__(self, trace_store: TraceStore, correlation_id: str, name: str, **attrs: Any) -> None:
        self._store = trace_store
        self._correlation_id = correlation_id
        self._name = name
        self._attrs = attrs
        self._start = 0.0

    def __enter__(self) -> "SpanTimer":
        self._start = time.perf_counter() * 1000.0
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        end = time.perf_counter() * 1000.0
        attrs = dict(self._attrs)
        if exc is not None:
            attrs["exception"] = str(exc)
        self._store.add_span(
            correlation_id=self._correlation_id,
            name=self._name,
            start_ms=self._start,
            end_ms=end,
            attrs=attrs,
        )


class EventStream:
    """Best-effort pub/sub queue for SSE clients."""

    def __init__(self, maxsize: int = 200) -> None:
        self._lock = threading.Lock()
        self._subs: List[queue.Queue[Dict[str, Any]]] = []
        self._maxsize = maxsize

    def subscribe(self) -> queue.Queue[Dict[str, Any]]:
        q: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[Dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: Dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subs)
        for q in subscribers:
            try:
                q.put_nowait(dict(event))
            except queue.Full:
                # Drop oldest event to keep stream alive under backpressure.
                try:
                    _ = q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(dict(event))
                except queue.Full:
                    pass
