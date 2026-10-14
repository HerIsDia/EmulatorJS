# EmulatorJS local RetroAchievements proxy

A small self-hosted service that lets EmulatorJS earn **local, casual** RetroAchievements:

- It speaks the RetroAchievements "Connect" protocol (`/dorequest.php`) that RetroArch/rcheevos uses, so a
  core built with `HAVE_CHEEVOS` can be pointed at it with `cheevos_custom_host`.
- It **reads** achievement definitions (`gameid`, `achievementsets`, `patch`, `hashlibrary`) from
  retroachievements.org with one configured account, caches them in SQLite and serves them offline afterwards.
- Everything the player does (logins, sessions, pings, unlocks, leaderboard entries) is answered and stored
  **locally**. Unlocks are **never sent** to retroachievements.org, and hardcore unlocks are refused.
- A REST + Server-Sent Events API lets the EmulatorJS interface list achievements and show unlock notifications.
- Badge images are cached from media.retroachievements.org.

It is inspired by [RAOfflineProxy](https://github.com/misantronic/RAOfflineProxy), but unlike it nothing is
ever synchronised back to RetroAchievements: this is a "mini RetroAchievements" for your own server.

> **Status: prototype.** EmulatorJS cores are not yet built with achievements support (`HAVE_CHEEVOS`), so
> achievements do not trigger in-game yet. See [docs/RETROACHIEVEMENTS.md](../docs/RETROACHIEVEMENTS.md) for
> the roadmap. Before any public deployment, please read the "RetroAchievements rules" section below.

## Run it

```sh
cd ra-proxy
cp docker-compose.example.yml docker-compose.yml   # edit the environment section
docker compose up -d --build
curl http://localhost:8080/api/status
```

Without Docker (Python 3.10+):

```sh
pip install .
DATA_DIR=./data RA_USERNAME=me RA_PASSWORD=secret LOCAL_USERS=alice:change-me python -m app.main
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RA_USERNAME` | | RetroAchievements account used **only to read** achievement definitions. |
| `RA_TOKEN` | | Its Connect API token (preferred). |
| `RA_PASSWORD` | | Used once to obtain a token if `RA_TOKEN` is not set. Never sent to the browser. |
| `LOCAL_USERS` | *(empty)* | Local players, `name:password,name2:password2`. Empty = open mode: any name is accepted (trusted networks only). |
| `ALLOWED_ORIGINS` | `*` | Origins allowed to call the proxy from the browser (CORS), comma separated. |
| `PUBLIC_URL` | *(request URL)* | Public URL of the proxy, used to rewrite badge URLs. |
| `ROOT_PATH` | | Set when a reverse proxy serves the proxy under a sub-path (e.g. `/ra-proxy`). |
| `OFFLINE` | `false` | Never contact retroachievements.org; serve the cache only. |
| `CACHE_TTL_HOURS` | `24` | Age after which cached definitions are refreshed (when online). `0` = never refresh. |
| `UPSTREAM_MIN_INTERVAL` | `1` | Minimum seconds between two requests to retroachievements.org. |
| `ROMM_URL`, `ROMM_TOKEN` | | Optional, experimental: resolve ROM hashes through RomM (`/api/roms/by-hash`). |
| `DATA_DIR` | `/data` | SQLite database, cached images and the token signing key. |
| `SECRET_KEY` | *(generated)* | Key used to sign local tokens. Generated in `DATA_DIR/secret.key` if empty. |

## Use it from EmulatorJS

```js
EJS_retroAchievements = {
    proxyUrl: "https://example.com/ra-proxy", // where this service is reachable from the browser
    username: "alice",                         // a LOCAL_USERS account (not your RetroAchievements login)
    password: "change-me",                     // or token: "..." (from POST /api/login)
    gameId: 1234,                              // optional RetroAchievements game id, e.g. RomM's ra_id
    notifications: true
};
```

EmulatorJS then writes `cheevos_enable`, `cheevos_custom_host`, `cheevos_username`, `cheevos_token`/`cheevos_password`
and `cheevos_hardcore_mode_enable = "false"` to `retroarch.cfg`, adds an "Achievements" button and a
"RetroAchievements" settings section, and shows a notification for each unlock.

If the page sends `Cross-Origin-Embedder-Policy: require-corp` (threaded cores, RomM), serving the proxy on the
same origin (for example `/ra-proxy/` behind your reverse proxy) is the simplest setup. Otherwise the proxy
already sends CORS headers and `Cross-Origin-Resource-Policy: cross-origin`.

## API

| Endpoint | Purpose |
|---|---|
| `GET/POST /dorequest.php` | RetroAchievements Connect protocol for the emulator. |
| `GET /api/status` | Version and configuration summary. |
| `POST /api/login` | `{username, password}` → `{username, token}`. |
| `GET /api/games/{id}?u=&t=` | Game, achievements with local unlock state, summary. `&all=1` includes unofficial ones. |
| `GET /api/games/by-hash/{md5}` | RetroAchievements game id for a ROM hash. |
| `GET /api/users/{name}/unlocks?t=` | All local unlocks and local score. |
| `GET /api/events?u=&t=` | Server-Sent Events: `session`, `unlock`, `leaderboard`. |
| `GET /Badge/{name}.png`, `/Images/…`, `/UserPic/…` | Cached images. |

Request types handled locally: `login2`, `startsession`, `ping`, `postactivity`, `awardachievement(s)`,
`submitlbentry`, `unlocks`, `getfriendlist`. Any other type is answered with HTTP 501 and is not forwarded.

## RetroAchievements rules

- Only read requests are ever sent upstream (enforced in `app/upstream.py`), with a dedicated User-Agent
  (`EmulatorJS-RALocal/<version>`), a rate limit and a persistent cache.
- Unlocks stay local and hardcore is refused, so nothing affects RetroAchievements leaderboards or profiles.
- Reading the Connect API from a client RetroAchievements has not approved is still a grey area. Please
  contact the RetroAchievements team before running this for anyone but yourself
  (see [docs/RETROACHIEVEMENTS.md](../docs/RETROACHIEVEMENTS.md)).

## Development

```sh
pip install -e ".[test]"
pytest
```

The tests replay the request sequence of rcheevos' `rc_client` against a fake retroachievements.org and check
that nothing but reads ever reaches it.

---

## En bref (français)

Un petit service auto-hébergé qui joue le rôle d'un « mini RetroAchievements » local pour EmulatorJS.
Il récupère (en lecture seule, avec cache) les définitions des succès sur retroachievements.org, et garde
les succès débloqués **uniquement en local** : rien n'est jamais renvoyé sur RetroAchievements, et le mode
hardcore est refusé. Les cores EmulatorJS doivent encore être recompilés avec `HAVE_CHEEVOS` pour que les
succès se déclenchent en jeu ; voir [docs/RETROACHIEVEMENTS.md](../docs/RETROACHIEVEMENTS.md).
