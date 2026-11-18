from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from campaign_clips.export import ExportedClip, export_highlights
from campaign_clips.highlights import build_highlights
from campaign_clips.media import probe
from campaign_clips.models import Campaign, Partner
from campaign_clips.tracker import ContentRewardsTracker


VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}


@dataclass
class ProcessResult:
    source: str
    campaign_id: str
    partner_login: str
    out_dir: str
    clips: list[ExportedClip]


def find_partner(campaign: Campaign, login: str) -> Partner:
    needle = login.strip().lstrip("@").lower()
    for partner in campaign.partners:
        if partner.normalized_login() == needle:
            return partner
    raise KeyError(f"partner '{login}' not in campaign '{campaign.id}'")


def find_campaign(campaigns: list[Campaign], campaign_id: str) -> Campaign:
    for campaign in campaigns:
        if campaign.id == campaign_id:
            return campaign
    raise KeyError(f"campaign not found: {campaign_id}")


def process_source(
    *,
    source: str | Path,
    campaign: Campaign,
    partner: Partner,
    out_root: str | Path,
    tracker: ContentRewardsTracker | None = None,
    max_clips: int | None = None,
    clip_duration_s: float | None = None,
    make_vertical: bool = True,
) -> ProcessResult:
    """Core Content Rewards path: local authorized VOD → highlights → export package."""
    src = Path(source)
    info = probe(src)
    duration = float(
        clip_duration_s
        if clip_duration_s is not None
        else campaign.default_clip_duration_s
    )
    limit = int(max_clips if max_clips is not None else campaign.max_clips_per_source)
    windows = build_highlights(
        str(src),
        clip_duration_s=duration,
        max_clips=limit,
        info=info,
    )
    out_dir = Path(out_root) / campaign.id / partner.normalized_login() / src.stem
    exported = export_highlights(
        source=src,
        windows=windows,
        campaign=campaign,
        partner=partner,
        out_dir=out_dir,
        make_vertical=make_vertical,
    )
    if tracker is not None:
        items = []
        for exported_clip in exported:
            base = exported_clip.to_dict()
            for site_id in campaign.reward_site_ids:
                row = dict(base)
                row["site_id"] = site_id
                items.append(row)
        tracker.upsert_ready(items)
    return ProcessResult(
        source=str(src),
        campaign_id=campaign.id,
        partner_login=partner.normalized_login(),
        out_dir=str(out_dir),
        clips=exported,
    )


def process_inbox(
    *,
    inbox_dir: str | Path,
    campaign: Campaign,
    partner: Partner,
    out_root: str | Path,
    tracker: ContentRewardsTracker | None = None,
    max_clips: int | None = None,
    make_vertical: bool = True,
) -> list[ProcessResult]:
    """Process every video in an inbox folder for one contracted partner."""
    root = Path(inbox_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"inbox not found: {root}")
    results: list[ProcessResult] = []
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in VIDEO_EXTS:
            continue
        if not path.is_file():
            continue
        results.append(
            process_source(
                source=path,
                campaign=campaign,
                partner=partner,
                out_root=out_root,
                tracker=tracker,
                max_clips=max_clips,
                make_vertical=make_vertical,
            )
        )
    return results
