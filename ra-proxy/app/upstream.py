"""Read-only client for retroachievements.org.

Only "read" request types may ever be sent upstream. Writes (unlocks, leaderboard entries,
rich presence pings) stay inside the proxy; see ``WRITE_REQUESTS`` and the guard in ``fetch``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from .config import USER_AGENT, Settings
from .store import Store

log = logging.getLogger("ra-proxy.upstream")

# Request types the proxy is allowed to forward. Everything else is answered locally.
READ_REQUESTS = frozenset({"gameid", "patch", "achievementsets", "hashlibrary"})
# Request types that need the upstream account's credentials.
AUTHENTICATED_READS = frozenset({"patch", "achievementsets"})
# Never forwarded, whatever happens.
WRITE_REQUESTS = frozenset({
    "awardachievement", "awardachievements", "submitlbentry", "ping", "postactivity", "startsession",
    "uploadachievement", "submitcodenote", "submitticket", "submitgametitle",
})


class UpstreamError(Exception):
    pass


class Upstream:
    def __init__(self, settings: Settings, store: Store, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.store = store
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(20.0),
            transport=transport,
            follow_redirects=True,
        )
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0
        self._token: str | None = settings.ra_token or store.kv_get("upstream_token")

    @property
    def available(self) -> bool:
        return not self.settings.offline and self.settings.upstream_configured

    async def close(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._rate_lock:
            wait = self._last_request + self.settings.upstream_min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _post(self, params: dict[str, str]) -> tuple[int, dict]:
        await self._throttle()
        try:
            response = await self._client.post(f"{self.settings.ra_host}/dorequest.php", data=params)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"cannot reach RetroAchievements: {exc}") from exc
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise UpstreamError(f"unexpected response from RetroAchievements (HTTP {response.status_code})") from exc
        if not isinstance(body, dict):
            raise UpstreamError("unexpected response from RetroAchievements")
        return response.status_code, body

    async def _login(self) -> str:
        if not self.settings.ra_password:
            raise UpstreamError("RA_TOKEN rejected and no RA_PASSWORD configured")
        log.info("logging in to RetroAchievements as %s", self.settings.ra_username)
        _, body = await self._post({"r": "login2", "u": self.settings.ra_username, "p": self.settings.ra_password})
        token = body.get("Token")
        if not body.get("Success") or not token:
            raise UpstreamError(f"RetroAchievements login failed: {body.get('Error') or 'unknown error'}")
        self._token = str(token)
        self.store.kv_set("upstream_token", self._token)
        return self._token

    async def fetch(self, request_type: str, params: dict[str, str]) -> dict:
        """Forward one read request to RetroAchievements and return the decoded JSON body."""
        if request_type not in READ_REQUESTS or request_type in WRITE_REQUESTS:
            raise UpstreamError(f"request type '{request_type}' is never forwarded")
        if not self.available:
            raise UpstreamError("upstream disabled (offline mode or no RetroAchievements account configured)")

        query = {k: v for k, v in params.items() if k not in ("u", "t", "p")}
        query["r"] = request_type
        if request_type in AUTHENTICATED_READS:
            token = self._token or await self._login()
            query.update(u=self.settings.ra_username, t=token)
            status, body = await self._post(query)
            if status == 401 and self.settings.ra_password:
                query["t"] = await self._login()
                status, body = await self._post(query)
        else:
            status, body = await self._post(query)

        if status >= 500:
            raise UpstreamError(f"RetroAchievements returned HTTP {status}")
        return body
