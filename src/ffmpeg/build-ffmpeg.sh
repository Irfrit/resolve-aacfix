#!/bin/bash
# Build FFmpeg 6.0.1 shared libs as drop-in replacements for the ones Blackmagic
# ships in /opt/resolve/libs, with AAC support re-enabled.
#
# Blackmagic's own configure line (recovered verbatim from the `--prefix` string
# embedded in every bundled lib) was:
#
#   --prefix=/media/datastore1/build/bmd-eng-ub_FTKZ/tmp/install/linux/ \
#   --enable-runtime-cpudetect --disable-lzma --disable-xlib --enable-shared \
#   --disable-programs --disable-doc --disable-avdevice --disable-postproc \
#   --disable-avfilter --disable-pixelutils --disable-static --disable-swresample \
#   --disable-iconv \
#   --disable-decoder='aac*' --disable-encoder='aac*,ac3*' --disable-parser='aac*' \
#   --disable-muxer='aac*,ac3*' --disable-demuxer='aac*,ac3' \
#   --extra-ldflags=-L<their zlib> --extra-cflags=-I<their zlib>
#
# We keep every flag identical except that the five aac/ac3 stripping flags are
# reduced to only the ac3 halves, so AAC decode/encode/parse/mux/demux comes back
# while AC-3 stays exactly as Blackmagic had it (decoder on, encoder/muxer/demuxer
# off).  Nothing else about the libraries changes -- that is what makes them a
# safe swap rather than "some other FFmpeg".
#
# The source tree must already have src/ffmpeg/patches/0001-av3a-demuxer-backport.patch
# applied; scripts/dev-setup.sh and the release workflow both do that.  Without it
# the build silently loses Blackmagic's AV3A / "Audio Vivid" demuxer.
#
# These libraries are installed into a working Resolve tree, so they must load
# on the oldest platform Resolve itself supports: Blackmagic's baseline for
# Resolve 21 is Rocky Linux 8, and Resolve's own binary references at most
# GLIBC_2.27.  A build on a current distro references GLIBC_2.35 and would
# install cleanly and then fail to load.
#
# So unless the host glibc is already old enough, this script re-runs itself
# inside a glibc 2.28 container and verifies the result before returning.  It
# refuses to produce libraries that would not load, rather than producing them
# and leaving the check to someone else.
#
# Paths come from the environment so this is usable from a checkout, from CI, or
# by hand:
#   FFMPEG_SRC     FFmpeg source tree (checked out at n6.0.1, patch applied)
#   FFMPEG_BUILD   scratch build directory
#   FFMPEG_PREFIX  install prefix; the libs land in $FFMPEG_PREFIX/lib
#   FFMPEG_NO_CONTAINER=1
#                  build with the host toolchain even if its glibc is newer.
#                  For iterating locally; the result is not shippable, and the
#                  glibc check at the end will say so.
set -euo pipefail

SELF="$(readlink -f "$0")"
REPO="$(cd "$(dirname "$SELF")/../.." && pwd)"

: "${FFMPEG_SRC:?set FFMPEG_SRC to the patched FFmpeg n6.0.1 source tree}"
SRC="$(readlink -f "$FFMPEG_SRC")"
BUILD="${FFMPEG_BUILD:-$PWD/_ffmpeg/build}"
PREFIX="${FFMPEG_PREFIX:-$PWD/_ffmpeg/install}"
mkdir -p "$BUILD" "$PREFIX"
BUILD="$(readlink -f "$BUILD")"
PREFIX="$(readlink -f "$PREFIX")"

MAX_GLIBC=2.28
FFMPEG_LIBS="libavcodec.so.60.3.100 libavformat.so.60.3.100 libavutil.so.58.2.100 libswscale.so.7.1.100"
if [ -r "$REPO/scripts/versions.sh" ]; then
    # shellcheck source=../../scripts/versions.sh
    . "$REPO/scripts/versions.sh"
fi

# Highest glibc this toolchain would link against.  ldd reports the runtime's
# version, which is what bounds the symbol versions the linker can emit.
host_glibc() { ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$'; }
ver_key() { awk -F. '{ printf "%d%03d\n", $1, $2 }' <<<"$1"; }

if [ "${FFMPEG_NO_CONTAINER:-0}" != 1 ] && \
   [ "$(ver_key "$(host_glibc)")" -gt "$(ver_key "$MAX_GLIBC")" ]; then
    if [ -x "$REPO/scripts/old-glibc-build.sh" ]; then
        printf '\033[1m==>\033[0m host glibc %s > %s -- building in a glibc %s container\n' \
            "$(host_glibc)" "$MAX_GLIBC" "$MAX_GLIBC" >&2
        exec "$REPO/scripts/old-glibc-build.sh" \
            --mount "$REPO" --mount "$SRC" --mount "$BUILD" --mount "$PREFIX" \
            --env "FFMPEG_SRC=$SRC" --env "FFMPEG_BUILD=$BUILD" \
            --env "FFMPEG_PREFIX=$PREFIX" --env FFMPEG_NO_CONTAINER=1 \
            -- "$SELF"
    fi
    printf '\033[33m[warn]\033[0m host glibc %s > %s and no scripts/old-glibc-build.sh\n' \
        "$(host_glibc)" "$MAX_GLIBC" >&2
    printf '        found -- building natively.  These libraries will NOT be shippable.\n' >&2
fi

[ -x "$SRC/configure" ] || { echo "no configure in $SRC" >&2; exit 1; }
grep -q AV_CODEC_ID_AVS3DA "$SRC/libavcodec/codec_id.h" || {
    echo "error: $SRC has no AV_CODEC_ID_AVS3DA -- the AV3A backport patch is not applied" >&2
    exit 1
}

mkdir -p "$BUILD"
cd "$BUILD"

"$SRC/configure" \
    --prefix="$PREFIX" \
    --enable-runtime-cpudetect \
    --disable-lzma \
    --disable-xlib \
    --enable-shared \
    --disable-programs \
    --disable-doc \
    --disable-avdevice \
    --disable-postproc \
    --disable-avfilter \
    --disable-pixelutils \
    --disable-static \
    --disable-swresample \
    --disable-iconv \
    --disable-encoder='ac3*' \
    --disable-muxer='ac3*' \
    --disable-demuxer='ac3'

# Blackmagic's libs carry RUNPATH=$ORIGIN so libavformat/libavcodec resolve each
# other from /opt/resolve/libs rather than from the system.  Getting a literal
# $ORIGIN through configure -> config.mak -> make -> sh unmangled is not possible
# with --extra-ldflags, so inject it into config.mak directly (single-quoted so
# the recipe shell leaves it alone; library.mak expands recipes twice, so four
# dollars are needed to end up with one).
sed -i "s|^LDFLAGS=|LDFLAGS= -Wl,-rpath,'\$\$\$\$ORIGIN' |" ffbuild/config.mak
grep -m1 '^LDFLAGS=' ffbuild/config.mak

make -j"$(nproc)"
make install

# Verify what we actually produced.  A library that references a newer glibc
# than Resolve's own baseline is worse than a failed build: it installs fine and
# then Resolve will not start.
if [ -x "$REPO/scripts/check-elf-compat.sh" ]; then
    printf '\n\033[1m==>\033[0m glibc floor\n'
    libs=()
    for lib in $FFMPEG_LIBS; do libs+=("$PREFIX/lib/$lib"); done
    if ! "$REPO/scripts/check-elf-compat.sh" "$MAX_GLIBC" "${libs[@]}"; then
        echo >&2
        echo "These libraries reference a newer glibc than Resolve's own baseline" >&2
        echo "and would fail to load on a platform Resolve supports." >&2
        [ "${FFMPEG_NO_CONTAINER:-0}" = 1 ] && \
            echo "(FFMPEG_NO_CONTAINER=1 was set -- unset it to build in a glibc $MAX_GLIBC container.)" >&2
        exit 1
    fi
fi
