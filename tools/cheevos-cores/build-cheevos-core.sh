#!/bin/bash
# Build EmulatorJS cores with RetroAchievements support (RetroArch HAVE_CHEEVOS).
#
# Uses the official EmulatorJS/build pipeline with the patches in ./patches applied:
#   - RetroArch-Makefile.emulatorjs.patch: enables HAVE_CHEEVOS, passes INCLUDE_DIRS to the compiler
#     (needed for deps/rcheevos/include) and adds the optional HAVE_BUILTINZLIB switch.
#   - build-core-json-cheevos.patch: writes "cheevos": true into each core's core.json.
#
# Requirements: emscripten 3.1.74 in PATH (source emsdk_env.sh), git, jq, 7z, zip.
#
# Usage: build-cheevos-core.sh <core> [--builtin-zlib] [--workdir=DIR]
#   <core>          a core name from EmulatorJS/build's cores.json (e.g. fceumm, snes9x, gambatte, mgba)
#   --builtin-zlib  use RetroArch's bundled zlib instead of downloading the emscripten zlib port
#   --workdir=DIR   where to clone and build (default: ./cheevos-build)
#
# Output: <workdir>/build/output/<core>*-wasm.data, to copy into EmulatorJS's data/cores/.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
core=""
workdir="$PWD/cheevos-build"
builtin_zlib=0

for arg in "$@"; do
    case "$arg" in
        --builtin-zlib) builtin_zlib=1 ;;
        --workdir=*) workdir="${arg#*=}" ;;
        -*) echo "Unknown option $arg" >&2; exit 1 ;;
        *) core="$arg" ;;
    esac
done

if [ -z "$core" ]; then
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
fi

for tool in emcc git jq 7z zip; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Missing required tool: $tool (for emcc, source emsdk_env.sh first)" >&2
        exit 1
    fi
done

# Apply a patch unless it is already applied.
apply_patch() {
    local repo="$1" patch="$2"
    if git -C "$repo" apply --reverse --check "$patch" >/dev/null 2>&1; then
        echo "Patch already applied: $(basename "$patch")"
    else
        git -C "$repo" apply "$patch"
        echo "Applied: $(basename "$patch")"
    fi
}

mkdir -p "$workdir"
cd "$workdir"

if [ ! -d build ]; then
    git clone --depth 1 https://github.com/EmulatorJS/build.git build
fi
apply_patch build "$here/patches/build-core-json-cheevos.patch"

# build.sh clones RetroArch into compile/RetroArch if missing; clone it first so it can be patched.
mkdir -p build/compile
if [ ! -d build/compile/RetroArch ]; then
    git clone --depth 1 https://github.com/EmulatorJS/RetroArch.git build/compile/RetroArch
fi
apply_patch build/compile/RetroArch "$here/patches/RetroArch-Makefile.emulatorjs.patch"

export HAVE_CHEEVOS=1
if [ "$builtin_zlib" = 1 ]; then
    export HAVE_BUILTINZLIB=1
fi

cd build
bash build.sh --core="$core"

echo
echo "Done. Cores with achievements support:"
ls -1 "$workdir/build/output/${core}"*-wasm.data
