from __future__ import annotations

import os
from typing import Any

import httpx

from campaign_clips.models import StreamStatus


class YouTubeAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class YouTubeClient:
    """YouTube Data API v3 helper for live detection on contracted channels."""

    API = "https://www.googleapis.com/youtube/v3"

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        if not api_key.strip():
            raise YouTubeAPIError("YouTube API key is empty")
        self.api_key = api_key.strip()
        self._http = http or httpx.Client(timeout=30.0)
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> YouTubeClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = dict(params)
        query["key"] = self.api_key
        response = self._http.get(f"{self.API}{path}", params=query)
        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                body = response.text
            raise YouTubeAPIError(
                f"YouTube API GET {path} failed: {response.status_code}",
                status_code=response.status_code,
                body=body,
            )
        return response.json()

    def resolve_channel_id(self, handle_or_id: str) -> str:
        value = handle_or_id.strip()
        if value.startswith("UC") and len(value) >= 24 and "@" not in value:
            return value
        handle = value.lstrip("@")
        data = self._get("/channels", {"part": "id", "forHandle": handle})
        rows = data.get("items") or []
        if rows:
            return str(rows[0]["id"])
        data = self._get(
            "/search",
            {"part": "snippet", "q": handle, "type": "channel", "maxResults": 1},
        )
        rows = data.get("items") or []
        if not rows:
            raise YouTubeAPIError(f"YouTube channel not found for '{handle_or_id}'")
        return str(rows[0]["snippet"]["channelId"])

    def get_live_for_channel(self, channel_id: str) -> StreamStatus | None:
        data = self._get(
            "/search",
            {
                "part": "snippet",
                "channelId": channel_id,
                "type": "video",
                "eventType": "live",
                "maxResults": 1,
            },
        )
        rows = data.get("items") or []
        if not rows:
            return None
        item = rows[0]
        snippet = item.get("snippet") or {}
        video_id = str((item.get("id") or {}).get("videoId") or "")
        title = str(snippet.get("title") or "")
        viewers = 0
        started_at = ""
        if video_id:
            detail = self._get(
                "/videos",
                {"part": "liveStreamingDetails,snippet", "id": video_id},
            )
            details = (detail.get("items") or [None])[0] or {}
            live = details.get("liveStreamingDetails") or {}
            viewers = int(live.get("concurrentViewers") or 0)
            started_at = str(live.get("actualStartTime") or "")
            sn = details.get("snippet") or {}
            title = str(sn.get("title") or title)
        return StreamStatus(
            user_id=channel_id,
            user_login=channel_id,
            user_name=str(snippet.get("channelTitle") or channel_id),
            game_id="",
            # YouTube has no Twitch-style game field; GameMatchTrigger also checks title.
            game_name=title,
            title=title,
            viewer_count=viewers,
            started_at=started_at,
            is_live=True,
            platform="youtube",
            video_id=video_id,
            watch_url=f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        )

    def get_live_by_channels(self, channel_ids: list[str]) -> dict[str, StreamStatus]:
        out: dict[str, StreamStatus] = {}
        for channel_id in channel_ids:
            status = self.get_live_for_channel(channel_id)
            if status:
                out[channel_id] = status
        return out


def youtube_credentials_from_env(env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    key = (source.get("YOUTUBE_API_KEY") or "").strip()
    if not key:
        from campaign_clips.config import ConfigError

        raise ConfigError(
            "Missing YOUTUBE_API_KEY (YouTube Data API v3 key for live channel checks)."
        )
    return key
