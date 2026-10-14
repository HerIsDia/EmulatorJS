"""SQLite storage: cached RetroAchievements responses, parsed definitions and LOCAL unlocks."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body TEXT NOT NULL, fetched_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS hashes (hash TEXT PRIMARY KEY, game_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY, title TEXT, console_id INTEGER, image_url TEXT, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL, title TEXT, description TEXT, points INTEGER,
    badge_name TEXT, flags INTEGER, type TEXT, display_order INTEGER
);
CREATE INDEX IF NOT EXISTS idx_achievements_game ON achievements (game_id);
CREATE TABLE IF NOT EXISTS leaderboards (
    id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL, title TEXT, description TEXT, format TEXT,
    lower_is_better INTEGER
);
CREATE TABLE IF NOT EXISTS unlocks (
    username TEXT NOT NULL, achievement_id INTEGER NOT NULL, game_id INTEGER, unlocked_at INTEGER NOT NULL,
    PRIMARY KEY (username, achievement_id)
);
CREATE TABLE IF NOT EXISTS lb_entries (
    username TEXT NOT NULL, leaderboard_id INTEGER NOT NULL, game_id INTEGER, score INTEGER NOT NULL,
    submitted_at INTEGER NOT NULL, PRIMARY KEY (username, leaderboard_id)
);
"""

# RetroAchievements achievement flags.
FLAG_CORE = 3
FLAG_UNOFFICIAL = 5


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _exec(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, params)
            rows = cur.fetchall()
            self._db.commit()
            return rows

    # --- key/value ---------------------------------------------------------------------------

    def kv_get(self, key: str) -> str | None:
        rows = self._exec("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def kv_set(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))

    # --- raw response cache ------------------------------------------------------------------

    def cache_get(self, key: str) -> tuple[str, int] | None:
        rows = self._exec("SELECT body, fetched_at FROM cache WHERE key = ?", (key,))
        return (rows[0]["body"], rows[0]["fetched_at"]) if rows else None

    def cache_put(self, key: str, body: str) -> None:
        self._exec(
            "INSERT OR REPLACE INTO cache (key, body, fetched_at) VALUES (?, ?, ?)",
            (key, body, int(time.time())),
        )

    # --- hashes ------------------------------------------------------------------------------

    def hash_get(self, md5: str) -> int | None:
        rows = self._exec("SELECT game_id FROM hashes WHERE hash = ?", (md5.lower(),))
        return rows[0]["game_id"] if rows else None

    def hash_put(self, md5: str, game_id: int) -> None:
        if md5 and game_id:
            self._exec("INSERT OR REPLACE INTO hashes (hash, game_id) VALUES (?, ?)", (md5.lower(), game_id))

    # --- definitions -------------------------------------------------------------------------

    def ingest_definitions(self, data: dict) -> int | None:
        """Index a ``patch`` or ``achievementsets`` response. Returns the game id."""
        now = int(time.time())
        if isinstance(data.get("PatchData"), dict):
            patch = data["PatchData"]
            game_id = _int(patch.get("ID"))
            if not game_id:
                return None
            self._upsert_game(game_id, patch.get("Title"), patch.get("ConsoleID"), patch.get("ImageIconURL"), now)
            self._upsert_set(game_id, patch.get("Achievements") or [], patch.get("Leaderboards") or [])
            return game_id
        if isinstance(data.get("Sets"), list):
            game_id = _int(data.get("GameId"))
            if not game_id:
                return None
            self._upsert_game(game_id, data.get("Title"), data.get("ConsoleId"), data.get("ImageIconUrl"), now)
            for game_set in data["Sets"]:
                if not isinstance(game_set, dict):
                    continue
                # Subsets keep their own game id on RetroAchievements; attach them to the root game
                # so that a play session shows every achievement it can trigger.
                self._upsert_set(game_id, game_set.get("Achievements") or [], game_set.get("Leaderboards") or [])
            return game_id
        return None

    def _upsert_game(self, game_id: int, title: Any, console_id: Any, image_url: Any, now: int) -> None:
        self._exec(
            "INSERT OR REPLACE INTO games (id, title, console_id, image_url, updated_at) VALUES (?, ?, ?, ?, ?)",
            (game_id, title or "", _int(console_id), image_url or "", now),
        )

    def _upsert_set(self, game_id: int, achievements: list, leaderboards: list) -> None:
        with self._lock:
            for order, ach in enumerate(achievements):
                if not isinstance(ach, dict) or not _int(ach.get("ID")):
                    continue
                self._db.execute(
                    "INSERT OR REPLACE INTO achievements "
                    "(id, game_id, title, description, points, badge_name, flags, type, display_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _int(ach.get("ID")), game_id, ach.get("Title") or "", ach.get("Description") or "",
                        _int(ach.get("Points")), str(ach.get("BadgeName") or ""), _int(ach.get("Flags"), FLAG_CORE),
                        ach.get("Type") or "", order,
                    ),
                )
            for lb in leaderboards:
                if not isinstance(lb, dict) or not _int(lb.get("ID")):
                    continue
                self._db.execute(
                    "INSERT OR REPLACE INTO leaderboards (id, game_id, title, description, format, lower_is_better) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        _int(lb.get("ID")), game_id, lb.get("Title") or "", lb.get("Description") or "",
                        lb.get("Format") or "", 1 if lb.get("LowerIsBetter") in (True, 1, "1") else 0,
                    ),
                )
            self._db.commit()

    def game(self, game_id: int) -> dict | None:
        rows = self._exec("SELECT * FROM games WHERE id = ?", (game_id,))
        return dict(rows[0]) if rows else None

    def achievement(self, achievement_id: int) -> dict | None:
        rows = self._exec("SELECT * FROM achievements WHERE id = ?", (achievement_id,))
        return dict(rows[0]) if rows else None

    def achievements_for_game(self, game_id: int, include_unofficial: bool = False) -> list[dict]:
        sql = "SELECT * FROM achievements WHERE game_id = ?"
        if not include_unofficial:
            sql += f" AND flags = {FLAG_CORE}"
        return [dict(r) for r in self._exec(sql + " ORDER BY display_order, id", (game_id,))]

    def leaderboard(self, leaderboard_id: int) -> dict | None:
        rows = self._exec("SELECT * FROM leaderboards WHERE id = ?", (leaderboard_id,))
        return dict(rows[0]) if rows else None

    # --- local unlocks -----------------------------------------------------------------------

    def add_unlock(self, username: str, achievement_id: int, game_id: int | None) -> bool:
        """Store an unlock. Returns False if the user already had it."""
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO unlocks (username, achievement_id, game_id, unlocked_at) VALUES (?, ?, ?, ?)",
                (username.lower(), achievement_id, game_id, int(time.time())),
            )
            self._db.commit()
            return cur.rowcount > 0

    def unlocks(self, username: str, game_id: int | None = None) -> list[dict]:
        sql = "SELECT achievement_id, game_id, unlocked_at FROM unlocks WHERE username = ?"
        params: tuple = (username.lower(),)
        if game_id is not None:
            sql += " AND game_id = ?"
            params += (game_id,)
        return [dict(r) for r in self._exec(sql + " ORDER BY unlocked_at, achievement_id", params)]

    def score(self, username: str) -> int:
        rows = self._exec(
            "SELECT COALESCE(SUM(a.points), 0) AS score FROM unlocks u "
            "JOIN achievements a ON a.id = u.achievement_id WHERE u.username = ? AND a.flags = ?",
            (username.lower(), FLAG_CORE),
        )
        return int(rows[0]["score"])

    def remaining(self, username: str, game_id: int) -> int:
        rows = self._exec(
            "SELECT COUNT(*) AS n FROM achievements a WHERE a.game_id = ? AND a.flags = ? AND a.id NOT IN "
            "(SELECT achievement_id FROM unlocks WHERE username = ?)",
            (game_id, FLAG_CORE, username.lower()),
        )
        return int(rows[0]["n"])

    # --- local leaderboards ------------------------------------------------------------------

    def submit_score(self, username: str, leaderboard_id: int, game_id: int | None, score: int,
                     lower_is_better: bool) -> int:
        """Keep the best score per user and leaderboard. Returns the user's best score."""
        user = username.lower()
        rows = self._exec(
            "SELECT score FROM lb_entries WHERE username = ? AND leaderboard_id = ?", (user, leaderboard_id)
        )
        best = rows[0]["score"] if rows else None
        better = best is None or (score < best if lower_is_better else score > best)
        if better:
            self._exec(
                "INSERT OR REPLACE INTO lb_entries (username, leaderboard_id, game_id, score, submitted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user, leaderboard_id, game_id, score, int(time.time())),
            )
            return score
        return best

    def leaderboard_entries(self, leaderboard_id: int, lower_is_better: bool) -> list[dict]:
        order = "ASC" if lower_is_better else "DESC"
        rows = self._exec(
            f"SELECT username, score, submitted_at FROM lb_entries WHERE leaderboard_id = ? "
            f"ORDER BY score {order}, submitted_at ASC",
            (leaderboard_id,),
        )
        return [dict(r) for r in rows]


def dumps(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"))
