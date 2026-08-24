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
# Paths come from the environment so this is usable from a checkout, from CI, or
# by hand:
#   FFMPEG_SRC     FFmpeg source tree (checked out at n6.0.1, patch applied)
#   FFMPEG_BUILD   scratch build directory
#   FFMPEG_PREFIX  install prefix; the libs land in $FFMPEG_PREFIX/lib
set -euo pipefail

: "${FFMPEG_SRC:?set FFMPEG_SRC to the patched FFmpeg n6.0.1 source tree}"
SRC="$FFMPEG_SRC"
BUILD="${FFMPEG_BUILD:-$PWD/_ffmpeg/build}"
PREFIX="${FFMPEG_PREFIX:-$PWD/_ffmpeg/install}"

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
