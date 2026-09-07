from __future__ import annotations

import threading
from collections.abc import Callable


class TurnCancelled(RuntimeError):
    """Internal control flow for a user-cancelled turn."""


class CancellationToken:
    """Share one idempotent cancellation signal across a turn."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next_key = 0
        self._reason = "Turn cancelled"

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def checkpoint(self) -> None:
        if self.cancelled:
            raise TurnCancelled(self.reason)

    def register(self, callback: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            if self._event.is_set():
                run_now = True
                key = -1
            else:
                run_now = False
                key = self._next_key
                self._next_key += 1
                self._callbacks[key] = callback
        if run_now:
            callback()

        def unregister() -> None:
            with self._lock:
                self._callbacks.pop(key, None)

        return unregister

    def cancel(self, reason: str = "Turn cancelled") -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = str(reason)[:500] or "Turn cancelled"
            self._event.set()
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                continue
        return True
