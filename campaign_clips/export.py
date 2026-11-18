from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from campaign_clips.highlights import HighlightWindow
from campaign_clips.media import cut_clip, probe
from campaign_clips.models import Campaign, Partner, utc_now_iso


@dataclass
class ExportedClip:
    clip_id: str
    source_path: str
    horizontal_path: str
    vertical_path: str
    start_s: float
    duration_s: float
    score: float
    reason: str
    title: str
    caption: str
    campaign_id: str
    partner_login: str
    contract_id: str
    content_rewards_campaign: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _slug(text: str) -> str:
    out = []
    for ch in text.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_") and (not out or out[-1] != "-"):
            out.append("-")
    return "".join(out).strip("-") or "clip"


def build_caption(campaign: Campaign, partner: Partner, index: int) -> tuple[str, str]:
    name = partner.display_name or partner.twitch_login
    prefix = campaign.clip_title_prefix or campaign.name
    title = f"{prefix} | {name} #{index}"
    tags = " ".join(f"#{h}" for h in campaign.hashtags)
    caption = f"{title}\n{tags}".strip()
    return title[:120], caption[:2200]


def export_highlights(
    *,
    source: str | Path,
    windows: list[HighlightWindow],
    campaign: Campaign,
    partner: Partner,
    out_dir: str | Path,
    make_vertical: bool = True,
) -> list[ExportedClip]:
    """Cut highlight windows into an export package for manual Content Rewards upload."""
    src = Path(source)
    probe(src)  # validate early
    root = Path(out_dir)
    horiz_dir = root / "horizontal"
    vert_dir = root / "vertical"
    meta_dir = root / "meta"
    horiz_dir.mkdir(parents=True, exist_ok=True)
    if make_vertical:
        vert_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    cr_name = campaign.content_rewards_campaign or campaign.name
    partner_slug = _slug(partner.normalized_login())
    exported: list[ExportedClip] = []

    for i, window in enumerate(windows, start=1):
        clip_id = f"{campaign.id}-{partner_slug}-{i:02d}-{int(window.start_s)}"
        title, caption = build_caption(campaign, partner, i)
        horiz = horiz_dir / f"{clip_id}.mp4"
        cut_clip(
            src,
            horiz,
            start_s=window.start_s,
            duration_s=window.duration_s,
            vertical=False,
        )
        vertical_path = ""
        if make_vertical:
            vert = vert_dir / f"{clip_id}_9x16.mp4"
            cut_clip(
                src,
                vert,
                start_s=window.start_s,
                duration_s=window.duration_s,
                vertical=True,
            )
            vertical_path = str(vert)

        item = ExportedClip(
            clip_id=clip_id,
            source_path=str(src),
            horizontal_path=str(horiz),
            vertical_path=vertical_path,
            start_s=window.start_s,
            duration_s=window.duration_s,
            score=window.score,
            reason=window.reason,
            title=title,
            caption=caption,
            campaign_id=campaign.id,
            partner_login=partner.normalized_login(),
            contract_id=partner.contract_id,
            content_rewards_campaign=cr_name,
            created_at=utc_now_iso(),
        )
        (meta_dir / f"{clip_id}.json").write_text(
            json.dumps(item.to_dict(), indent=2),
            encoding="utf-8",
        )
        exported.append(item)

    manifest = {
        "campaign_id": campaign.id,
        "partner": partner.normalized_login(),
        "content_rewards_campaign": cr_name,
        "source": str(src),
        "created_at": utc_now_iso(),
        "clips": [e.to_dict() for e in exported],
        "upload_notes": (
            "Content Rewards has no public API. Upload vertical clips manually "
            "in the Content Rewards portal, then mark each clip submitted in the tracker."
        ),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    captions = root / "captions.txt"
    captions.write_text(
        "\n\n".join(f"{e.clip_id}\n{e.caption}" for e in exported),
        encoding="utf-8",
    )
    return exported
