# RetroAchievements in EmulatorJS (local proxy): design and roadmap

Tracking issue upstream: [EmulatorJS/EmulatorJS#589](https://github.com/EmulatorJS/EmulatorJS/issues/589).

## Why a proxy

- A browser cannot call RetroAchievements' emulator endpoint (`dorequest.php`) directly. It sends no CORS
  headers, and RetroAchievements identifies clients by their User-Agent, which a browser cannot set.
- The trigger conditions of achievements (`MemAddr`) are only available through that endpoint, with a user token
  (`r=achievementsets` / `r=patch`). The public Web API does not expose them.
- RetroAchievements' rules require a recognised client for hardcore unlocks, and offline unlocks to be synced
  securely ([hardcore compliance](https://docs.retroachievements.org/general/hardcore-compliance-requirements.html)).
  Keeping unlocks local and casual avoids touching RetroAchievements data at all.

## Architecture

```
Browser ─ EmulatorJS (JS) ── writes retroarch.cfg (cheevos_*) ─┐
   └─ WASM core (RetroArch + rcheevos) ── emscripten_fetch ──> <proxy>/dorequest.php
                                                                │
ra-proxy (FastAPI + SQLite, Docker) ────────────────────────────┘
   ├─ cached reads: gameid / achievementsets / patch / badges ──> retroachievements.org (dedicated User-Agent)
   ├─ answered locally: login2 / startsession / ping
   ├─ stored locally, never forwarded: awardachievement / submitlbentry
   └─ REST + Server-Sent Events ──> EmulatorJS interface (achievement list, notifications)
```

Achievements are evaluated inside the core by rcheevos, as in desktop RetroArch. RetroArch already knows each core's
memory map (`rc_libretro`), hashes the ROM (`rc_hash`) and handles notifications, so EmulatorJS does not need
to read emulated memory from JavaScript.

`cheevos_custom_host` may include a path: rcheevos builds the URL as `<host>/dorequest.php`
(`rc_api_url_build_dorequest_url` in `src/api/rc_api_common.c`), so `https://example.com/ra-proxy` works.

## What exists today

- `ra-proxy/`: the proxy service (see its README), with tests that replay the rc_client request sequence.
- `data/src/achievements.js`: the `EJS_retroAchievements` option, the `retroarch.cfg` lines, the event stream,
  the "Achievements" button and popup, the unlock notification, and the "RetroAchievements" settings section
  (enable/disable and notifications).
- `core.json` may declare `"cheevos": true`; EmulatorJS exposes it as `coreSupportsCheevos` and otherwise shows
  "This core does not support achievements yet" in the achievements popup.
- An `achievementUnlocked` event is emitted (`EJS_emulator.on("achievementUnlocked", fn)`).

## Phase 2: cores with achievements support (not done)

Cores are built in [EmulatorJS/build](https://github.com/EmulatorJS/build) from the EmulatorJS RetroArch fork.

1. Enable `HAVE_CHEEVOS=1` in the fork's `Makefile.emulatorjs` (it currently enables `HAVE_NETWORKING`
   but not `HAVE_CHEEVOS`). Upstream RetroArch's `Makefile.common` compiles `deps/rcheevos` when the
   flag is set.
2. Check that HTTP goes through `tasks/task_http_emscripten.c` (`emscripten_fetch`) and works in both threaded and
   non-threaded builds.
3. Add `"cheevos": true` to `core.json` for the cores that were built this way.
4. Check RetroArch's on-screen notifications in the WASM build. If they are disabled, rely on the proxy's event
   stream, which EmulatorJS already uses.
5. Start with cartridge systems whose hash is simple: NES, SNES, Game Boy, Game Boy Color, Game Boy Advance,
   Mega Drive.
6. Test against a real game with the proxy, then measure the per-frame cost of `rc_client_do_frame`.

## Phase 3: RomM

RomM already stores the RetroAchievements hash and game id of each ROM (`Rom.ra_hash`, `Rom.ra_id`) and embeds
EmulatorJS in `frontend/src/views/Player/EmulatorJS/Player.vue`.

1. Run `ra-proxy` next to RomM in `docker-compose`, and expose it on the same origin (e.g. `/ra-proxy/`) through the
   reverse proxy, because RomM's player pages send `Cross-Origin-Embedder-Policy: require-corp`.
2. Set `window.EJS_retroAchievements = { proxyUrl: "/ra-proxy", username, token, gameId: rom.ra_id }` in
   `Player.vue`. The token could come from a small RomM endpoint mapping RomM users to proxy users.
3. Optionally set `ROMM_URL`/`ROMM_TOKEN` on the proxy so it resolves hashes through RomM instead of
   retroachievements.org.
4. Use an EmulatorJS build with achievement-enabled cores (phase 2) by mounting it at
   `/var/www/html/assets/emulatorjs`, with netplay disabled (otherwise RomM loads the nightly CDN build instead).
5. Show local unlocks in RomM's game page through `GET /api/users/{name}/unlocks`.

## Before any public deployment: contact RetroAchievements

Even though no unlock is ever sent, the proxy reads the Connect API with an unapproved client. Ask the team first:
Discord `#coders` / RAdmin ([standalone integration policy](https://docs.retroachievements.org/general/standalone-support.html),
[Connect API](https://api-docs.retroachievements.org/connect/standalone.html)). Suggested message:

> Hi! We are prototyping achievement support for EmulatorJS (browser emulator, embedded by RomM) through a small
> self-hosted proxy. It only *reads* achievement sets (`gameid`, `achievementsets`) with one account, with a
> dedicated User-Agent (`EmulatorJS-RALocal/x.y.z`), a rate limit and a persistent cache. Unlocks are stored
> locally and are never submitted, and hardcore is disabled. Would this use of the Connect API be acceptable?
> If so, are there conditions you would like us to follow (rate limits, User-Agent, caching)?
> Source: https://github.com/HerIsDia/EmulatorJS/tree/main/ra-proxy

## Phase 4 (optional, only with RetroAchievements' approval)

Sync casual unlocks to RetroAchievements like RAOfflineProxy does: signed queue, `o` = seconds since the unlock,
`v` validation hash. This is out of scope unless RetroAchievements explicitly agrees.
