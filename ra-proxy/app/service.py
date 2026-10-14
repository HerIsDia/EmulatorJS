"""Business logic shared by the emulator-facing (dorequest.php) and UI-facing (REST) endpoints."""

from __future__ import annotations

import json
import logging
import time

from .config import Settings
from .events import Broker
from .romm import RommClient
from .store import Store, dumps
from .upstream import READ_REQUESTS, Upstream, UpstreamError

log = logging.getLogger("ra-proxy")

# Parameters that do not change the content of a read response and must not split the cache.
_IGNORED_CACHE_PARAMS = {"r", "u", "t", "p"}


class ReadFailure(Exception):
    """A read request could not be served from the cache nor from RetroAchievements."""


def cache_key(request_type: str, params: dict[str, str]) -> str:
    parts = sorted((k, v) for k, v in params.items() if k not in _IGNORED_CACHE_PARAMS)
    return request_type + "?" + "&".join(f"{k}={v}" for k, v in parts)


class Service:
    def __init__(self, settings: Settings, store: Store, upstream: Upstream, romm: RommClient, broker: Broker):
        self.settings = settings
        self.store = store
        self.upstream = upstream
        self.romm = romm
        self.broker = broker

    # --- media URLs --------------------------------------------------------------------------

    def rewrite_media(self, body: str, public_base: str) -> str:
        """Point badge/icon URLs at the proxy, which caches the images."""
        media = self.settings.ra_media_host
        escaped = media.replace("/", "\\/")
        return body.replace(escaped, public_base.replace("/", "\\/")).replace(media, public_base)

    # --- read-through cache ------------------------------------------------------------------

    def _is_fresh(self, fetched_at: int) -> bool:
        ttl = self.settings.cache_ttl_hours
        return ttl <= 0 or (time.time() - fetched_at) < ttl * 3600

    async def read(self, request_type: str, params: dict[str, str]) -> str:
        """Serve a read request (gameid, patch, achievementsets, hashlibrary) as a JSON string."""
        if request_type not in READ_REQUESTS:
            raise ValueError(request_type)

        if request_type == "gameid":
            local = await self._local_game_id(params.get("m", ""))
            if local:
                return dumps({"Success": True, "GameID": local})

        key = cache_key(request_type, params)
        cached = self.store.cache_get(key)
        if cached and (self._is_fresh(cached[1]) or not self.upstream.available):
            return cached[0]

        try:
            body = await self.upstream.fetch(request_type, params)
        except UpstreamError as exc:
            if cached:
                log.info("serving stale cache for %s (%s)", key, exc)
                return cached[0]
            raise ReadFailure(str(exc)) from exc

        text = dumps(body)
        if body.get("Success"):
            self.store.cache_put(key, text)
            self._index(request_type, params, body)
        elif cached:
            # Keep serving the last good answer rather than an upstream error.
            return cached[0]
        return text

    async def _local_game_id(self, md5: str) -> int | None:
        if not md5:
            return None
        game_id = self.store.hash_get(md5)
        if game_id:
            return game_id
        game_id = await self.romm.game_id_for_hash(md5)
        if game_id:
            self.store.hash_put(md5, game_id)
        return game_id

    def _index(self, request_type: str, params: dict[str, str], body: dict) -> None:
        if request_type == "gameid":
            game_id = body.get("GameID")
            if isinstance(game_id, int) and game_id > 0:
                self.store.hash_put(params.get("m", ""), game_id)
        elif request_type in ("patch", "achievementsets"):
            game_id = self.store.ingest_definitions(body)
            if game_id and params.get("m"):
                self.store.hash_put(params["m"], game_id)

    async def ensure_definitions(self, game_id: int) -> bool:
        """Make sure the achievements of a game are known locally (used by the REST API)."""
        if self.store.game(game_id):
            return True
        for request_type, params in (("achievementsets", {"g": str(game_id), "v": "2"}), ("patch", {"g": str(game_id)})):
            try:
                body = json.loads(await self.read(request_type, params))
            except (ReadFailure, ValueError):
                continue
            if self.store.ingest_definitions(body):
                return True
        return False

    # --- local writes ------------------------------------------------------------------------

    def award(self, username: str, achievement_id: int, public_base: str = "") -> dict:
        achievement = self.store.achievement(achievement_id)
        game_id = achievement["game_id"] if achievement else None
        if self.store.add_unlock(username, achievement_id, game_id):
            view = self.achievement_view(achievement, achievement_id, True, int(time.time()), public_base)
            self.broker.publish(username, "unlock", view)
        score = self.store.score(username)
        return {
            "Success": True,
            "Score": score,
            "SoftcoreScore": score,
            "AchievementID": achievement_id,
            "AchievementsRemaining": self.store.remaining(username, game_id) if game_id else 0,
        }

    def submit_score(self, username: str, leaderboard_id: int, score: int) -> dict:
        board = self.store.leaderboard(leaderboard_id)
        lower = bool(board and board["lower_is_better"])
        best = self.store.submit_score(username, leaderboard_id, board["game_id"] if board else None, score, lower)
        entries = self.store.leaderboard_entries(leaderboard_id, lower)
        rank = next((i + 1 for i, e in enumerate(entries) if e["username"] == username.lower()), len(entries))
        self.broker.publish(username, "leaderboard", {
            "id": leaderboard_id, "title": board["title"] if board else "", "score": score, "best": best,
        })
        return {
            "Success": True,
            "Response": {
                "Score": score,
                "BestScore": best,
                "RankInfo": {"Rank": rank, "NumEntries": len(entries)},
                "TopEntries": [
                    {"User": e["username"], "Rank": i + 1, "Score": e["score"]} for i, e in enumerate(entries[:10])
                ],
            },
        }

    # --- views for the UI --------------------------------------------------------------------

    def achievement_view(self, achievement: dict | None, achievement_id: int, unlocked: bool = False,
                         unlocked_at: int | None = None, public_base: str = "") -> dict:
        base = public_base or self.settings.public_url
        badge = achievement["badge_name"] if achievement else ""
        return {
            "id": achievement_id,
            "gameId": achievement["game_id"] if achievement else None,
            "title": achievement["title"] if achievement else f"#{achievement_id}",
            "description": achievement["description"] if achievement else "",
            "points": achievement["points"] if achievement else 0,
            "type": achievement["type"] if achievement else "",
            "badgeUrl": f"{base}/Badge/{badge}.png" if badge else "",
            "badgeLockedUrl": f"{base}/Badge/{badge}_lock.png" if badge else "",
            "unlocked": unlocked,
            "unlockedAt": unlocked_at,
        }

    def game_view(self, username: str, game_id: int, public_base: str, include_unofficial: bool = False) -> dict | None:
        game = self.store.game(game_id)
        if not game:
            return None
        unlocked = {u["achievement_id"]: u["unlocked_at"] for u in self.store.unlocks(username, game_id)}
        achievements = [
            self.achievement_view(a, a["id"], a["id"] in unlocked, unlocked.get(a["id"]), public_base)
            for a in self.store.achievements_for_game(game_id, include_unofficial)
        ]
        return {
            "game": {
                "id": game["id"],
                "title": game["title"],
                "consoleId": game["console_id"],
                "imageUrl": self.rewrite_media(game["image_url"] or "", public_base),
            },
            "achievements": achievements,
            "summary": {
                "total": len(achievements),
                "unlocked": sum(1 for a in achievements if a["unlocked"]),
                "points": sum(a["points"] for a in achievements),
                "pointsUnlocked": sum(a["points"] for a in achievements if a["unlocked"]),
            },
        }
