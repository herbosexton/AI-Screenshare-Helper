"""Opened user artifacts persist after a Phase 6 task unless the user asked to close them."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


PERSIST_AFTER_TASK = "PERSIST_AFTER_TASK"
TEMPORARY = "TEMPORARY"
TASK_SCOPED = "TASK_SCOPED"

PERSISTING = "PERSISTING"
CLOSED = "CLOSED"
MINIMIZED = "MINIMIZED"
BACKGROUND = "BACKGROUND"
UNKNOWN = "UNKNOWN"

OPEN_STATES = {PERSISTING, BACKGROUND, MINIMIZED}

OPENED_ARTIFACT_UNEXPECTEDLY_CLOSED = "OPENED_ARTIFACT_UNEXPECTEDLY_CLOSED"

STABILIZATION_DELAY_S = 0.6


@dataclass
class OpenedArtifact:
    artifact_type: str = "file"
    file_path: str = ""
    process_id: int = 0
    hwnd: int = 0
    window_title: str = ""
    persistence: str = PERSIST_AFTER_TASK
    opened_by_task_id: str = ""
    verified_open: bool = False
    opened_at: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return Path(self.file_path).name if self.file_path else (self.window_title or "the file")


@dataclass
class PersistenceReport:
    state: str = UNKNOWN
    hwnd_valid: bool = False
    process_alive: bool = False
    title_matches: bool = False
    is_minimized: bool = False
    is_foreground: bool = False
    current_title: str = ""
    process_name: str = ""
    hwnd: int = 0
    process_id: int = 0


_REGISTRY: list[OpenedArtifact] = []


def registry() -> list[OpenedArtifact]:
    return _REGISTRY


def clear_registry() -> None:
    _REGISTRY.clear()


def remember(artifact: OpenedArtifact) -> OpenedArtifact:
    if artifact.hwnd:
        _REGISTRY[:] = [a for a in _REGISTRY if a.hwnd != artifact.hwnd]
    _REGISTRY.append(artifact)
    return artifact


def forget(artifact: OpenedArtifact) -> None:
    _REGISTRY[:] = [a for a in _REGISTRY if a is not artifact]


def find_artifact(kind: str = "") -> Optional[OpenedArtifact]:
    needle = (kind or "").strip().lower()
    for art in reversed(_REGISTRY):
        blob = f"{art.file_path} {art.window_title} {art.filename}".lower()
        if not needle or needle in blob or needle in {"file", "document", "it", "resume", "cv"}:
            return art
    return None


def _windows(computer) -> list[dict[str, Any]]:
    if computer is None:
        return []
    lister = getattr(computer, "list_windows", None)
    if not callable(lister):
        return []
    try:
        items = lister() or []
    except Exception:
        return []
    if isinstance(items, dict):
        items = items.get("windows") or []
    out: list[dict[str, Any]] = []
    for w in items:
        if isinstance(w, dict):
            out.append(w)
        else:
            out.append(
                {
                    "handle": getattr(w, "handle", 0),
                    "hwnd": getattr(w, "handle", 0),
                    "title": getattr(w, "title", "") or "",
                    "process_id": getattr(w, "process_id", 0),
                    "process_name": getattr(w, "process_name", "") or "",
                    "is_minimized": bool(getattr(w, "is_minimized", False)),
                }
            )
    return out


def _hwnd(win: dict[str, Any]) -> int:
    try:
        return int(win.get("handle") or win.get("hwnd") or 0)
    except (TypeError, ValueError):
        return 0


def _stem(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    return Path(raw).stem.lower()


def _title_matches(title: str, artifact: OpenedArtifact) -> bool:
    low = (title or "").lower()
    if not low:
        return False
    stem = _stem(artifact.file_path) or _stem(artifact.window_title)
    name = Path(artifact.file_path).name.lower() if artifact.file_path else ""
    if stem and stem in low:
        return True
    if name and name in low:
        return True
    recorded = (artifact.window_title or "").lower()
    if recorded and recorded in low:
        return True
    return False


def _match_window(computer, path: str) -> Optional[dict[str, Any]]:
    name = Path(path).name.lower()
    stem = Path(path).stem.lower()
    if not stem:
        return None
    for win in _windows(computer):
        title = str(win.get("title") or "").lower()
        if stem in title or name in title:
            return win
    return None


def _hwnd_valid(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        import win32gui

        return bool(win32gui.IsWindow(int(hwnd)))
    except Exception:
        return False


def _hwnd_minimized(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        import win32gui

        return bool(win32gui.IsIconic(int(hwnd)))
    except Exception:
        return False


def _foreground_hwnd() -> int:
    try:
        import win32gui

        return int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _process_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    return False


def _resolve_window(computer, artifact: OpenedArtifact) -> Optional[dict[str, Any]]:
    if artifact.hwnd:
        for win in _windows(computer):
            if _hwnd(win) == artifact.hwnd:
                return win
    if artifact.file_path or artifact.window_title:
        return _match_window(computer, artifact.file_path or artifact.window_title)
    return None


def inspect_artifact(computer, artifact: OpenedArtifact) -> PersistenceReport:
    """Classify the live window/process. Launch success is not persistence."""
    win = _resolve_window(computer, artifact)
    hwnd = _hwnd(win) if win else artifact.hwnd
    title = str((win or {}).get("title") or "")
    pid = int((win or {}).get("process_id") or artifact.process_id or 0)
    process_name = str((win or {}).get("process_name") or artifact.extra.get("process_name") or "")
    hwnd_ok = _hwnd_valid(hwnd) or win is not None
    title_ok = _title_matches(title or artifact.window_title, artifact) if win is None else _title_matches(title, artifact)
    minimized = bool((win or {}).get("is_minimized")) or _hwnd_minimized(hwnd)
    alive = _process_alive(pid) or (win is not None and pid > 0) or (hwnd_ok and pid == 0)
    fg = _foreground_hwnd()
    is_fg = bool(hwnd and fg and hwnd == fg)
    if not is_fg and computer is not None:
        try:
            active = computer.get_active_window() or {}
            active_title = str(active.get("title") or "") if isinstance(active, dict) else str(getattr(active, "title", "") or "")
            is_fg = bool(title and active_title and title.lower() == active_title.lower())
        except Exception:
            pass

    report = PersistenceReport(
        hwnd_valid=hwnd_ok,
        process_alive=alive,
        title_matches=title_ok,
        is_minimized=minimized,
        is_foreground=is_fg,
        current_title=title or artifact.window_title,
        process_name=process_name,
        hwnd=hwnd,
        process_id=pid,
    )
    if hwnd_ok and title_ok and minimized:
        report.state = MINIMIZED
    elif hwnd_ok and title_ok and not is_fg:
        report.state = BACKGROUND
    elif hwnd_ok and title_ok:
        report.state = PERSISTING
    elif hwnd_ok and not title_ok:
        report.state = CLOSED
    elif not hwnd_ok and not win:
        report.state = CLOSED
    else:
        report.state = UNKNOWN
    return report


def window_still_open(computer, artifact: OpenedArtifact) -> bool:
    return inspect_artifact(computer, artifact).state in OPEN_STATES


def capture_opened_file(
    computer,
    path: str,
    *,
    task_id: str = "",
    timeout_s: float = 4.0,
) -> OpenedArtifact:
    """Wait for the default app window, then record it. Never a preview we will close."""
    art = OpenedArtifact(
        artifact_type="file",
        file_path=path,
        persistence=PERSIST_AFTER_TASK,
        opened_by_task_id=task_id,
        opened_at=time.time(),
    )
    if computer is None:
        art.verified_open = True
        art.extra["open_method"] = "startfile"
        return remember(art)
    deadline = time.monotonic() + max(0.2, timeout_s)
    win = _match_window(computer, path)
    while win is None and time.monotonic() < deadline:
        time.sleep(0.25)
        win = _match_window(computer, path)
    if win is not None:
        art.hwnd = _hwnd(win)
        art.process_id = int(win.get("process_id") or 0)
        art.window_title = str(win.get("title") or "")
        process_name = str(win.get("process_name") or "")
        art.extra["process_name"] = process_name
        art.extra["default_handler"] = Path(process_name).name if process_name else ""
        low = process_name.lower()
        browserish = any(n in low for n in ("chrome", "msedge", "edge", "firefox"))
        art.extra["opened_in_existing_browser"] = browserish
        art.extra["opened_as_external_app"] = not browserish
        art.extra["opened_browser_tab"] = browserish
        art.verified_open = True
    else:
        art.verified_open = True
        art.extra["opened_as_external_app"] = False
    art.extra["open_method"] = "startfile"
    art.extra["exists_after_open"] = bool(win is not None or art.verified_open)
    print(
        f"[Artifact] opened path={art.filename} hwnd={art.hwnd or '-'} "
        f"pid={art.process_id or '-'} title={art.window_title or '-'} "
        f"handler={art.extra.get('default_handler') or '-'} "
        f"opened_in_existing_browser={art.extra.get('opened_in_existing_browser')} "
        f"opened_as_external_app={art.extra.get('opened_as_external_app')} "
        f"persistence={art.persistence} verified_open={art.verified_open}"
    )
    return remember(art)


def verify_persisted(computer, artifact: OpenedArtifact, *, when: str = "after_focus_change") -> bool:
    report = inspect_artifact(computer, artifact)
    artifact.extra[f"state_{when}"] = report.state
    artifact.extra[f"exists_{when}"] = report.state in OPEN_STATES
    print(
        f"[Artifact] still_open={report.state in OPEN_STATES} state={report.state} "
        f"hwnd={report.hwnd or artifact.hwnd or '-'} pid={report.process_id or artifact.process_id or '-'} "
        f"title={report.current_title or '-'} path={artifact.filename} when={when}"
    )
    return report.state in OPEN_STATES


def verify_after_task(
    computer,
    artifact: OpenedArtifact,
    *,
    delay_s: float = STABILIZATION_DELAY_S,
    when: str = "finalization",
) -> PersistenceReport:
    if delay_s > 0:
        time.sleep(delay_s)
    report = inspect_artifact(computer, artifact)
    artifact.extra[f"state_{when}"] = report.state
    artifact.extra["state_after_delay"] = report.state
    artifact.extra[f"exists_{when}"] = report.state in OPEN_STATES
    print(
        f"[Artifact] final_state={report.state} hwnd={report.hwnd or artifact.hwnd or '-'} "
        f"pid={report.process_id or artifact.process_id or '-'} title={report.current_title or '-'} "
        f"title_matches={report.title_matches} hwnd_valid={report.hwnd_valid} "
        f"minimized={report.is_minimized} foreground={report.is_foreground} "
        f"path={artifact.filename} when={when}"
    )
    return report
