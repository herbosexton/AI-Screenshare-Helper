from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from campaign_clips.models import utc_now_iso


ALLOWED_STATUSES = ("ready", "submitted", "rejected", "paid")


@dataclass
class TrackerRow:
    clip_id: str
    campaign_id: str
    partner_login: str
    contract_id: str
    content_rewards_campaign: str
    title: str
    vertical_path: str
    horizontal_path: str
    status: str = "ready"
    submitted_at: str = ""
    external_post_url: str = ""
    notes: str = ""
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContentRewardsTracker:
    """Local submission tracker — Content Rewards has no API, so status is manual."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def _load_raw(self) -> list[dict[str, Any]]:
        text = self.path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        return [json.loads(line) for line in text.splitlines()]

    def load(self) -> list[TrackerRow]:
        rows: list[TrackerRow] = []
        for data in self._load_raw():
            rows.append(
                TrackerRow(
                    clip_id=str(data["clip_id"]),
                    campaign_id=str(data.get("campaign_id") or ""),
                    partner_login=str(data.get("partner_login") or ""),
                    contract_id=str(data.get("contract_id") or ""),
                    content_rewards_campaign=str(data.get("content_rewards_campaign") or ""),
                    title=str(data.get("title") or ""),
                    vertical_path=str(data.get("vertical_path") or ""),
                    horizontal_path=str(data.get("horizontal_path") or ""),
                    status=str(data.get("status") or "ready"),
                    submitted_at=str(data.get("submitted_at") or ""),
                    external_post_url=str(data.get("external_post_url") or ""),
                    notes=str(data.get("notes") or ""),
                    updated_at=str(data.get("updated_at") or utc_now_iso()),
                )
            )
        return rows

    def _save(self, rows: list[TrackerRow]) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row.to_dict(), ensure_ascii=True) + "\n")

    def upsert_ready(self, items: list[dict[str, Any]]) -> int:
        by_id = {r.clip_id: r for r in self.load()}
        added = 0
        for item in items:
            clip_id = str(item["clip_id"])
            if clip_id in by_id:
                continue
            by_id[clip_id] = TrackerRow(
                clip_id=clip_id,
                campaign_id=str(item.get("campaign_id") or ""),
                partner_login=str(item.get("partner_login") or ""),
                contract_id=str(item.get("contract_id") or ""),
                content_rewards_campaign=str(item.get("content_rewards_campaign") or ""),
                title=str(item.get("title") or ""),
                vertical_path=str(item.get("vertical_path") or ""),
                horizontal_path=str(item.get("horizontal_path") or ""),
                status="ready",
            )
            added += 1
        self._save(list(by_id.values()))
        return added

    def set_status(
        self,
        clip_id: str,
        status: str,
        *,
        external_post_url: str = "",
        notes: str = "",
    ) -> TrackerRow:
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"status must be one of {ALLOWED_STATUSES}")
        rows = self.load()
        found: TrackerRow | None = None
        for row in rows:
            if row.clip_id == clip_id:
                row.status = status
                row.updated_at = utc_now_iso()
                if status == "submitted" and not row.submitted_at:
                    row.submitted_at = row.updated_at
                if external_post_url:
                    row.external_post_url = external_post_url
                if notes:
                    row.notes = notes
                found = row
                break
        if found is None:
            raise KeyError(f"clip_id not found: {clip_id}")
        self._save(rows)
        return found

    def summarize(self) -> dict[str, Any]:
        rows = self.load()
        by_status: dict[str, int] = {}
        for row in rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1
        return {
            "total": len(rows),
            "by_status": by_status,
            "rows": [r.to_dict() for r in rows],
        }

    def export_csv(self, dest: str | Path) -> Path:
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.load()
        fields = list(TrackerRow(
            clip_id="",
            campaign_id="",
            partner_login="",
            contract_id="",
            content_rewards_campaign="",
            title="",
            vertical_path="",
            horizontal_path="",
        ).to_dict().keys())
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(row.to_dict())
        return path
