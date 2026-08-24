#!/usr/bin/env bash
#
# check-ffmpeg-libs.sh DIR
#
# Assert that the four libraries in DIR really are drop-in replacements for the
# ones Blackmagic bundles, and really do have AAC.
#
# These libs get copied over files inside a working Resolve install.  Every check
# here corresponds to a way that could go wrong quietly: a wrong soname loads
# nothing, a missing RUNPATH makes libavformat bind to the system libavcodec, a
# stray --enable-gpl changes the license, a missing AV3A demuxer silently drops a
# format Resolve supports, and a build without AAC leaves the binary patch
# routing audio to a decoder that isn't there.
#
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# An explicitly-exported MAX_GLIBC wins, so make-release.sh's
# --allow-newer-glibc escape hatch reaches this check too.
_max_glibc_in="${MAX_GLIBC:-}"
# shellcheck source=versions.sh
. "$HERE/versions.sh"
[ -n "$_max_glibc_in" ] && MAX_GLIBC="$_max_glibc_in"

DIR="${1:?usage: $0 DIR}"
rc=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*" >&2; rc=1; }

# --- 1. the exact four files, with the sonames Resolve's binaries link against
declare -A WANT_SONAME=(
    [libavcodec.so.60.3.100]=libavcodec.so.60
    [libavformat.so.60.3.100]=libavformat.so.60
    [libavutil.so.58.2.100]=libavutil.so.58
    [libswscale.so.7.1.100]=libswscale.so.7
)
for lib in $FFMPEG_LIBS; do
    f="$DIR/$lib"
    [ -f "$f" ] || { bad "missing $lib"; continue; }
    got="$(readelf -dW "$f" | sed -n 's/.*SONAME.*\[\(.*\)\].*/\1/p')"
    [ "$got" = "${WANT_SONAME[$lib]}" ] \
        && ok "$lib soname=$got" \
        || bad "$lib soname=$got want=${WANT_SONAME[$lib]}"
    # $ORIGIN makes the set resolve each other from /opt/resolve/libs.  Without
    # it libavformat would bind to whatever libavcodec the system provides.
    readelf -dW "$f" | grep -qE 'RUNPATH|RPATH.*\$ORIGIN' \
        && ok "$lib RUNPATH=\$ORIGIN" \
        || bad "$lib has no \$ORIGIN RUNPATH"
done

# --- 2. glibc floor
libpaths=()
for lib in $FFMPEG_LIBS; do libpaths+=("$DIR/$lib"); done
"$HERE/check-elf-compat.sh" "$MAX_GLIBC" "${libpaths[@]}" || rc=1

# --- 3. codec inventory -- functional, by loading the libraries.  FFmpeg's
# codec/format tables have hidden visibility, so `nm -D` finds none of them;
# the only honest check is to dlopen and ask.
"$HERE/ffmpeg-inventory.py" "$DIR" || rc=1

# --- 4. licensing: a plain LGPL build, no GPL/nonfree, no external codec libs
cfg="$(strings -a "$DIR/libavcodec.so.60.3.100" | grep -m1 -- '--prefix=' || true)"
case "$cfg" in
    *--enable-gpl*|*--enable-nonfree*)
        bad "configure line contains --enable-gpl/--enable-nonfree: $cfg" ;;
    *) ok "LGPL build (no --enable-gpl / --enable-nonfree)" ;;
esac

exit $rc
