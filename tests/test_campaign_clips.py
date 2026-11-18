from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from campaign_clips.clipper import CampaignClipper
from campaign_clips.config import ConfigError, load_campaigns, twitch_credentials_from_env
from campaign_clips.models import Campaign, Partner, StreamStatus
from campaign_clips.monitor import CampaignMonitor
from campaign_clips.store import ClipStore
from campaign_clips.triggers import (
    GameMatchTrigger,
    IntervalTrigger,
    ViewerSpikeTrigger,
    combine_all,
)
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
    )
    base.update(overrides)
    return Campaign(**base)


def test_load_campaigns_example(tmp_path: Path):
    src = Path("config/campaign_clips.example.yaml")
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    path = tmp_path / "campaigns.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    campaigns = load_campaigns(path)
    assert len(campaigns) == 1
    assert campaigns[0].id == "gta6-launch-week"
    assert campaigns[0].partners[0].normalized_login() == "partner_streamer_one"


def test_load_campaigns_requires_partners(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "campaigns": [
                    {
                        "id": "x",
                        "name": "x",
                        "game": "GTA",
                        "partners": [],
                    }
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
    creds = twitch_credentials_from_env()
    assert creds["access_token"] == "token"


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
    assert not game.evaluate(
        StreamStatus(**{**live.__dict__, "game_name": "Just Chatting"})
    ).should_clip

    spike = ViewerSpikeTrigger(min_viewers=10, spike_ratio=1.5)
    assert not spike.evaluate(live).should_clip  # baseline
    hotter = StreamStatus(**{**live.__dict__, "viewer_count": 200})
    assert spike.evaluate(hotter).should_clip


def test_interval_trigger_and_combine():
    interval = IntervalTrigger(every_n_polls=2)
    stream = StreamStatus(
        user_id="1",
        user_login="a",
        user_name="a",
        game_id="",
        game_name="GTA",
        title="",
        viewer_count=1,
        started_at="",
    )
    assert not interval.evaluate(stream).should_clip
    assert interval.evaluate(stream).should_clip
    ok = combine_all(
        GameMatchTrigger("GTA").evaluate(stream),
        interval.evaluate(stream),  # count=3 -> wait
    )
    assert not ok.should_clip


class _FakeTransport(httpx.BaseTransport):
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url)))
        path = request.url.path
        if path.endswith("/users"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "101",
                            "login": "partner_one",
                            "display_name": "Partner One",
                        }
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
                json={
                    "data": [
                        {
                            "id": "ClipABC",
                            "edit_url": "https://clips.twitch.tv/ClipABC/edit",
                        }
                    ]
                },
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


def test_twitch_client_create_clip():
    transport = _FakeTransport()
    http = httpx.Client(transport=transport)
    client = TwitchClient("id", "secret", "token", http=http)
    users = client.get_users_by_login(["partner_one"])
    assert users["partner_one"]["id"] == "101"
    streams = client.get_streams_by_user_ids(["101"])
    assert streams["101"].viewer_count == 250
    clip = client.create_clip("101")
    assert clip["id"] == "ClipABC"
    detail = client.get_clip("ClipABC")
    assert detail and detail["url"].endswith("ClipABC")
    client.close()


def test_clipper_and_monitor_rate_limit(tmp_path: Path):
    transport = _FakeTransport()
    http = httpx.Client(transport=transport)
    client = TwitchClient("id", "secret", "token", http=http)
    store = ClipStore(tmp_path / "clips.jsonl")
    clipper = CampaignClipper(
        client,
        store,
        sleep_fn=lambda _s: None,
        clip_ready_attempts=1,
        clip_ready_delay_s=0,
    )
    campaign = _campaign(max_clips_per_hour=1)

    # Force spike path: first poll baselines, second poll spikes.
    monitor = CampaignMonitor(
        [campaign],
        clipper,
        poll_interval_s=0,
        use_viewer_spike=True,
        use_interval_fallback=False,
        sleep_fn=lambda _s: None,
    )
    # First poll: live + game match but spike baseline -> skip
    created = monitor.poll_once()
    assert created == []

    # Bump mocked viewers by replacing transport handler via second client is hard;
    # instead call clipper directly for rate-limit coverage, and use interval fallback
    # for monitor clip creation.
    stream = StreamStatus(
        user_id="101",
        user_login="partner_one",
        user_name="Partner One",
        game_id="9",
        game_name="Grand Theft Auto VI",
        title="Heist night",
        viewer_count=250,
        started_at="2026-11-18T12:00:00Z",
    )
    record = clipper.create_for_stream(
        campaign, campaign.partners[0], stream, trigger="manual_test"
    )
    assert record is not None
    assert record.clip_id == "ClipABC"
    assert "GTA6" in record.title

    # Rate limit
    assert (
        clipper.create_for_stream(
            campaign, campaign.partners[0], stream, trigger="again"
        )
        is None
    )

    summary = store.summarize()
    assert summary["total_clips"] == 1
    client.close()


def test_monitor_interval_fallback_creates_clip(tmp_path: Path):
    transport = _FakeTransport()
    http = httpx.Client(transport=transport)
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
    assert created[0].partner_login == "partner_one"
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


def test_cli_validate_and_report(tmp_path: Path, monkeypatch):
    from campaign_clips.__main__ import main

    example = Path("config/campaign_clips.example.yaml")
    cfg = tmp_path / "campaigns.yaml"
    cfg.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    store = tmp_path / "clips.jsonl"
    store.write_text(
        json.dumps(
            {
                "campaign_id": "gta6-launch-week",
                "partner_login": "partner_one",
                "clip_id": "x",
                "url": "https://clips.twitch.tv/x",
                "embed_url": "",
                "title": "t",
                "created_at": "2026-11-18T20:00:00+00:00",
                "trigger": "test",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["--config", str(cfg), "validate-config"]) == 0
    assert main(["--store", str(store), "report"]) == 0
