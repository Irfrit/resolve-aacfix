#!/usr/bin/env bash
#
# dev-setup.sh -- populate vendor/ and prebuilt/ in a git checkout, so the
# patcher can run without downloading a release tarball.
#
#   scripts/dev-setup.sh [e9patch] [pylibs] [ffmpeg]      (default: all)
#
# This is the local equivalent of .github/workflows/release.yml.  The one thing
# it deliberately does NOT reproduce is the release's old-glibc container: libs
# built here carry your distro's glibc requirement and are fine for local
# testing, but are not what should be shipped.  scripts/check-elf-compat.sh
# will tell you.
#
set -euo pipefail

SELF="$(readlink -f "$0")"
HERE="$(cd "$(dirname "$SELF")" && pwd)"
ROOT="$(dirname "$HERE")"
# shellcheck source=versions.sh
. "$HERE/versions.sh"

info() { printf '\033[1m==>\033[0m %s\n' "$*"; }
msg()  { printf '  %s\n' "$*"; }
die()  { printf '\033[31m[error]\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"; }

# glibc of the toolchain we would link against.  Anything we ship has to load on
# Rocky Linux 8 (glibc 2.28) -- the oldest platform Resolve 21 supports -- so a
# build on a newer host is redone inside a container rather than shipped.
host_glibc() { ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$'; }
ver_key()    { awk -F. '{ printf "%d%03d\n", $1, $2 }' <<<"$1"; }
glibc_too_new() {
    [ "${AAC_NO_CONTAINER:-0}" = 1 ] && return 1
    [ "$(ver_key "$(host_glibc)")" -gt "$(ver_key "$MAX_GLIBC")" ]
}

do_e9patch() {
    need git
    if glibc_too_new; then
        info "host glibc $(host_glibc) > $MAX_GLIBC -- building e9patch in a glibc $MAX_GLIBC container"
        # Not exec: a bare `dev-setup.sh` run still has other components to do.
        "$HERE/old-glibc-build.sh" --mount "$ROOT" --env AAC_NO_CONTAINER=1 \
            -- "$SELF" e9patch
        "$ROOT/scripts/check-elf-compat.sh" "$MAX_GLIBC" \
            "$ROOT/vendor/e9patch/e9patch" "$ROOT/vendor/e9patch/e9tool"
        return
    fi
    need gcc; need g++; need make; need xxd
    local dst="$ROOT/vendor/e9patch"
    if [ ! -d "$dst/.git" ]; then
        info "Cloning e9patch @ ${E9PATCH_COMMIT:0:12}"
        rm -rf "$dst"; mkdir -p "$(dirname "$dst")"
        git clone -q "$E9PATCH_REPO" "$dst"
    fi
    git -C "$dst" fetch -q origin || true
    git -C "$dst" checkout -q "$E9PATCH_COMMIT"

    # e9patch's build.sh insists on a `markdown` binary (Debian/Ubuntu install
    # python3-markdown as `markdown_py`).  Give it one rather than patching
    # their build.
    local shim; shim="$(mktemp -d)"
    if ! command -v markdown >/dev/null 2>&1; then
        if command -v markdown_py >/dev/null 2>&1; then
            printf '#!/bin/sh\nexec markdown_py "$@"\n' > "$shim/markdown"
        else
            # docs only; a no-op keeps the build honest about what it needs
            printf '#!/bin/sh\ncat\n' > "$shim/markdown"
        fi
        chmod +x "$shim/markdown"
    fi

    info "Building e9patch"
    ( cd "$dst" && PATH="$shim:$PATH" ./build.sh >/dev/null )
    rm -rf "$shim"
    [ -x "$dst/e9tool" ] && [ -x "$dst/e9patch" ] || die "e9patch build produced no binaries"
    msg "vendor/e9patch/{e9patch,e9tool}"

    info "Compiling the trampoline"
    ( cd "$dst" && ./e9compile.sh "$ROOT/src/trampoline/aacadd.c" >/dev/null 2>&1 )
    mv -f "$dst/aacadd" "$ROOT/vendor/aacadd"
    rm -f "$dst/aacadd.o"
    check_trampoline "$ROOT/vendor/aacadd"
    msg "vendor/aacadd"

    "$ROOT/scripts/check-elf-compat.sh" "$MAX_GLIBC" \
        "$dst/e9patch" "$dst/e9tool"
}

# The trampoline is code spliced into /opt/resolve/bin/resolve, and e9tool
# resolves the names below out of it.  A missing symbol shows up as a cryptic
# e9tool failure at patch time, so assert the full set here instead.
check_trampoline() {
    local f="$1" got missing=""
    got="$(readelf --dyn-syms -W "$f" | awk '$4=="FUNC"{print $8}')"
    for s in $TRAMPOLINE_SYMS; do
        grep -qx "$s" <<<"$got" || missing="$missing $s"
    done
    [ -z "$missing" ] || die "trampoline is missing exported symbols:$missing"
}

do_pylibs() {
    need python3
    local dst="$ROOT/vendor/pylibs" tmp
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
    info "Vendoring capstone + pyelftools"
    # manylinux2014 == glibc 2.17, comfortably under our floor.  Vendoring them
    # means a release tarball needs nothing but a python3 interpreter.
    python3 -m pip download -q --only-binary=:all: \
        --platform manylinux2014_x86_64 --dest "$tmp" \
        capstone==5.0.9 pyelftools==0.33
    rm -rf "$dst"; mkdir -p "$dst"
    for w in "$tmp"/*.whl; do
        python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$w" "$dst"
    done
    rm -rf "$dst"/*.dist-info "$dst"/*.data
    PYTHONPATH="$dst" python3 -c 'import capstone, elftools' \
        || die "vendored pylibs do not import"
    msg "vendor/pylibs/{capstone,elftools}"
}

do_ffmpeg() {
    # Only git is needed on the host: build-ffmpeg.sh runs the compile inside a
    # glibc 2.28 container unless the host glibc is already that old, so the
    # toolchain lives in the image rather than here.
    need git
    local src="${FFMPEG_SRC:-$ROOT/_ffmpeg/src}"
    local prefix="${FFMPEG_PREFIX:-$ROOT/_ffmpeg/install}"
    if [ ! -d "$src/.git" ]; then
        info "Cloning FFmpeg $FFMPEG_TAG"
        mkdir -p "$(dirname "$src")"
        git clone -q --depth 1 --branch "$FFMPEG_TAG" "$FFMPEG_REPO" "$src"
    fi
    # Re-apply the AV3A backport from scratch each time, so a half-applied tree
    # from an interrupted run can't silently produce a lib without AV3A.
    info "Applying the AV3A demuxer backport"
    git -C "$src" checkout -q -- . && git -C "$src" clean -qfd
    git -C "$src" apply "$ROOT/src/ffmpeg/patches/0001-av3a-demuxer-backport.patch"

    info "Building FFmpeg (this takes a while)"
    FFMPEG_SRC="$src" \
    FFMPEG_BUILD="${FFMPEG_BUILD:-$ROOT/_ffmpeg/build}" \
    FFMPEG_PREFIX="$prefix" \
        "$ROOT/src/ffmpeg/build-ffmpeg.sh"

    mkdir -p "$ROOT/prebuilt/ffmpeg-aac"
    local lib
    for lib in $FFMPEG_LIBS; do
        cp -f "$prefix/lib/$lib" "$ROOT/prebuilt/ffmpeg-aac/$lib"
        msg "prebuilt/ffmpeg-aac/$lib"
    done
    "$ROOT/scripts/check-ffmpeg-libs.sh" "$ROOT/prebuilt/ffmpeg-aac"
}

what=("$@"); [ ${#what[@]} -gt 0 ] || what=(pylibs e9patch ffmpeg)
for w in "${what[@]}"; do
    case "$w" in
        e9patch) do_e9patch ;;
        pylibs)  do_pylibs ;;
        ffmpeg)  do_ffmpeg ;;
        *) die "unknown component: $w (want e9patch | pylibs | ffmpeg)" ;;
    esac
done
info "Done."
