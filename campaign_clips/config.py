from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from campaign_clips.models import Campaign, Partner


class ConfigError(ValueError):
    pass


def _require(mapping: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"Missing required field '{key}' in {ctx}")
    return mapping[key]


def load_campaigns(path: str | Path) -> list[Campaign]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    campaigns_raw = raw.get("campaigns")
    if not isinstance(campaigns_raw, list) or not campaigns_raw:
        raise ConfigError("config must include a non-empty 'campaigns' list")

    campaigns: list[Campaign] = []
    for i, item in enumerate(campaigns_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"campaigns[{i}] must be a mapping")
        ctx = f"campaigns[{i}]"
        partners_raw = _require(item, "partners", ctx)
        if not isinstance(partners_raw, list) or not partners_raw:
            raise ConfigError(f"{ctx}.partners must be a non-empty list")

        partners: list[Partner] = []
        for j, p in enumerate(partners_raw):
            if not isinstance(p, dict):
                raise ConfigError(f"{ctx}.partners[{j}] must be a mapping")
            pctx = f"{ctx}.partners[{j}]"
            twitch_login = str(p.get("twitch_login") or "").strip()
            youtube_channel_id = str(p.get("youtube_channel_id") or "").strip()
            youtube_handle = str(p.get("youtube_handle") or "").strip()
            if not twitch_login and not youtube_channel_id and not youtube_handle:
                raise ConfigError(
                    f"{pctx} requires twitch_login and/or youtube_channel_id/youtube_handle"
                )
            platforms_raw = p.get("platforms") or []
            if platforms_raw and not isinstance(platforms_raw, list):
                raise ConfigError(f"{pctx}.platforms must be a list")
            display = str(
                p.get("display_name")
                or twitch_login
                or youtube_handle
                or youtube_channel_id
            )
            partners.append(
                Partner(
                    twitch_login=twitch_login,
                    youtube_channel_id=youtube_channel_id,
                    youtube_handle=youtube_handle,
                    display_name=display,
                    contract_id=str(p.get("contract_id") or ""),
                    notes=str(p.get("notes") or ""),
                    platforms=tuple(str(x).lower() for x in platforms_raw),
                )
            )

        hashtags = tuple(str(h).lstrip("#") for h in (item.get("hashtags") or []))
        campaigns.append(
            Campaign(
                id=str(_require(item, "id", ctx)),
                name=str(_require(item, "name", ctx)),
                game=str(_require(item, "game", ctx)),
                partners=tuple(partners),
                clip_title_prefix=str(item.get("clip_title_prefix") or ""),
                hashtags=hashtags,
                max_clips_per_hour=int(item.get("max_clips_per_hour") or 6),
                enabled=bool(item.get("enabled", True)),
                content_rewards_campaign=str(
                    item.get("content_rewards_campaign") or item.get("name") or ""
                ),
                content_rewards_slug=str(
                    item.get("content_rewards_slug")
                    or item.get("id")
                    or ""
                ),
                reward_site_ids=_reward_site_ids(item),
                site_slugs=_site_slugs(item),
                default_clip_duration_s=float(item.get("default_clip_duration_s") or 25),
                max_clips_per_source=int(item.get("max_clips_per_source") or 8),
                inbox_dir=str(item.get("inbox_dir") or ""),
                export_dir=str(item.get("export_dir") or "data/campaign_clips/export"),
            )
        )
    return campaigns


def _reward_site_ids(item: dict) -> tuple[str, ...]:
    raw = item.get("reward_sites")
    if raw is None:
        return ("content_rewards",)
    if not isinstance(raw, list) or not raw:
        raise ConfigError("campaign.reward_sites must be a non-empty list of site ids")
    return tuple(str(x) for x in raw)


def _site_slugs(item: dict) -> tuple[tuple[str, str], ...]:
    raw = item.get("site_slugs") or {}
    if not raw:
        return ()
    if not isinstance(raw, dict):
        raise ConfigError("campaign.site_slugs must be a mapping of site_id → slug")
    return tuple((str(k), str(v)) for k, v in raw.items())


def twitch_credentials_from_env() -> dict[str, str]:
    """Load Twitch app credentials from environment.

    Required:
      TWITCH_CLIENT_ID
      TWITCH_CLIENT_SECRET
      TWITCH_ACCESS_TOKEN  (user token with clips:edit for contracted channels)
    Optional:
      TWITCH_REFRESH_TOKEN
    """
    client_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    access_token = os.environ.get("TWITCH_ACCESS_TOKEN", "").strip()
    missing = [
        name
        for name, value in (
            ("TWITCH_CLIENT_ID", client_id),
            ("TWITCH_CLIENT_SECRET", client_secret),
            ("TWITCH_ACCESS_TOKEN", access_token),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            "Missing Twitch credentials: "
            + ", ".join(missing)
            + ". Use a user access token that has clips:edit on contracted partner channels."
        )
    out = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": access_token,
    }
    refresh = os.environ.get("TWITCH_REFRESH_TOKEN", "").strip()
    if refresh:
        out["refresh_token"] = refresh
    return out
