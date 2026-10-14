"""FastAPI application: a local stand-in for retroachievements.org.

* ``/dorequest.php`` speaks the RetroAchievements "Connect" protocol used by rcheevos/RetroArch.
  Reads (game ids, achievement definitions) are cached from retroachievements.org; everything the
  player does (unlocks, leaderboard entries, pings) is answered and stored locally and is NEVER
  forwarded upstream.
* ``/api/*`` is a small REST + Server-Sent Events API for the EmulatorJS interface (and RomM).
* ``/Badge``, ``/Images``, ``/UserPic`` cache images from media.retroachievements.org.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from . import __version__
from .config import USER_AGENT, Settings
from .events import Broker
from .romm import RommClient
from .service import ReadFailure, Service
from .store import Store
from .upstream import READ_REQUESTS, Upstream

log = logging.getLogger("ra-proxy")

_MEDIA_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,64}\.png$")
_SSE_KEEPALIVE_SECONDS = 15.0


def _error(message: str, code: str, status: int) -> JSONResponse:
    return JSONResponse({"Success": False, "Error": message, "Code": code}, status_code=status)


def _uint(value: str | None) -> int:
    try:
        number = int(value or 0)
    except ValueError:
        return 0
    return max(number, 0)


def create_app(settings: Settings | None = None,
               upstream_transport: httpx.AsyncBaseTransport | None = None,
               romm_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_secret()
    store = Store(settings.data_dir / "ra-proxy.sqlite3")
    upstream = Upstream(settings, store, upstream_transport)
    romm = RommClient(settings, romm_transport)
    service = Service(settings, store, upstream, romm, Broker())
    media_client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(20.0),
                                     transport=upstream_transport, follow_redirects=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.auth_mode == "open":
            log.warning("LOCAL_USERS is empty: open mode, any user name is accepted")
        if not settings.upstream_configured:
            log.warning("no RetroAchievements account configured: only cached games will work")
        yield
        await upstream.close()
        await romm.close()
        await media_client.aclose()
        store.close()

    app = FastAPI(title="EmulatorJS local RetroAchievements proxy", version=__version__, lifespan=lifespan)
    app.state.service = service

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def cross_origin_resource_policy(request: Request, call_next):
        # Pages running EmulatorJS with threads use COEP "require-corp"; let them load our resources.
        response = await call_next(request)
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        return response

    def public_base(request: Request) -> str:
        return settings.public_url or str(request.base_url).rstrip("/")

    def user_from(params: dict[str, str]) -> str | None:
        username = params.get("u", "")
        return username if settings.check_token(username, params.get("t", "")) else None

    # --- RetroAchievements Connect protocol ----------------------------------------------------

    @app.api_route("/dorequest.php", methods=["GET", "POST"])
    async def dorequest(request: Request) -> Response:
        params = dict(request.query_params)
        if request.method == "POST":
            body = (await request.body()).decode("utf-8", "replace")
            params.update(parse_qsl(body, keep_blank_values=True))
        request_type = params.get("r", "")

        if request_type == "login2":
            return login(params)

        if request_type in READ_REQUESTS:
            if request_type in ("patch", "achievementsets") and not user_from(params):
                return _error("Invalid user/token combination.", "invalid_credentials", 401)
            try:
                text = await service.read(request_type, params)
            except ReadFailure as exc:
                return _error(f"Not available from the local proxy: {exc}", "not_cached", 503)
            return Response(service.rewrite_media(text, public_base(request)), media_type="application/json")

        username = user_from(params)
        if username is None:
            return _error("Invalid user/token combination.", "invalid_credentials", 401)

        if request_type == "startsession":
            game_id = _uint(params.get("g"))
            hardcore = params.get("h") == "1"
            unlocks = [] if hardcore else [
                {"ID": u["achievement_id"], "When": u["unlocked_at"]} for u in store.unlocks(username, game_id)
            ]
            store.kv_set(f"current_game:{username.lower()}", str(game_id))
            service.broker.publish(username, "session", {"gameId": game_id})
            return JSONResponse({"Success": True, "Unlocks": unlocks, "HardcoreUnlocks": [],
                                 "ServerNow": int(time.time())})

        if request_type in ("ping", "postactivity"):
            return JSONResponse({"Success": True})

        if request_type == "awardachievement":
            if params.get("h") == "1":
                return _error("Hardcore unlocks are not supported by the local proxy.", "hardcore_not_supported", 403)
            achievement_id = _uint(params.get("a"))
            if not achievement_id:
                return _error("Missing achievement id.", "invalid_parameter", 400)
            return JSONResponse(service.award(username, achievement_id, public_base(request)))

        if request_type == "awardachievements":
            if params.get("h") == "1":
                return _error("Hardcore unlocks are not supported by the local proxy.", "hardcore_not_supported", 403)
            ids = [_uint(a) for a in params.get("a", "").split(",")]
            existing = {u["achievement_id"] for u in store.unlocks(username)}
            awarded = [a for a in ids if a and a not in existing]
            for achievement_id in awarded:
                service.award(username, achievement_id, public_base(request))
            score = store.score(username)
            return JSONResponse({"Success": True, "Score": score, "SoftcoreScore": score,
                                 "ExistingIDs": sorted(existing.intersection(ids)), "SuccessfulIDs": awarded})

        if request_type == "submitlbentry":
            leaderboard_id = _uint(params.get("i"))
            try:
                score = int(params.get("s", ""))
            except ValueError:
                return _error("Missing score.", "invalid_parameter", 400)
            if not leaderboard_id:
                return _error("Missing leaderboard id.", "invalid_parameter", 400)
            return JSONResponse(service.submit_score(username, leaderboard_id, score))

        if request_type == "unlocks":
            game_id = _uint(params.get("g"))
            ids = [] if params.get("h") == "1" else [u["achievement_id"] for u in store.unlocks(username, game_id)]
            return JSONResponse({"Success": True, "UserUnlocks": ids, "GameID": game_id, "HardcoreMode": False})

        if request_type == "getfriendlist":
            return JSONResponse({"Success": True, "Friends": []})

        return _error(f"Request type '{request_type}' is not supported by the local proxy.", "unsupported", 501)

    def login(params: dict[str, str]) -> JSONResponse:
        username = params.get("u", "")
        if params.get("p") is not None and "t" not in params:
            ok = settings.check_password(username, params.get("p", ""))
        else:
            ok = settings.check_token(username, params.get("t", ""))
        if not ok:
            return _error("Invalid user/password combination. Please try again.", "invalid_credentials", 401)
        score = store.score(username)
        return JSONResponse({
            "Success": True, "User": username, "Token": settings.local_token(username),
            "Score": score, "SoftcoreScore": score, "Messages": 0, "Permissions": 1, "AccountType": "Registered",
        })

    # --- REST API for the EmulatorJS interface -------------------------------------------------

    @app.get("/api/status")
    async def status() -> dict:
        return {
            "name": "ejs-ra-proxy",
            "version": __version__,
            "authMode": settings.auth_mode,
            "upstream": upstream.available,
            "offline": settings.offline,
            "romm": romm.enabled,
            "unlocksForwarded": False,
        }

    @app.post("/api/login")
    async def api_login(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            payload = {}
        username = str(payload.get("username", ""))
        if not settings.check_password(username, str(payload.get("password", ""))):
            return _error("Invalid user/password combination.", "invalid_credentials", 401)
        return JSONResponse({"username": username, "token": settings.local_token(username)})

    @app.get("/api/games/by-hash/{md5}")
    async def game_by_hash(md5: str) -> JSONResponse:
        if not re.fullmatch(r"[0-9a-fA-F]{32}", md5):
            return _error("Invalid hash.", "invalid_parameter", 400)
        try:
            body = json.loads(await service.read("gameid", {"m": md5.lower()}))
        except ReadFailure as exc:
            return _error(str(exc), "not_cached", 503)
        return JSONResponse({"hash": md5.lower(), "gameId": body.get("GameID") or 0})

    @app.get("/api/games/{game_id}")
    async def game(game_id: int, request: Request) -> JSONResponse:
        username = user_from(dict(request.query_params))
        if username is None:
            return _error("Invalid user/token combination.", "invalid_credentials", 401)
        if not await service.ensure_definitions(game_id):
            return _error("Game not cached and RetroAchievements unreachable.", "not_cached", 503)
        include_unofficial = request.query_params.get("all") == "1"
        return JSONResponse(service.game_view(username, game_id, public_base(request), include_unofficial))

    @app.get("/api/users/{username}/unlocks")
    async def user_unlocks(username: str, request: Request) -> JSONResponse:
        params = dict(request.query_params)
        params["u"] = username
        if user_from(params) is None:
            return _error("Invalid user/token combination.", "invalid_credentials", 401)
        base = public_base(request)
        unlocks = [
            service.achievement_view(store.achievement(u["achievement_id"]), u["achievement_id"], True,
                                     u["unlocked_at"], base)
            for u in store.unlocks(username)
        ]
        return JSONResponse({"username": username, "score": store.score(username), "unlocks": unlocks})

    @app.get("/api/events")
    async def events(request: Request) -> Response:
        username = user_from(dict(request.query_params))
        if username is None:
            return _error("Invalid user/token combination.", "invalid_credentials", 401)
        queue = service.broker.subscribe(username)

        async def stream():
            try:
                yield "retry: 5000\n\n"
                while True:
                    try:
                        event, data = await asyncio.wait_for(queue.get(), _SSE_KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        yield ": keepalive\n\n"
                        continue
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
            finally:
                service.broker.unsubscribe(username, queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- cached images -------------------------------------------------------------------------

    async def media(kind: str, name: str) -> Response:
        if not _MEDIA_NAME.match(name):
            return Response(status_code=404)
        path = settings.data_dir / "media" / kind / name
        if not path.exists():
            if not upstream.available:
                return Response(status_code=404)
            try:
                response = await media_client.get(f"{settings.ra_media_host}/{kind}/{name}")
            except httpx.HTTPError:
                return Response(status_code=502)
            if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/"):
                return Response(status_code=404)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/Badge/{name}")
    async def badge(name: str) -> Response:
        return await media("Badge", name)

    @app.get("/Images/{name}")
    async def image(name: str) -> Response:
        return await media("Images", name)

    @app.get("/UserPic/{name}")
    async def user_pic(name: str) -> Response:
        return await media("UserPic", name)

    return app


def run() -> None:
    import os

    import uvicorn

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")),
                root_path=os.environ.get("ROOT_PATH", ""), proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    run()
