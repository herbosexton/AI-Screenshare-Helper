from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from campaign_clips.models import ClipRecord


class ClipStore:
    """Append-only JSONL store for campaign clip records + rate limiting."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append(self, record: ClipRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), ensure_ascii=True) + "\n")

    def load(self) -> list[ClipRecord]:
        rows: list[ClipRecord] = []
        text = self.path.read_text(encoding="utf-8").strip()
        if not text:
            return rows
        for line in text.splitlines():
            data = json.loads(line)
            rows.append(
                ClipRecord(
                    campaign_id=str(data["campaign_id"]),
                    partner_login=str(data["partner_login"]),
                    clip_id=str(data["clip_id"]),
                    url=str(data.get("url") or ""),
                    embed_url=str(data.get("embed_url") or ""),
                    title=str(data.get("title") or ""),
                    created_at=str(data.get("created_at") or ""),
                    trigger=str(data.get("trigger") or ""),
                    broadcaster_id=str(data.get("broadcaster_id") or ""),
                    game_id=str(data.get("game_id") or ""),
                    metadata=dict(data.get("metadata") or {}),
                )
            )
        return rows

    def clips_in_last_hour(self, campaign_id: str, partner_login: str) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        login = partner_login.lower()
        count = 0
        for row in self.load():
            if row.campaign_id != campaign_id:
                continue
            if row.partner_login.lower() != login:
                continue
            try:
                created = datetime.fromisoformat(row.created_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if created >= cutoff:
                count += 1
        return count

    def summarize(self) -> dict[str, object]:
        rows = self.load()
        by_campaign: dict[str, int] = defaultdict(int)
        by_partner: dict[str, int] = defaultdict(int)
        for row in rows:
            by_campaign[row.campaign_id] += 1
            by_partner[row.partner_login.lower()] += 1
        return {
            "total_clips": len(rows),
            "by_campaign": dict(by_campaign),
            "by_partner": dict(by_partner),
            "clips": [r.to_dict() for r in rows],
        }
