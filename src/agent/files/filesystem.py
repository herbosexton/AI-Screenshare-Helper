"""Authorized local filesystem operations."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from src.agent.files.extractors import IMAGE_EXTENSIONS, extract_text
from src.agent.files.index import DocumentIndex
from src.agent.files.permissions import FilePathPolicy, PathAccessError

SKIP_EXTRACT_EXTENSIONS = IMAGE_EXTENSIONS | {
    ".exe",
    ".dll",
    ".zip",
    ".7z",
    ".rar",
    ".gz",
    ".mp4",
    ".mp3",
    ".wav",
    ".iso",
    ".bin",
    ".pyc",
    ".pyd",
    ".so",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".db",
    ".sqlite",
}
FALLBACK_MAX_BYTES = 2_000_000
FALLBACK_MAX_CHARS = 8000
FALLBACK_MAX_FILES = 400
_RESUME_NAME = re.compile(r"resume|r[eé]sum[eé]|\bcv\b|curriculum", re.I)


def _looks_like_resume_query(needle: str) -> bool:
    return bool(_RESUME_NAME.search(needle or ""))


def _resume_name_score(name: str) -> int:
    return 1 if _RESUME_NAME.search(name or "") else 0


class FileSystemService:
    def __init__(
        self,
        policy: FilePathPolicy,
        index: Optional[DocumentIndex] = None,
        max_read_bytes: int = 2_000_000,
    ):
        self.policy = policy
        self.index = index
        self.max_read_bytes = max_read_bytes

    def list_dir(self, directory: str, limit: int = 200) -> dict[str, Any]:
        path = self.policy.check(directory, must_exist=True)
        if not path.is_dir():
            raise PathAccessError(f"Not a directory: {path}")
        entries = []
        for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
            if len(entries) >= limit:
                break
            try:
                if self.policy.is_blocked(child) or not self.policy.is_allowed_root(child):
                    continue
                stat = child.stat()
                entries.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "is_dir": child.is_dir(),
                        "size": stat.st_size if child.is_file() else None,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )
            except OSError:
                continue
        return {"directory": str(path), "entries": entries, "count": len(entries)}

    def find_by_name(
        self,
        name_contains: str,
        *,
        extensions: Optional[list[str]] = None,
        limit: int = 50,
        roots: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        needle = (name_contains or "").lower()
        exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (extensions or [])}
        matches: list[dict[str, Any]] = []
        for root in self._iter_roots(roots):
            for path in root.rglob("*"):
                if len(matches) >= limit:
                    break
                if not path.is_file():
                    continue
                if needle and needle not in path.name.lower():
                    continue
                if exts and path.suffix.lower() not in exts:
                    continue
                try:
                    self.policy.check(str(path))
                except PathAccessError:
                    continue
                matches.append(self._meta(path))
        matches.sort(key=lambda m: m.get("modified") or "", reverse=True)
        return {"query": name_contains, "matches": matches, "count": len(matches)}

    def find_recent(
        self,
        *,
        name_contains: str = "",
        extensions: Optional[list[str]] = None,
        since_days: Optional[float] = None,
        limit: int = 20,
        roots: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        cutoff = None
        # since_days=0 reads as "no age limit", never as "modified after right now",
        # which would filter out every file on disk.
        if since_days is not None and float(since_days) > 0:
            cutoff = datetime.now() - timedelta(days=float(since_days))
        needle = (name_contains or "").lower()
        exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (extensions or [])}
        found: list[dict[str, Any]] = []
        for root in self._iter_roots(roots):
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if needle and needle not in path.name.lower():
                    continue
                if exts and path.suffix.lower() not in exts:
                    continue
                try:
                    self.policy.check(str(path))
                    stat = path.stat()
                except (PathAccessError, OSError):
                    continue
                modified = datetime.fromtimestamp(stat.st_mtime)
                if cutoff and modified < cutoff:
                    continue
                found.append(self._meta(path, stat=stat))
        found.sort(key=lambda m: m.get("modified") or "", reverse=True)
        if _looks_like_resume_query(needle):
            relevant = [m for m in found if _resume_name_score(str(m.get("name") or m.get("path") or ""))]
            if relevant:
                relevant.sort(key=lambda m: m.get("modified") or "", reverse=True)
                found = relevant + [m for m in found if m not in relevant]
        return {"matches": found[:limit], "count": min(len(found), limit)}

    def get_metadata(self, path: str) -> dict[str, Any]:
        p = self.policy.check(path, must_exist=True)
        return self._meta(p, include_hash=True)

    def read(self, path: str) -> dict[str, Any]:
        p = self.policy.check(path, must_exist=True)
        if not p.is_file():
            raise PathAccessError(f"Not a file: {p}")
        extracted = extract_text(p, max_chars=self.max_read_bytes)
        text = extracted.get("text") or ""
        if len(text.encode("utf-8", errors="replace")) > self.max_read_bytes:
            text = text[: self.max_read_bytes]
        return {
            "path": str(p),
            "extractor": extracted.get("extractor"),
            "ok": extracted.get("ok", False),
            "error": extracted.get("error"),
            "text": text,
            "untrusted": True,
        }

    def extract(self, path: str) -> dict[str, Any]:
        return self.read(path)

    def open_file(self, path: str) -> dict[str, Any]:
        p = self.policy.check(path, must_exist=True)
        os.startfile(str(p))  # noqa: S606
        return {
            "opened": str(p),
            "path": str(p),
            "filename": p.name,
            "persistence": "PERSIST_AFTER_TASK",
        }

    def copy(self, source: str, destination: str) -> dict[str, Any]:
        src = self.policy.check(source, must_exist=True)
        dest = self.policy.check(destination, write=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return {"copied": str(src), "to": str(dest)}

    def move(self, source: str, destination: str) -> dict[str, Any]:
        src = self.policy.check(source, write=True, must_exist=True)
        dest = self.policy.check(destination, write=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return {"moved": str(src), "to": str(dest)}

    def rename(self, path: str, new_name: str) -> dict[str, Any]:
        src = self.policy.check(path, write=True, must_exist=True)
        dest = src.with_name(new_name)
        dest = self.policy.check(str(dest), write=True)
        src.rename(dest)
        return {"renamed": str(src), "to": str(dest)}

    def create_directory(self, path: str) -> dict[str, Any]:
        dest = self.policy.check(path, write=True)
        dest.mkdir(parents=True, exist_ok=True)
        return {"created": str(dest)}

    def delete(self, path: str) -> dict[str, Any]:
        p = self.policy.check(path, write=True, must_exist=True)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"deleted": str(p)}

    def overwrite(self, path: str, content: str) -> dict[str, Any]:
        p = self.policy.check(path, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"overwritten": str(p), "bytes": len(content.encode("utf-8"))}

    def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        hits: list[dict[str, Any]] = []
        if self.index is not None:
            hits = self.index.search(query, limit=limit)
        if hits:
            return {"query": query, "source": "index", "matches": hits, "count": len(hits)}
        matches = self._walk_extract_search(query, limit=limit)
        return {
            "query": query,
            "source": "walk_extract",
            "matches": matches,
            "count": len(matches),
        }

    def _walk_extract_search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        needle = (query or "").strip().lower()
        if not needle:
            return []
        matches: list[dict[str, Any]] = []
        scanned = 0
        for root in self._iter_roots(None):
            for path in root.rglob("*"):
                if len(matches) >= limit or scanned >= FALLBACK_MAX_FILES:
                    return matches
                if not path.is_file():
                    continue
                if path.suffix.lower() in SKIP_EXTRACT_EXTENSIONS:
                    continue
                try:
                    self.policy.check(str(path))
                    stat = path.stat()
                except (PathAccessError, OSError):
                    continue
                if stat.st_size > FALLBACK_MAX_BYTES:
                    continue
                scanned += 1
                name_hit = needle in path.name.lower()
                snippet = ""
                if not name_hit:
                    extracted = extract_text(path, max_chars=FALLBACK_MAX_CHARS)
                    text = (extracted.get("text") or "")
                    if needle not in text.lower():
                        continue
                    idx = text.lower().find(needle)
                    start = max(0, idx - 40)
                    snippet = text[start : start + 120]
                else:
                    snippet = path.name
                meta = self._meta(path, stat=stat, include_hash=False)
                meta["snippet"] = snippet
                matches.append(meta)
        return matches

    def index_now(self, path: Optional[str] = None) -> dict[str, Any]:
        if self.index is None:
            raise PathAccessError("Document index is not available")
        if path:
            p = self.policy.check(path, must_exist=True)
            if p.is_dir():
                return self.index.index_roots([p])
            return self.index.index_path(p)
        return self.index.index_roots()

    def _iter_roots(self, roots: Optional[list[str]]) -> list[Path]:
        if roots:
            resolved = []
            for r in roots:
                try:
                    resolved.append(self.policy.check(r, must_exist=True))
                except PathAccessError:
                    continue
            return resolved
        return [r for r in self.policy.allowed_roots if r.exists()]

    @staticmethod
    def _hash_prefix(path: Path, max_bytes: int = 8_000_000) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            remaining = max_bytes
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
        return digest.hexdigest()[:12]

    def _meta(self, path: Path, stat=None, *, include_hash: bool = False) -> dict[str, Any]:
        stat = stat or path.stat()
        created = getattr(stat, "st_ctime", stat.st_mtime)
        payload = {
            "path": str(path),
            "name": path.name,
            "extension": path.suffix.lower(),
            "size": stat.st_size,
            "created": datetime.fromtimestamp(created).isoformat(),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
        if include_hash:
            try:
                payload["hash_prefix"] = self._hash_prefix(path)
            except OSError:
                payload["hash_prefix"] = ""
        return payload
