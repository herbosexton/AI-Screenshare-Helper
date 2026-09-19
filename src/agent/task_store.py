from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from src.agent.models.task import Task, TaskStatus


class TaskStore:
    """SQLite-backed persistent task store."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save(self, task: Task) -> Task:
        task.touch()
        payload = task.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, payload, status, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload=excluded.payload,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (task.id, payload, task.status.value, task.updated_at.isoformat()),
            )
            conn.commit()
        return task

    def get(self, task_id: str) -> Optional[Task]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if not row:
            return None
        return Task.model_validate_json(row["payload"])

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 50,
    ) -> list[Task]:
        query = "SELECT payload FROM tasks"
        params: list[object] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status.value)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [Task.model_validate_json(r["payload"]) for r in rows]

    def get_active(self) -> Optional[Task]:
        active = {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.WAITING_FOR_USER,
            TaskStatus.WAITING_FOR_APPROVAL,
            TaskStatus.PAUSED,
        }
        for task in self.list_tasks(limit=100):
            if task.status in active:
                return task
        return None

    def delete(self, task_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cur.rowcount > 0
