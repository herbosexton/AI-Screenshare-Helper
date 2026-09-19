"""Upload/download helpers. Uploads must pass FilePathPolicy. Downloads are never executed."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.agent.browser.errors import DOWNLOAD_FAILED, UPLOAD_FAILED, BrowserError
from src.agent.files.permissions import FilePathPolicy, PathAccessError

EXECUTABLE_SUFFIXES = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".scr", ".com", ".js", ".vbs"}


class BrowserFileIO:
    def __init__(self, policy: Optional[FilePathPolicy], downloads_dir: Path):
        self.policy = policy
        self.downloads_dir = Path(downloads_dir)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.recent: list[dict[str, Any]] = []

    def authorize_upload(self, path: str) -> Path:
        if self.policy is None:
            p = Path(path)
            if not p.is_file():
                raise BrowserError(f"Upload file not found: {path}", UPLOAD_FAILED, retryable=False)
            return p
        try:
            resolved = self.policy.check(path, must_exist=True)
        except PathAccessError as e:
            raise BrowserError(str(e), UPLOAD_FAILED, retryable=False) from e
        if not resolved.is_file():
            raise BrowserError(f"Upload path is not a file: {resolved}", UPLOAD_FAILED, retryable=False)
        return resolved

    def record_download(self, *, filename: str, source_url: str, dest: Path, size: int, status: str) -> dict[str, Any]:
        record = {
            "filename": filename,
            "source_url": source_url,
            "destination": str(dest),
            "size": size,
            "status": status,
            "executed": False,
        }
        self.recent.append(record)
        return record

    def is_executable(self, path: Path) -> bool:
        return path.suffix.lower() in EXECUTABLE_SUFFIXES
