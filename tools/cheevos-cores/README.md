# Cores with RetroAchievements support

EmulatorJS cores are RetroArch (Emscripten) linked with a libretro core. Achievements are evaluated by
RetroArch's rcheevos integration, which the official builds do not compile (`HAVE_CHEEVOS` is off).
This folder builds cores with it enabled, for use with the local proxy in [`ra-proxy/`](../../ra-proxy).

## Build a core

Requirements: [emsdk](https://github.com/emscripten-core/emsdk) with Emscripten **3.1.74** (the version
EmulatorJS/build uses), `git`, `jq`, `7z`, `zip`.

```sh
source /path/to/emsdk/emsdk_env.sh
tools/cheevos-cores/build-cheevos-core.sh fceumm             # or snes9x, gambatte, mgba, genesis_plus_gx...
cp cheevos-build/build/output/fceumm*-wasm.data data/cores/
```

The script runs the official [EmulatorJS/build](https://github.com/EmulatorJS/build) pipeline with two patches:

- [`patches/RetroArch-Makefile.emulatorjs.patch`](patches/RetroArch-Makefile.emulatorjs.patch), for
  [EmulatorJS/RetroArch](https://github.com/EmulatorJS/RetroArch):
  - `HAVE_CHEEVOS ?= 1`;
  - `CFLAGS += $(INCLUDE_DIRS)`: `Makefile.emulatorjs` never passed `Makefile.common`'s include dirs to the
    compiler, so `deps/rcheevos/include` was missing. With the default options `INCLUDE_DIRS` only holds
    dirs that were already passed by hand, so this does not change existing builds;
  - optional `HAVE_BUILTINZLIB=1`: use RetroArch's bundled zlib instead of downloading the Emscripten port
    (`--builtin-zlib`), for networks that block GitHub archive downloads.
- [`patches/build-core-json-cheevos.patch`](patches/build-core-json-cheevos.patch), for EmulatorJS/build:
  writes `"cheevos": true` into the packaged `core.json`, which EmulatorJS reads to know the core supports
  achievements.

The flag costs about 60 KB per core. Nothing happens at runtime unless `cheevos_enable` is set, which
EmulatorJS only does when `EJS_retroAchievements` is configured.

## End-to-end test (no RetroAchievements account needed)

1. `python3 tools/cheevos-cores/make-test-rom.py roms/cheevos-test.nes` builds a NES ROM that flips `$0010`
   from 0 to 1 after ~3 seconds, and prints its RetroAchievements hash.
2. `python tools/cheevos-cores/fake-ra-proxy.py --hash <hash>` runs `ra-proxy` on port 8081 against a fake
   retroachievements.org whose game 1 has achievement 101 = `0xH0010=1` (needs ra-proxy's dependencies).
3. `python3 serve.py 8000` from the repository root, with the `fceumm` cores built above in `data/cores/`
   (and `data/cores/reports/fceumm.json`).
4. `node tools/cheevos-cores/e2e.cjs` (Playwright) opens [`e2e.html`](e2e.html) and waits until the core
   itself awards achievement 101: no request is simulated. Pass
   `http://localhost:8000/tools/cheevos-cores/e2e.html?threads=1` as first argument to test the threaded cores.
   The proxy keeps unlocks, so restart it (fresh data dir) between runs.

Expected output:

```
core declares cheevos: true
toast: ACHIEVEMENT UNLOCKED | First steps (5) | Start the game
unlocks stored by the proxy: [101]
[INFO] [RCHEEVOS] Using host: http://localhost:8081
[INFO] [RCHEEVOS] Generated hash 439f8908cbabae3bd013344ae0abaebe
[INFO] [RCHEEVOS] alice logged in successfully
[INFO] [RCHEEVOS] Identified game: 1 "Test Game" (439f8908cbabae3bd013344ae0abaebe)
...
```

Headless Chromium has no WebGL2, so EmulatorJS asks for the `-legacy` core variant: build both (the script does).
