from __future__ import annotations

import os
from dataclasses import dataclass, field
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
class RewardSite:
    """One paid rewards website the bot can log into and upload clips to."""

    id: str
    display_name: str
    base_url: str
    login_url: str
    upload_url_template: str
    selectors: PortalSelectors
    email_env: str = "CONTENT_REWARDS_EMAIL"
    password_env: str = "CONTENT_REWARDS_PASSWORD"
    headless: bool = True
    slow_mo_ms: int = 0
    navigation_timeout_ms: int = 60_000
    upload_timeout_ms: int = 180_000

    def upload_url(self, campaign_slug: str) -> str:
        return self.upload_url_template.format(
            campaign_slug=campaign_slug,
            base_url=self.base_url.rstrip("/"),
            site_id=self.id,
        )


# Backward-compatible alias used by older portal.py call sites / tests.
PortalConfig = RewardSite


def _selectors_from_raw(sel_raw: dict[str, Any] | None) -> PortalSelectors:
    raw = sel_raw or {}
    return PortalSelectors(
        email=str(raw.get("email") or PortalSelectors.email),
        password=str(raw.get("password") or PortalSelectors.password),
        login_submit=str(raw.get("login_submit") or PortalSelectors.login_submit),
        file_input=str(raw.get("file_input") or PortalSelectors.file_input),
        title=str(raw.get("title") or PortalSelectors.title),
        caption=str(raw.get("caption") or PortalSelectors.caption),
        upload_submit=str(raw.get("upload_submit") or PortalSelectors.upload_submit),
        success=str(raw.get("success") or PortalSelectors.success),
        post_url=str(raw.get("post_url") or PortalSelectors.post_url),
    )


def _site_from_mapping(site_id: str, raw: dict[str, Any], ctx: str) -> RewardSite:
    creds = raw.get("credentials_env") or {}
    if creds and not isinstance(creds, dict):
        raise ConfigError(f"{ctx}.credentials_env must be a mapping")
    return RewardSite(
        id=site_id,
        display_name=str(raw.get("display_name") or site_id),
        base_url=str(_require(raw, "base_url", ctx)).rstrip("/"),
        login_url=str(_require(raw, "login_url", ctx)),
        upload_url_template=str(_require(raw, "upload_url_template", ctx)),
        selectors=_selectors_from_raw(raw.get("selectors") if isinstance(raw.get("selectors"), dict) else {}),
        email_env=str(creds.get("email") or raw.get("email_env") or "CONTENT_REWARDS_EMAIL"),
        password_env=str(creds.get("password") or raw.get("password_env") or "CONTENT_REWARDS_PASSWORD"),
        headless=bool(raw.get("headless", True)),
        slow_mo_ms=int(raw.get("slow_mo_ms") or 0),
        navigation_timeout_ms=int(raw.get("navigation_timeout_ms") or 60_000),
        upload_timeout_ms=int(raw.get("upload_timeout_ms") or 180_000),
    )


@dataclass(frozen=True)
class RewardSiteRegistry:
    sites: dict[str, RewardSite] = field(default_factory=dict)

    def get(self, site_id: str) -> RewardSite:
        if site_id not in self.sites:
            raise KeyError(f"unknown reward site: {site_id}")
        return self.sites[site_id]

    def ids(self) -> list[str]:
        return list(self.sites.keys())


def load_reward_sites(path: str | Path) -> RewardSiteRegistry:
    """Load `reward_sites:` and/or legacy single `portal:` block."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sites: dict[str, RewardSite] = {}

    multi = raw.get("reward_sites")
    if multi is not None:
        if not isinstance(multi, dict) or not multi:
            raise ConfigError("'reward_sites' must be a non-empty mapping of site_id → config")
        for site_id, item in multi.items():
            if not isinstance(item, dict):
                raise ConfigError(f"reward_sites.{site_id} must be a mapping")
            sites[str(site_id)] = _site_from_mapping(str(site_id), item, f"reward_sites.{site_id}")

    # Legacy single-portal config → site id "content_rewards"
    portal = raw.get("portal")
    if portal is not None:
        if not isinstance(portal, dict):
            raise ConfigError("'portal' must be a mapping")
        if "content_rewards" not in sites:
            sites["content_rewards"] = _site_from_mapping(
                "content_rewards", portal, "portal"
            )

    return RewardSiteRegistry(sites=sites)


def load_portal_config(path: str | Path) -> RewardSite | None:
    """Backward compatible: return the first/default site, if any."""
    registry = load_reward_sites(path)
    if not registry.sites:
        return None
    if "content_rewards" in registry.sites:
        return registry.sites["content_rewards"]
    return next(iter(registry.sites.values()))


def site_credentials(
    site: RewardSite,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    source = env if env is not None else os.environ
    email = (source.get(site.email_env) or "").strip()
    password = (source.get(site.password_env) or "").strip()
    missing = [
        name
        for name, value in ((site.email_env, email), (site.password_env, password))
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Missing credentials for reward site '{site.id}' ({site.display_name}): "
            + ", ".join(missing)
        )
    return {"email": email, "password": password}


def portal_credentials_from_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Legacy helper for the default Content Rewards env var names."""
    site = RewardSite(
        id="content_rewards",
        display_name="Content Rewards",
        base_url="https://example.invalid",
        login_url="https://example.invalid/login",
        upload_url_template="{base_url}/upload",
        selectors=PortalSelectors(),
    )
    return site_credentials(site, env=env)
