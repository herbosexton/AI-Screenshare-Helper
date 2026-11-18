from __future__ import annotations

from typing import Any

import httpx

from campaign_clips.models import StreamStatus


class TwitchAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class TwitchClient:
    """Minimal Twitch Helix client for authorized campaign clipping."""

    HELIX = "https://api.twitch.tv/helix"
    OAUTH = "https://id.twitch.tv/oauth2"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str | None = None,
        http: httpx.Client | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._http = http or httpx.Client(timeout=30.0)
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> TwitchClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | list[tuple[str, str]] | None = None,
        json: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        response = self._http.request(
            method,
            url,
            params=params,
            json=json,
            headers=self._headers(),
        )
        if response.status_code == 401 and retry_auth and self.refresh_token:
            self.refresh_access_token()
            return self._request(
                method, url, params=params, json=json, retry_auth=False
            )
        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                body = response.text
            raise TwitchAPIError(
                f"Twitch API {method} {url} failed: {response.status_code}",
                status_code=response.status_code,
                body=body,
            )
        if not response.content:
            return {}
        return response.json()

    def refresh_access_token(self) -> str:
        if not self.refresh_token:
            raise TwitchAPIError("No refresh token configured")
        response = self._http.post(
            f"{self.OAUTH}/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        if response.status_code >= 400:
            raise TwitchAPIError(
                "Failed to refresh Twitch token",
                status_code=response.status_code,
                body=response.text,
            )
        payload = response.json()
        self.access_token = str(payload["access_token"])
        if payload.get("refresh_token"):
            self.refresh_token = str(payload["refresh_token"])
        return self.access_token

    def get_users_by_login(self, logins: list[str]) -> dict[str, dict[str, Any]]:
        if not logins:
            return {}
        # Helix allows up to 100 logins per call.
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(logins), 100):
            chunk = [login.lower() for login in logins[i : i + 100]]
            data = self._request(
                "GET",
                f"{self.HELIX}/users",
                params=[("login", login) for login in chunk],
            )
            for user in data.get("data") or []:
                out[str(user["login"]).lower()] = user
        return out

    def get_streams_by_user_ids(self, user_ids: list[str]) -> dict[str, StreamStatus]:
        if not user_ids:
            return {}
        out: dict[str, StreamStatus] = {}
        for i in range(0, len(user_ids), 100):
            chunk = user_ids[i : i + 100]
            data = self._request(
                "GET",
                f"{self.HELIX}/streams",
                params=[("user_id", uid) for uid in chunk],
            )
            for row in data.get("data") or []:
                status = StreamStatus(
                    user_id=str(row["user_id"]),
                    user_login=str(row["user_login"]).lower(),
                    user_name=str(row.get("user_name") or row["user_login"]),
                    game_id=str(row.get("game_id") or ""),
                    game_name=str(row.get("game_name") or ""),
                    title=str(row.get("title") or ""),
                    viewer_count=int(row.get("viewer_count") or 0),
                    started_at=str(row.get("started_at") or ""),
                    is_live=True,
                )
                out[status.user_id] = status
        return out

    def create_clip(self, broadcaster_id: str, has_delay: bool = False) -> dict[str, Any]:
        """Create a clip. Caller must be the broadcaster or an editor with clips:edit."""
        data = self._request(
            "POST",
            f"{self.HELIX}/clips",
            params={"broadcaster_id": broadcaster_id, "has_delay": str(has_delay).lower()},
        )
        rows = data.get("data") or []
        if not rows:
            raise TwitchAPIError("Twitch returned no clip data", body=data)
        return rows[0]

    def get_clip(self, clip_id: str) -> dict[str, Any] | None:
        data = self._request(
            "GET",
            f"{self.HELIX}/clips",
            params={"id": clip_id},
        )
        rows = data.get("data") or []
        return rows[0] if rows else None
