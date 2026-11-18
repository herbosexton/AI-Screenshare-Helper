from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from campaign_clips.models import Campaign
from campaign_clips.portal_config import RewardSite, RewardSiteRegistry, site_credentials
from campaign_clips.tracker import ContentRewardsTracker, TrackerRow


class PortalError(RuntimeError):
    pass


class BrowserPage(Protocol):
    def goto(self, url: str, **kwargs: Any) -> Any: ...
    def fill(self, selector: str, value: str, **kwargs: Any) -> Any: ...
    def click(self, selector: str, **kwargs: Any) -> Any: ...
    def set_input_files(self, selector: str, files: str | list[str], **kwargs: Any) -> Any: ...
    def wait_for_selector(self, selector: str, **kwargs: Any) -> Any: ...
    def locator(self, selector: str) -> Any: ...
    def url(self) -> str: ...


@dataclass
class UploadResult:
    clip_id: str
    ok: bool
    site_id: str = ""
    post_url: str = ""
    error: str = ""


class RewardsPortalBot:
    """Automates login + clip upload for any configured rewards website.

    These portals typically have no public API, so the bot drives the website
    for campaign accounts you control (contracted/paid campaigns only).
    """

    def __init__(
        self,
        site: RewardSite,
        *,
        email: str,
        password: str,
        page: BrowserPage | None = None,
    ):
        self.site = site
        self.config = site  # alias used by older call sites
        self.email = email
        self.password = password
        self._page = page
        self._playwright = None
        self._browser = None
        self._context = None
        self._owns_browser = page is None

    def __enter__(self) -> RewardsPortalBot:
        if self._page is None:
            self._start_browser()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _start_browser(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PortalError(
                "playwright is required for rewards portal automation. "
                "Install with: pip install playwright && playwright install chromium"
            ) from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.site.headless,
            slow_mo=self.site.slow_mo_ms,
        )
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.site.navigation_timeout_ms)

    @property
    def page(self) -> BrowserPage:
        if self._page is None:
            raise PortalError("portal browser is not started")
        return self._page

    def close(self) -> None:
        if not self._owns_browser:
            return
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def login(self) -> None:
        page = self.page
        sel = self.site.selectors
        page.goto(self.site.login_url, wait_until="domcontentloaded")
        page.fill(sel.email, self.email)
        page.fill(sel.password, self.password)
        page.click(sel.login_submit)
        try:
            page.wait_for_selector(sel.login_submit, state="detached", timeout=15_000)
        except Exception:
            pass

    def open_upload(self, campaign_slug: str) -> None:
        url = self.site.upload_url(campaign_slug)
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_selector(
            self.site.selectors.file_input,
            timeout=self.site.navigation_timeout_ms,
        )

    def upload_clip(
        self,
        *,
        video_path: str | Path,
        title: str,
        caption: str,
        campaign_slug: str,
        already_on_upload_page: bool = False,
    ) -> UploadResult:
        path = Path(video_path)
        if not path.is_file():
            return UploadResult(
                clip_id=path.stem,
                ok=False,
                site_id=self.site.id,
                error=f"missing file: {path}",
            )

        sel = self.site.selectors
        page = self.page
        try:
            if not already_on_upload_page:
                self.open_upload(campaign_slug)

            page.set_input_files(sel.file_input, str(path))
            try:
                page.fill(sel.title, title)
            except Exception:
                pass
            try:
                page.fill(sel.caption, caption)
            except Exception:
                pass

            page.click(sel.upload_submit)
            page.wait_for_selector(sel.success, timeout=self.site.upload_timeout_ms)

            post_url = ""
            try:
                href = page.locator(sel.post_url).first.get_attribute("href")
                if href:
                    post_url = str(href)
            except Exception:
                post_url = ""

            return UploadResult(
                clip_id=path.stem,
                ok=True,
                site_id=self.site.id,
                post_url=post_url,
            )
        except Exception as exc:
            return UploadResult(
                clip_id=path.stem,
                ok=False,
                site_id=self.site.id,
                error=str(exc),
            )


# Backward-compatible name
ContentRewardsPortal = RewardsPortalBot


def upload_ready_clips(
    *,
    portal: RewardsPortalBot,
    tracker: ContentRewardsTracker,
    campaign_slug: str,
    site_id: str | None = None,
    campaign_id: str | None = None,
    limit: int | None = None,
    prefer_vertical: bool = True,
    login: bool = True,
) -> list[UploadResult]:
    """Upload ready tracker rows for one site, then mark them submitted."""
    resolved_site = site_id or portal.site.id
    ready = tracker.ready_for_site(resolved_site, campaign_id=campaign_id)
    if not ready:
        # Legacy rows may omit site filtering nuances
        ready = [
            r
            for r in tracker.load()
            if r.status == "ready" and (r.site_id == resolved_site or not site_id)
        ]
        if campaign_id:
            ready = [r for r in ready if r.campaign_id == campaign_id]
    if limit is not None:
        ready = ready[: max(0, limit)]

    results: list[UploadResult] = []
    if not ready:
        return results

    if login:
        portal.login()

    for row in ready:
        video = _pick_video(row, prefer_vertical=prefer_vertical)
        if not video:
            results.append(
                UploadResult(
                    clip_id=row.clip_id,
                    ok=False,
                    site_id=resolved_site,
                    error="no vertical/horizontal video path on tracker row",
                )
            )
            continue

        caption = row.title
        meta_caption = _caption_from_sidecar(row)
        if meta_caption:
            caption = meta_caption

        result = portal.upload_clip(
            video_path=video,
            title=row.title or row.clip_id,
            caption=caption,
            campaign_slug=campaign_slug,
            already_on_upload_page=False,
        )

        if result.ok:
            tracker.set_status(
                row.clip_id,
                "submitted",
                site_id=row.site_id or resolved_site,
                external_post_url=result.post_url,
                notes=f"uploaded by rewards portal bot ({resolved_site})",
            )
        results.append(
            UploadResult(
                clip_id=row.clip_id,
                ok=result.ok,
                site_id=resolved_site,
                post_url=result.post_url,
                error=result.error,
            )
        )
    return results


def upload_campaign_to_sites(
    *,
    campaign: Campaign,
    registry: RewardSiteRegistry,
    tracker: ContentRewardsTracker,
    site_ids: list[str] | None = None,
    limit_per_site: int | None = None,
    headed: bool = False,
    env: dict[str, str] | None = None,
    portal_factory=None,
) -> list[UploadResult]:
    """Upload one campaign's ready clips to each configured rewards website."""
    targets = list(site_ids) if site_ids else list(campaign.reward_site_ids)
    results: list[UploadResult] = []
    factory = portal_factory or (
        lambda site, email, password: RewardsPortalBot(site, email=email, password=password)
    )

    for site_id in targets:
        site = registry.get(site_id)
        if headed:
            site = replace(site, headless=False)
        creds = site_credentials(site, env=env)
        slug = campaign.slug_for_site(site_id)
        with factory(site, creds["email"], creds["password"]) as portal:
            batch = upload_ready_clips(
                portal=portal,
                tracker=tracker,
                campaign_slug=slug,
                site_id=site_id,
                campaign_id=campaign.id,
                limit=limit_per_site,
                login=True,
            )
            results.extend(batch)
    return results


def _pick_video(row: TrackerRow, *, prefer_vertical: bool) -> str:
    if prefer_vertical and row.vertical_path and Path(row.vertical_path).is_file():
        return row.vertical_path
    if row.horizontal_path and Path(row.horizontal_path).is_file():
        return row.horizontal_path
    if row.vertical_path and Path(row.vertical_path).is_file():
        return row.vertical_path
    return ""


def _caption_from_sidecar(row: TrackerRow) -> str:
    for candidate in (row.vertical_path, row.horizontal_path):
        if not candidate:
            continue
        meta = Path(candidate).resolve().parent.parent / "meta" / f"{row.clip_id}.json"
        if meta.is_file():
            import json

            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                return str(data.get("caption") or "")
            except Exception:
                return ""
    return ""
