from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
import yaml

from campaign_clips.clipper import CampaignClipper
from campaign_clips.config import ConfigError, load_campaigns, twitch_credentials_from_env
from campaign_clips.export import export_highlights
from campaign_clips.highlights import HighlightWindow, build_highlights
from campaign_clips.models import Campaign, Partner, StreamStatus
from campaign_clips.monitor import CampaignMonitor
from campaign_clips.pipeline import process_source
from campaign_clips.store import ClipStore
from campaign_clips.tracker import ContentRewardsTracker
from campaign_clips.triggers import GameMatchTrigger, ViewerSpikeTrigger
from campaign_clips.twitch_client import TwitchAPIError, TwitchClient


def _campaign(**overrides):
    base = dict(
        id="gta6-launch-week",
        name="GTA 6 Launch",
        game="Grand Theft Auto VI",
        partners=(
            Partner(
                twitch_login="Partner_One",
                display_name="Partner One",
                contract_id="C-1",
            ),
        ),
        clip_title_prefix="GTA6 Campaign",
        hashtags=("GTA6", "GTAVI"),
        max_clips_per_hour=3,
        enabled=True,
        content_rewards_campaign="GTA6 Content Rewards",
        content_rewards_slug="gta6-launch-week",
        default_clip_duration_s=5.0,
        max_clips_per_source=3,
    )
    base.update(overrides)
    return Campaign(**base)


def _make_test_video(path: Path, *, seconds: int = 12) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=640x360:rate=30:duration={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=880:duration={seconds}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        "-y",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert path.is_file()
    return path


def test_load_campaigns_example(tmp_path: Path):
    src = Path("config/campaign_clips.example.yaml")
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    path = tmp_path / "campaigns.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    campaigns = load_campaigns(path)
    assert len(campaigns) == 1
    assert campaigns[0].content_rewards_campaign.startswith("GTA6")
    assert campaigns[0].partners[0].normalized_login() == "partner_streamer_one"


def test_load_campaigns_requires_partners(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "campaigns": [
                    {"id": "x", "name": "x", "game": "GTA", "partners": []}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_campaigns(path)


def test_twitch_credentials_from_env(monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    with pytest.raises(ConfigError):
        twitch_credentials_from_env()
    monkeypatch.setenv("TWITCH_CLIENT_ID", "id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TWITCH_ACCESS_TOKEN", "token")
    assert twitch_credentials_from_env()["access_token"] == "token"


def test_game_and_spike_triggers():
    game = GameMatchTrigger("Grand Theft Auto VI")
    live = StreamStatus(
        user_id="1",
        user_login="partner_one",
        user_name="Partner",
        game_id="9",
        game_name="Grand Theft Auto VI",
        title="Launch day",
        viewer_count=100,
        started_at="2026-11-18T00:00:00Z",
    )
    assert game.evaluate(live).should_clip
    spike = ViewerSpikeTrigger(min_viewers=10, spike_ratio=1.5)
    assert not spike.evaluate(live).should_clip
    hotter = StreamStatus(**{**live.__dict__, "viewer_count": 200})
    assert spike.evaluate(hotter).should_clip


def test_highlights_and_export_pipeline(tmp_path: Path):
    video = _make_test_video(tmp_path / "vod.mp4", seconds=12)
    windows = build_highlights(
        str(video),
        clip_duration_s=4.0,
        max_clips=2,
        min_spacing_s=3.0,
        pre_roll_s=1.0,
    )
    assert 1 <= len(windows) <= 2
    campaign = _campaign()
    partner = campaign.partners[0]
    out = tmp_path / "export"
    exported = export_highlights(
        source=video,
        windows=windows,
        campaign=campaign,
        partner=partner,
        out_dir=out,
        make_vertical=True,
    )
    assert exported
    assert Path(exported[0].horizontal_path).is_file()
    assert Path(exported[0].vertical_path).is_file()
    assert (out / "manifest.json").is_file()
    assert "GTA6" in exported[0].caption


def test_process_source_updates_tracker(tmp_path: Path):
    video = _make_test_video(tmp_path / "partner_vod.mp4", seconds=10)
    campaign = _campaign(max_clips_per_source=2, default_clip_duration_s=4.0)
    tracker = ContentRewardsTracker(tmp_path / "tracker.jsonl")
    result = process_source(
        source=video,
        campaign=campaign,
        partner=campaign.partners[0],
        out_root=tmp_path / "out",
        tracker=tracker,
        make_vertical=False,
    )
    assert result.clips
    summary = tracker.summarize()
    assert summary["total"] == len(result.clips)
    assert summary["by_status"]["ready"] == len(result.clips)

    clip_id = result.clips[0].clip_id
    row = tracker.set_status(clip_id, "submitted", external_post_url="https://example.com/p")
    assert row.status == "submitted"
    csv_path = tracker.export_csv(tmp_path / "tracker.csv")
    assert csv_path.is_file()


class _FakeTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/users"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "101", "login": "partner_one", "display_name": "Partner One"}
                    ]
                },
            )
        if path.endswith("/streams"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "user_id": "101",
                            "user_login": "partner_one",
                            "user_name": "Partner One",
                            "game_id": "9",
                            "game_name": "Grand Theft Auto VI",
                            "title": "Heist night",
                            "viewer_count": 250,
                            "started_at": "2026-11-18T12:00:00Z",
                        }
                    ]
                },
            )
        if request.method == "POST" and path.endswith("/clips"):
            return httpx.Response(
                202,
                json={"data": [{"id": "ClipABC", "edit_url": "https://clips.twitch.tv/ClipABC/edit"}]},
            )
        if request.method == "GET" and path.endswith("/clips"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "ClipABC",
                            "url": "https://clips.twitch.tv/ClipABC",
                            "embed_url": "https://clips.twitch.tv/embed?clip=ClipABC",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "missing mock", "path": path})


def test_optional_twitch_monitor(tmp_path: Path):
    http = httpx.Client(transport=_FakeTransport())
    client = TwitchClient("id", "secret", "token", http=http)
    store = ClipStore(tmp_path / "clips.jsonl")
    clipper = CampaignClipper(
        client, store, sleep_fn=lambda _s: None, clip_ready_attempts=1, clip_ready_delay_s=0
    )
    campaign = _campaign(max_clips_per_hour=5)
    monitor = CampaignMonitor(
        [campaign],
        clipper,
        use_viewer_spike=False,
        use_interval_fallback=True,
        interval_every_n_polls=1,
        sleep_fn=lambda _s: None,
    )
    created = monitor.poll_once()
    assert len(created) == 1
    client.close()


def test_twitch_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "missing scope"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = TwitchClient("id", "secret", "token", http=http)
    with pytest.raises(TwitchAPIError) as exc:
        client.create_clip("101")
    assert exc.value.status_code == 403
    client.close()


def test_cli_validate_process_tracker(tmp_path: Path):
    from campaign_clips.__main__ import main

    example = Path("config/campaign_clips.example.yaml")
    cfg = tmp_path / "campaigns.yaml"
    data = yaml.safe_load(example.read_text(encoding="utf-8"))
    data["campaigns"][0]["max_clips_per_source"] = 2
    data["campaigns"][0]["default_clip_duration_s"] = 4
    data["campaigns"][0]["export_dir"] = str(tmp_path / "export")
    cfg.write_text(yaml.safe_dump(data), encoding="utf-8")

    video = _make_test_video(tmp_path / "in.mp4", seconds=9)
    tracker = tmp_path / "tracker.jsonl"

    assert main(["--config", str(cfg), "validate-config"]) == 0
    assert (
        main(
            [
                "--config",
                str(cfg),
                "--tracker",
                str(tracker),
                "process",
                "--campaign",
                "gta6-launch-week",
                "--partner",
                "partner_streamer_one",
                "--source",
                str(video),
                "--no-vertical",
            ]
        )
        == 0
    )
    assert main(["--tracker", str(tracker), "tracker", "list"]) == 0
    rows = ContentRewardsTracker(tracker).load()
    assert rows
    assert (
        main(
            [
                "--tracker",
                str(tracker),
                "tracker",
                "mark",
                "--clip-id",
                rows[0].clip_id,
                "--status",
                "paid",
            ]
        )
        == 0
    )


def test_export_accepts_manual_windows(tmp_path: Path):
    video = _make_test_video(tmp_path / "manual.mp4", seconds=8)
    campaign = _campaign()
    exported = export_highlights(
        source=video,
        windows=[HighlightWindow(start_s=1.0, duration_s=3.0, score=1.0, reason="manual")],
        campaign=campaign,
        partner=campaign.partners[0],
        out_dir=tmp_path / "pack",
        make_vertical=False,
    )
    assert len(exported) == 1
    assert exported[0].reason == "manual"


class _FakeLocator:
    def __init__(self, href: str = "https://contentrewards.example/p/1"):
        self._href = href

    @property
    def first(self):
        return self

    def get_attribute(self, name: str):
        return self._href if name == "href" else None


class _FakePage:
    def __init__(self):
        self.actions: list[tuple] = []
        self._url = "https://contentrewards.example/login"

    def goto(self, url: str, **kwargs):
        self._url = url
        self.actions.append(("goto", url))

    def fill(self, selector: str, value: str, **kwargs):
        self.actions.append(("fill", selector, value))

    def click(self, selector: str, **kwargs):
        self.actions.append(("click", selector))

    def set_input_files(self, selector: str, files, **kwargs):
        self.actions.append(("files", selector, files))

    def wait_for_selector(self, selector: str, **kwargs):
        self.actions.append(("wait", selector, kwargs.get("state")))

    def locator(self, selector: str):
        return _FakeLocator()

    def url(self) -> str:
        return self._url


def test_portal_upload_marks_tracker_submitted(tmp_path: Path):
    from campaign_clips.portal import ContentRewardsPortal, upload_ready_clips
    from campaign_clips.portal_config import PortalConfig, PortalSelectors

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    tracker = ContentRewardsTracker(tmp_path / "tracker.jsonl")
    tracker.upsert_ready(
        [
            {
                "clip_id": "clip-1",
                "campaign_id": "gta6-launch-week",
                "partner_login": "partner_one",
                "contract_id": "C-1",
                "content_rewards_campaign": "GTA6",
                "title": "GTA6 clip",
                "vertical_path": str(video),
                "horizontal_path": "",
            }
        ]
    )

    page = _FakePage()
    portal = ContentRewardsPortal(
        PortalConfig(
            base_url="https://contentrewards.example",
            login_url="https://contentrewards.example/login",
            upload_url_template="{base_url}/campaigns/{campaign_slug}/upload",
            selectors=PortalSelectors(),
        ),
        email="bot@example.com",
        password="secret",
        page=page,
    )
    results = upload_ready_clips(
        portal=portal,
        tracker=tracker,
        campaign_slug="gta6-launch-week",
        login=True,
    )
    assert len(results) == 1
    assert results[0].ok
    assert tracker.load()[0].status == "submitted"
    assert any(a[0] == "files" for a in page.actions)
    assert any(a[0] == "fill" and "password" in a[1] for a in page.actions)


def test_load_portal_config(tmp_path: Path):
    from campaign_clips.portal_config import load_portal_config, portal_credentials_from_env

    example = Path("config/campaign_clips.example.yaml")
    cfg = tmp_path / "c.yaml"
    cfg.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    portal = load_portal_config(cfg)
    assert portal is not None
    assert "campaigns" in portal.upload_url("gta6-launch-week")

    with pytest.raises(ConfigError):
        portal_credentials_from_env({})
    creds = portal_credentials_from_env(
        {"CONTENT_REWARDS_EMAIL": "a@b.c", "CONTENT_REWARDS_PASSWORD": "x"}
    )
    assert creds["email"] == "a@b.c"
