"""Replays the request sequence RetroArch (rcheevos rc_client) sends to its achievements host."""

from __future__ import annotations

from conftest import GAME_HASH, PNG, login

from app.config import Settings
from app.upstream import READ_REQUESTS


def connect(client, token, **params):
    return client.post("/dorequest.php", data={"u": "alice", "t": token, **params})


def test_login_is_local_and_never_forwarded(client, fake_ra):
    token = login(client)
    assert token
    assert client.post("/dorequest.php", data={"r": "login2", "u": "alice", "p": "nope"}).status_code == 401
    assert client.post("/dorequest.php", data={"r": "login2", "u": "mallory", "p": "local-pass"}).status_code == 401
    # Token login (what RetroArch does once it has a token).
    again = client.post("/dorequest.php", data={"r": "login2", "u": "alice", "t": token})
    assert again.json()["Success"] is True
    assert not any(r.get("r") == "login2" for r in fake_ra.requests)


def test_full_session_keeps_unlocks_local(client, fake_ra):
    token = login(client)

    gameid = client.post("/dorequest.php", data={"r": "gameid", "m": GAME_HASH}).json()
    assert gameid == {"Success": True, "GameID": 1}

    sets = connect(client, token, r="achievementsets", m=GAME_HASH, v="2")
    assert sets.status_code == 200
    body = sets.json()
    assert body["Sets"][0]["Achievements"][0]["MemAddr"] == "0xH0010=1"
    # Badge URLs point at the proxy so images are cached locally.
    assert body["Sets"][0]["Achievements"][0]["BadgeURL"] == "http://proxy.test/Badge/12345.png"
    # Upstream was called with the configured RA account, never with the local token.
    upstream_sets = [r for r in fake_ra.requests if r.get("r") == "achievementsets"]
    assert upstream_sets and upstream_sets[0]["u"] == "uploader" and upstream_sets[0]["t"] == "real-ra-token"

    session = connect(client, token, r="startsession", g="1", h="0", m=GAME_HASH).json()
    assert session["Success"] is True and session["Unlocks"] == []

    award = connect(client, token, r="awardachievement", a="101", h="0", m=GAME_HASH, v="x").json()
    assert award == {"Success": True, "Score": 5, "SoftcoreScore": 5, "AchievementID": 101,
                     "AchievementsRemaining": 1}
    # Awarding twice is harmless.
    assert connect(client, token, r="awardachievement", a="101", h="0").json()["Success"] is True

    session = connect(client, token, r="startsession", g="1", h="0").json()
    assert [u["ID"] for u in session["Unlocks"]] == [101]

    assert connect(client, token, r="ping", g="1", m="Playing").json() == {"Success": True}
    unlocks = connect(client, token, r="unlocks", g="1", h="0").json()
    assert unlocks["UserUnlocks"] == [101]

    # The whole point: nothing but reads ever reached retroachievements.org.
    sent = {r.get("r") for r in fake_ra.requests}
    assert sent <= READ_REQUESTS | {"login2"}
    assert "awardachievement" not in sent and "startsession" not in sent and "ping" not in sent


def test_definitions_are_cached(client, fake_ra):
    token = login(client)
    for _ in range(3):
        assert connect(client, token, r="achievementsets", m=GAME_HASH, v="2").status_code == 200
        assert client.post("/dorequest.php", data={"r": "gameid", "m": GAME_HASH}).json()["GameID"] == 1
    assert sum(1 for r in fake_ra.requests if r.get("r") == "achievementsets") == 1
    # The hash was learned from the achievementsets answer, so gameid never went upstream.
    assert sum(1 for r in fake_ra.requests if r.get("r") == "gameid") == 0


def test_offline_serves_cache(make_client, settings, fake_ra):
    client = make_client(settings)
    token = login(client)
    assert connect(client, token, r="achievementsets", m=GAME_HASH, v="2").status_code == 200

    fake_ra.down = True
    settings.cache_ttl_hours = 0.000001  # everything is stale, upstream unreachable -> stale cache
    assert connect(client, token, r="achievementsets", m=GAME_HASH, v="2").status_code == 200
    missing = connect(client, token, r="achievementsets", m="f" * 32, v="2")
    assert missing.status_code == 503 and missing.json()["Success"] is False


def test_offline_mode_never_contacts_upstream(make_client, settings, fake_ra):
    settings.offline = True
    client = make_client(settings)
    token = login(client)
    assert connect(client, token, r="achievementsets", m=GAME_HASH, v="2").status_code == 503
    assert fake_ra.requests == []


def test_hardcore_is_refused(client):
    token = login(client)
    response = connect(client, token, r="awardachievement", a="101", h="1")
    assert response.status_code == 403
    assert response.json()["Code"] == "hardcore_not_supported"


def test_requires_local_token(client):
    login(client)
    assert client.post("/dorequest.php", data={"r": "startsession", "u": "alice", "t": "bad", "g": "1"}).status_code == 401
    assert client.post("/dorequest.php", data={"r": "achievementsets", "u": "alice", "t": "bad",
                                               "m": GAME_HASH}).status_code == 401


def test_unknown_requests_are_not_forwarded(client, fake_ra):
    token = login(client)
    response = connect(client, token, r="submitticket", i="101")
    assert response.status_code == 501
    assert fake_ra.requests == []


def test_leaderboards_are_local(client, fake_ra):
    token = login(client)
    connect(client, token, r="achievementsets", m=GAME_HASH, v="2")
    first = connect(client, token, r="submitlbentry", i="201", s="500").json()
    assert first["Response"]["BestScore"] == 500
    better = connect(client, token, r="submitlbentry", i="201", s="300").json()  # lower is better
    assert better["Response"]["BestScore"] == 300
    worse = connect(client, token, r="submitlbentry", i="201", s="900").json()
    assert worse["Response"]["BestScore"] == 300 and worse["Response"]["RankInfo"]["Rank"] == 1
    assert "submitlbentry" not in {r.get("r") for r in fake_ra.requests}


def test_rest_api_and_events(client):
    token = login(client)
    service = client.app.state.service
    queue = service.broker.subscribe("alice")

    game = client.get("/api/games/1", params={"u": "alice", "t": token}).json()
    assert game["game"]["title"] == "Test Game"
    assert [a["id"] for a in game["achievements"]] == [101, 102]  # unofficial hidden by default
    assert game["summary"] == {"total": 2, "unlocked": 0, "points": 15, "pointsUnlocked": 0}

    connect(client, token, r="awardachievement", a="102", h="0")
    event, data = queue.get_nowait()
    assert event == "unlock" and data["id"] == 102 and data["title"] == "Boss"

    game = client.get("/api/games/1", params={"u": "alice", "t": token}).json()
    assert game["summary"]["unlocked"] == 1 and game["summary"]["pointsUnlocked"] == 10
    assert client.get("/api/games/1", params={"u": "alice", "t": "bad"}).status_code == 401

    unlocks = client.get("/api/users/alice/unlocks", params={"t": token}).json()
    assert unlocks["score"] == 10 and unlocks["unlocks"][0]["id"] == 102

    status = client.get("/api/status").json()
    assert status["unlocksForwarded"] is False and status["authMode"] == "users"

    rest_login = client.post("/api/login", json={"username": "alice", "password": "local-pass"}).json()
    assert rest_login["token"] == token


def test_media_cache(client, fake_ra):
    first = client.get("/Badge/12345.png")
    assert first.status_code == 200 and first.content == PNG
    assert client.get("/Badge/12345.png").content == PNG
    assert fake_ra.media_requests == ["/Badge/12345.png"]
    assert client.get("/Badge/..%2Fsecret.png").status_code == 404
    assert first.headers["Cross-Origin-Resource-Policy"] == "cross-origin"


def test_open_mode_accepts_any_user(make_client, tmp_path):
    settings = Settings(data_dir=tmp_path, secret_key="s", upstream_min_interval=0)
    client = make_client(settings)
    token = login(client, "anyone", "whatever")
    assert client.post("/dorequest.php", data={"r": "ping", "u": "anyone", "t": token}).json()["Success"] is True


def test_romm_resolves_hashes(make_client, settings, fake_ra):
    import httpx

    settings.romm_url = "http://romm.test"
    settings.romm_token = "romm-token"
    seen = []

    def romm(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.url.params.get("ra_hash"), request.headers.get("authorization")))
        return httpx.Response(200, json={"id": 42, "ra_id": 1, "ra_hash": GAME_HASH})

    client = make_client(settings, romm)
    assert client.post("/dorequest.php", data={"r": "gameid", "m": GAME_HASH}).json()["GameID"] == 1
    assert seen == [("/api/roms/by-hash", GAME_HASH, "Bearer romm-token")]
    assert not any(r.get("r") == "gameid" for r in fake_ra.requests)
