from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from campaign_clips.config import ConfigError, _require


@dataclass(frozen=True)
class PortalSelectors:
    email: str = 'input[type="email"], input[name="email"], input[name="username"]'
    password: str = 'input[type="password"], input[name="password"]'
    login_submit: str = 'button[type="submit"]'
    file_input: str = 'input[type="file"]'
    title: str = 'input[name="title"], input[placeholder*="Title" i]'
    caption: str = 'textarea[name="caption"], textarea[name="description"], textarea'
    upload_submit: str = 'button[type="submit"], button:has-text("Upload"), button:has-text("Submit")'
    success: str = "text=/upload(ed)? successfully|success|submitted/i"
    post_url: str = "a[href*='http']"


@dataclass(frozen=True)
class PortalConfig:
    """Browser-automation profile for a Content Rewards portal (no public API)."""

    base_url: str
    login_url: str
    upload_url_template: str
    selectors: PortalSelectors
    headless: bool = True
    slow_mo_ms: int = 0
    navigation_timeout_ms: int = 60_000
    upload_timeout_ms: int = 180_000

    def upload_url(self, campaign_slug: str) -> str:
        return self.upload_url_template.format(
            campaign_slug=campaign_slug,
            base_url=self.base_url.rstrip("/"),
        )


def load_portal_config(path: str | Path) -> PortalConfig | None:
    """Load optional top-level `portal:` block from campaign YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    portal = raw.get("portal")
    if portal is None:
        return None
    if not isinstance(portal, dict):
        raise ConfigError("'portal' must be a mapping")

    sel_raw = portal.get("selectors") or {}
    if sel_raw and not isinstance(sel_raw, dict):
        raise ConfigError("portal.selectors must be a mapping")

    selectors = PortalSelectors(
        email=str(sel_raw.get("email") or PortalSelectors.email),
        password=str(sel_raw.get("password") or PortalSelectors.password),
        login_submit=str(sel_raw.get("login_submit") or PortalSelectors.login_submit),
        file_input=str(sel_raw.get("file_input") or PortalSelectors.file_input),
        title=str(sel_raw.get("title") or PortalSelectors.title),
        caption=str(sel_raw.get("caption") or PortalSelectors.caption),
        upload_submit=str(sel_raw.get("upload_submit") or PortalSelectors.upload_submit),
        success=str(sel_raw.get("success") or PortalSelectors.success),
        post_url=str(sel_raw.get("post_url") or PortalSelectors.post_url),
    )

    return PortalConfig(
        base_url=str(_require(portal, "base_url", "portal")).rstrip("/"),
        login_url=str(_require(portal, "login_url", "portal")),
        upload_url_template=str(
            _require(portal, "upload_url_template", "portal")
        ),
        selectors=selectors,
        headless=bool(portal.get("headless", True)),
        slow_mo_ms=int(portal.get("slow_mo_ms") or 0),
        navigation_timeout_ms=int(portal.get("navigation_timeout_ms") or 60_000),
        upload_timeout_ms=int(portal.get("upload_timeout_ms") or 180_000),
    )


def portal_credentials_from_env(env: dict[str, str] | None = None) -> dict[str, str]:
    import os

    source = env if env is not None else os.environ
    email = (source.get("CONTENT_REWARDS_EMAIL") or "").strip()
    password = (source.get("CONTENT_REWARDS_PASSWORD") or "").strip()
    missing = [
        name
        for name, value in (
            ("CONTENT_REWARDS_EMAIL", email),
            ("CONTENT_REWARDS_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            "Missing Content Rewards portal credentials: "
            + ", ".join(missing)
            + ". Set them for the bot account that runs your contracted campaigns."
        )
    return {"email": email, "password": password}


def campaign_slug_from_config(item: dict[str, Any]) -> str:
    return str(
        item.get("content_rewards_slug")
        or item.get("content_rewards_campaign")
        or item.get("id")
        or ""
    )
