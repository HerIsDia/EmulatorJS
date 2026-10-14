"""Optional RomM lookup: RomM already stores the RetroAchievements hash and game id of each ROM.

Experimental. Uses ``GET /api/roms/by-hash?ra_hash=...`` with a RomM client API token.
"""

from __future__ import annotations

import logging

import httpx

from .config import USER_AGENT, Settings

log = logging.getLogger("ra-proxy.romm")


class RommClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        headers = {"User-Agent": USER_AGENT}
        if settings.romm_token:
            headers["Authorization"] = f"Bearer {settings.romm_token}"
        self._client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(10.0), transport=transport)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.romm_url)

    async def close(self) -> None:
        await self._client.aclose()

    async def game_id_for_hash(self, md5: str) -> int | None:
        if not self.enabled:
            return None
        try:
            response = await self._client.get(f"{self.settings.romm_url}/api/roms/by-hash", params={"ra_hash": md5})
        except httpx.HTTPError as exc:
            log.warning("RomM lookup failed: %s", exc)
            return None
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        if isinstance(data, list):
            data = data[0] if data else None
        if isinstance(data, dict):
            ra_id = data.get("ra_id")
            if isinstance(ra_id, int) and ra_id > 0:
                return ra_id
        return None
