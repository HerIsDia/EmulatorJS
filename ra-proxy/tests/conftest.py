from __future__ import annotations

from urllib.parse import parse_qsl

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

GAME_HASH = "0123456789abcdef0123456789abcdef"
MEDIA = "https://media.retroachievements.org"

ACHIEVEMENT_SETS = {
    "Success": True,
    "GameId": 1,
    "Title": "Test Game",
    "ConsoleId": 7,
    "ImageIconUrl": f"{MEDIA}/Images/000001.png",
    "RichPresenceGameId": 1,
    "RichPresencePatch": "Display:\nPlaying",
    "Sets": [{
        "AchievementSetId": 10,
        "GameId": 1,
        "Title": None,
        "Type": "core",
        "ImageIconUrl": f"{MEDIA}/Images/000001.png",
        "Achievements": [
            {"ID": 101, "Title": "First steps", "Description": "Start the game", "Flags": 3, "Points": 5,
             "MemAddr": "0xH0010=1", "Author": "dev", "BadgeName": "12345", "Created": 0, "Modified": 0,
             "Type": "progression", "Rarity": 50.0, "RarityHardcore": 20.0,
             "BadgeURL": f"{MEDIA}/Badge/12345.png", "BadgeLockedURL": f"{MEDIA}/Badge/12345_lock.png"},
            {"ID": 102, "Title": "Boss", "Description": "Beat the boss", "Flags": 3, "Points": 10,
             "MemAddr": "0xH0020=1", "Author": "dev", "BadgeName": "12346", "Created": 0, "Modified": 0,
             "Type": "win_condition", "Rarity": 10.0, "RarityHardcore": 5.0,
             "BadgeURL": f"{MEDIA}/Badge/12346.png", "BadgeLockedURL": f"{MEDIA}/Badge/12346_lock.png"},
            {"ID": 103, "Title": "Unofficial", "Description": "Work in progress", "Flags": 5, "Points": 1,
             "MemAddr": "0xH0030=1", "Author": "dev", "BadgeName": "00000", "Created": 0, "Modified": 0,
             "Type": "", "Rarity": 0, "RarityHardcore": 0,
             "BadgeURL": f"{MEDIA}/Badge/00000.png", "BadgeLockedURL": f"{MEDIA}/Badge/00000_lock.png"},
        ],
        "Leaderboards": [
            {"ID": 201, "Title": "Fastest", "Description": "Speedrun", "Mem": "STA:0=1::CAN:0=2::SUB:0=3::VAL:0",
             "Format": "FRAMES", "LowerIsBetter": True, "Hidden": False},
        ],
    }],
}

PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeRetroAchievements:
    """Stands in for retroachievements.org and records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []
        self.media_requests: list[str] = []
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("offline", request=request)
        if request.url.host == "media.retroachievements.org":
            self.media_requests.append(request.url.path)
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        params = dict(parse_qsl(request.content.decode()))
        self.requests.append(params)
        r = params.get("r")
        if r == "login2":
            if params.get("u") == "uploader" and params.get("p") == "ra-password":
                return httpx.Response(200, json={"Success": True, "User": "uploader", "Token": "real-ra-token"})
            return httpx.Response(401, json={"Success": False, "Error": "Invalid user/password combination."})
        if r == "gameid":
            return httpx.Response(200, json={"Success": True, "GameID": 1 if params.get("m") == GAME_HASH else 0})
        if r in ("achievementsets", "patch"):
            if params.get("u") != "uploader" or params.get("t") != "real-ra-token":
                return httpx.Response(401, json={"Success": False, "Error": "Invalid user/token combination."})
            return httpx.Response(200, json=ACHIEVEMENT_SETS)
        return httpx.Response(500, json={"Success": False, "Error": f"fake server got unexpected r={r}"})


@pytest.fixture
def fake_ra() -> FakeRetroAchievements:
    return FakeRetroAchievements()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        ra_username="uploader",
        ra_password="ra-password",
        local_users={"alice": "local-pass"},
        secret_key="test-secret",
        upstream_min_interval=0,
        public_url="http://proxy.test",
    )


@pytest.fixture
def make_client(fake_ra):
    clients = []

    def factory(settings: Settings, romm_handler=None) -> TestClient:
        romm_transport = httpx.MockTransport(romm_handler) if romm_handler else None
        app = create_app(settings, httpx.MockTransport(fake_ra.handler), romm_transport)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client, settings) -> TestClient:
    return make_client(settings)


def login(client: TestClient, user: str = "alice", password: str = "local-pass") -> str:
    response = client.post("/dorequest.php", data={"r": "login2", "u": user, "p": password})
    assert response.status_code == 200, response.text
    return response.json()["Token"]
