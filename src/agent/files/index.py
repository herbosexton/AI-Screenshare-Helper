"""Local SQLite FTS5 document index. Incremental by mtime/size/hash."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.agent.files.extractors import extract_text
from src.agent.files.permissions import FilePathPolicy, PathAccessError, is_secret_path


def _file_hash(path: Path, max_bytes: int = 8_000_000) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        remaining = max_bytes
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


class DocumentIndex:
    def __init__(self, db_path: Path, policy: FilePathPolicy, max_text_chars: int = 50000):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self.max_text_chars = max_text_chars
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    path TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    extension TEXT,
                    created REAL,
                    modified REAL,
                    size INTEGER,
                    content_hash TEXT,
                    extracted_text TEXT,
                    indexed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
                USING fts5(name, extracted_text, path UNINDEXED)
                """
            )
            conn.commit()

    def index_path(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {"ok": False, "path": str(path), "error": "Not a file"}
        if is_secret_path(path) or self.policy.is_blocked(path):
            return {"ok": False, "path": str(path), "error": "Blocked path"}
        if not self.policy.is_allowed_root(path):
            return {"ok": False, "path": str(path), "error": "Outside allowed directories"}

        stat = path.stat()
        digest = _file_hash(path)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT modified, size, content_hash FROM documents WHERE path = ?",
                (str(path),),
            ).fetchone()
            if (
                row
                and abs(row["modified"] - stat.st_mtime) < 0.01
                and row["size"] == stat.st_size
                and row["content_hash"] == digest
            ):
                return {"ok": True, "path": str(path), "skipped": True, "reason": "unchanged"}

        extracted = extract_text(path, max_chars=self.max_text_chars)
        text = extracted.get("text") or ""
        created = getattr(stat, "st_ctime", stat.st_mtime)
        indexed_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (path, name, extension, created, modified, size, content_hash, extracted_text, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    name=excluded.name,
                    extension=excluded.extension,
                    created=excluded.created,
                    modified=excluded.modified,
                    size=excluded.size,
                    content_hash=excluded.content_hash,
                    extracted_text=excluded.extracted_text,
                    indexed_at=excluded.indexed_at
                """,
                (
                    str(path),
                    path.name,
                    path.suffix.lower(),
                    created,
                    stat.st_mtime,
                    stat.st_size,
                    digest,
                    text,
                    indexed_at,
                ),
            )
            conn.execute("DELETE FROM documents_fts WHERE path = ?", (str(path),))
            conn.execute(
                "INSERT INTO documents_fts (name, extracted_text, path) VALUES (?, ?, ?)",
                (path.name, text, str(path)),
            )
            conn.commit()
        return {
            "ok": True,
            "path": str(path),
            "skipped": False,
            "extractor": extracted.get("extractor"),
            "chars": len(text),
        }

    def index_roots(self, roots: Optional[list[Path]] = None, limit: int = 5000) -> dict[str, Any]:
        roots = roots or list(self.policy.allowed_roots)
        indexed = 0
        skipped = 0
        errors = 0
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if indexed + skipped + errors >= limit:
                    break
                if not path.is_file():
                    continue
                try:
                    self.policy.check(str(path), write=False)
                except PathAccessError:
                    continue
                result = self.index_path(path)
                if not result.get("ok"):
                    errors += 1
                elif result.get("skipped"):
                    skipped += 1
                else:
                    indexed += 1
        return {"ok": True, "indexed": indexed, "skipped": skipped, "errors": errors}

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        # FTS5: quote tokens so punctuation/spaces don't break MATCH
        tokens = [t for t in q.replace('"', " ").split() if t]
        match_q = " ".join(f'"{t}"' for t in tokens) if tokens else q
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT d.path, d.name, d.extension, d.modified, d.size,
                           snippet(documents_fts, 1, '[', ']', '…', 12) AS snippet
                    FROM documents_fts
                    JOIN documents d ON d.path = documents_fts.path
                    WHERE documents_fts MATCH ?
                    LIMIT ?
                    """,
                    (match_q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                like = f"%{q}%"
                rows = conn.execute(
                    """
                    SELECT path, name, extension, modified, size,
                           substr(extracted_text, 1, 160) AS snippet
                    FROM documents
                    WHERE name LIKE ? OR extracted_text LIKE ?
                    LIMIT ?
                    """,
                    (like, like, limit),
                ).fetchall()
        return [dict(r) for r in rows]
