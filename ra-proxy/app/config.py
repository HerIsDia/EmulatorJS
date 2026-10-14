"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

USER_AGENT = f"EmulatorJS-RALocal/{__version__} (+https://github.com/HerIsDia/EmulatorJS/tree/main/ra-proxy)"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _parse_users(raw: str) -> dict[str, str]:
    """Parse ``LOCAL_USERS`` ("alice:secret,bob:hunter2") into {lowercase name: password}."""
    users = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        name, password = entry.split(":", 1)
        if name.strip():
            users[name.strip().lower()] = password
    return users


@dataclass
class Settings:
    data_dir: Path = Path("/data")
    # Upstream RetroAchievements account, only used to READ achievement definitions.
    ra_host: str = "https://retroachievements.org"
    ra_media_host: str = "https://media.retroachievements.org"
    ra_username: str = ""
    ra_token: str = ""
    ra_password: str = ""
    # Never contact retroachievements.org; serve only what is already cached.
    offline: bool = False
    # How long cached definitions are considered fresh (hours). 0 = never refresh once cached.
    cache_ttl_hours: float = 24.0
    # Minimum delay between two upstream requests (seconds).
    upstream_min_interval: float = 1.0
    # Local accounts. Empty = open mode (any user name is accepted, the token still protects writes).
    local_users: dict[str, str] = field(default_factory=dict)
    secret_key: str = ""
    allowed_origins: list[str] = field(default_factory=lambda: ["*"])
    # Public base URL of the proxy as seen by the browser (used to rewrite badge URLs).
    public_url: str = ""
    # Optional RomM instance used to resolve ROM hashes to RetroAchievements game ids.
    romm_url: str = ""
    romm_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        origins = [o.strip() for o in _env("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
        return cls(
            data_dir=Path(_env("DATA_DIR", "/data")),
            ra_host=_env("RA_HOST", "https://retroachievements.org").rstrip("/"),
            ra_media_host=_env("RA_MEDIA_HOST", "https://media.retroachievements.org").rstrip("/"),
            ra_username=_env("RA_USERNAME"),
            ra_token=_env("RA_TOKEN"),
            ra_password=_env("RA_PASSWORD"),
            offline=_env_bool("OFFLINE"),
            cache_ttl_hours=float(_env("CACHE_TTL_HOURS", "24") or 24),
            upstream_min_interval=float(_env("UPSTREAM_MIN_INTERVAL", "1") or 1),
            local_users=_parse_users(_env("LOCAL_USERS")),
            secret_key=_env("SECRET_KEY"),
            allowed_origins=origins or ["*"],
            public_url=_env("PUBLIC_URL").rstrip("/"),
            romm_url=_env("ROMM_URL").rstrip("/"),
            romm_token=_env("ROMM_TOKEN"),
        )

    @property
    def upstream_configured(self) -> bool:
        return bool(self.ra_username and (self.ra_token or self.ra_password))

    @property
    def auth_mode(self) -> str:
        return "users" if self.local_users else "open"

    def ensure_secret(self) -> None:
        """Load or create the HMAC key used to sign local tokens."""
        if self.secret_key:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "secret.key"
        if path.exists():
            self.secret_key = path.read_text().strip()
        if not self.secret_key:
            self.secret_key = secrets.token_hex(32)
            path.write_text(self.secret_key)
            try:
                path.chmod(0o600)
            except OSError:
                pass

    def local_token(self, username: str) -> str:
        """Token handed to the emulator for a local user. It is NOT a RetroAchievements token."""
        digest = hmac.new(self.secret_key.encode(), username.lower().encode(), hashlib.sha256)
        return digest.hexdigest()[:32]

    def check_password(self, username: str, password: str) -> bool:
        if self.auth_mode == "open":
            return bool(username)
        expected = self.local_users.get(username.lower())
        return expected is not None and hmac.compare_digest(expected, password)

    def check_token(self, username: str, token: str) -> bool:
        if not username or not token:
            return False
        if self.auth_mode == "users" and username.lower() not in self.local_users:
            return False
        return hmac.compare_digest(self.local_token(username), token)
