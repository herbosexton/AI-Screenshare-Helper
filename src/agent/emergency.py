from __future__ import annotations

import threading
from typing import Callable, Optional


class EmergencyStop:
    """
    Global kill switch for automation.
    When engaged: stop further tool execution; preserve task state as paused.
    """

    def __init__(self):
        self._engaged = threading.Event()
        self._listeners: list[Callable[[], None]] = []

    @property
    def is_engaged(self) -> bool:
        return self._engaged.is_set()

    def engage(self, reason: str = "user") -> None:
        print(f"[EmergencyStop] ENGAGED ({reason})")
        self._engaged.set()
        for listener in list(self._listeners):
            try:
                listener()
            except Exception as e:
                print(f"[EmergencyStop] listener error: {e}")

    def clear(self) -> None:
        print("[EmergencyStop] Cleared")
        self._engaged.clear()

    def check(self) -> None:
        if self._engaged.is_set():
            raise RuntimeError("Emergency stop engaged — tool execution blocked")

    def on_engage(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)


# Process-wide singleton used by hotkeys and orchestrator
GLOBAL_EMERGENCY_STOP = EmergencyStop()
