#!/usr/bin/env bash
#
# make-release.sh [-o OUTDIR] [VERSION]
#
# Assemble the self-contained release tarball: the repo plus the built payload
# in vendor/ and prebuilt/.  A user should be able to download it, extract it,
# and run ./aac-fix install with nothing installed but python3 and binutils.
#
# Run scripts/dev-setup.sh (or let the release workflow do it) first; this only
# packages what is already built, and refuses if anything is missing.
#
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT="$(dirname "$HERE")"
# shellcheck source=versions.sh
. "$HERE/versions.sh"

OUTDIR="$ROOT/dist"
VERSION=""
ALLOW_NEWER_GLIBC=0
while [ $# -gt 0 ]; do
    case "$1" in
        -o|--out) OUTDIR="$2"; shift 2 ;;
        # For iterating on packaging locally.  A build on a modern distro
        # produces libraries needing GLIBC_2.35, which would not load on the
        # oldest platform Resolve supports -- so this must never be used for a
        # tarball anyone else runs.  Real releases come from the Rocky 8
        # container in .github/workflows/release.yml.
        --allow-newer-glibc) ALLOW_NEWER_GLIBC=1; shift ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) VERSION="$1"; shift ;;
    esac
done
if [ -z "$VERSION" ]; then
    VERSION="$(git -C "$ROOT" describe --tags --always --dirty 2>/dev/null || echo dev)"
fi

info() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

NAME="resolve-aacfix-${VERSION}-linux-x86_64"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
DEST="$STAGE/$NAME"
mkdir -p "$DEST"

info "Staging $NAME"

# --- the repo itself.  git archive keeps .gitignore/.gitattributes honest and
# guarantees we never ship a stray build artifact from the working tree.
if git -C "$ROOT" rev-parse --verify -q HEAD >/dev/null 2>&1 && \
   [ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
    git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$DEST"
else
    # dirty tree or no commits yet: copy the tracked-ish set by hand
    ( cd "$ROOT" && tar -c \
        --exclude=.git --exclude=vendor --exclude=prebuilt \
        --exclude=dist --exclude=_ffmpeg --exclude=__pycache__ \
        --exclude='*.pyc' --exclude='*.so' --exclude=testdata \
        --exclude=.pytest_cache --exclude=.ruff_cache --exclude='*.whl' \
        . ) | tar -x -C "$DEST"
fi

# --- payload: only what the installer needs, not the whole e9patch checkout
info "Adding payload"
mkdir -p "$DEST/vendor/e9patch" "$DEST/prebuilt/ffmpeg-aac"
for f in e9patch e9tool; do
    [ -x "$ROOT/vendor/e9patch/$f" ] || die "missing vendor/e9patch/$f (run scripts/dev-setup.sh e9patch)"
    cp -p "$ROOT/vendor/e9patch/$f" "$DEST/vendor/e9patch/$f"
done
# e9patch is GPL-3.0; ship its license text alongside the binaries.
cp -p "$ROOT/vendor/e9patch/LICENSE" "$DEST/vendor/e9patch/LICENSE" 2>/dev/null || \
    die "missing vendor/e9patch/LICENSE"
printf 'e9patch %s\nbuilt from %s\n' "$E9PATCH_COMMIT" "$E9PATCH_REPO" \
    > "$DEST/vendor/e9patch/SOURCE"

[ -f "$ROOT/vendor/aacadd" ] || die "missing vendor/aacadd (run scripts/dev-setup.sh e9patch)"
cp -p "$ROOT/vendor/aacadd" "$DEST/vendor/aacadd"

[ -d "$ROOT/vendor/pylibs/capstone" ] || die "missing vendor/pylibs (run scripts/dev-setup.sh pylibs)"
cp -a "$ROOT/vendor/pylibs" "$DEST/vendor/pylibs"
find "$DEST/vendor/pylibs" -name '__pycache__' -type d -prune -exec rm -rf {} +

for lib in $FFMPEG_LIBS; do
    [ -f "$ROOT/prebuilt/ffmpeg-aac/$lib" ] || die "missing prebuilt/ffmpeg-aac/$lib (run scripts/dev-setup.sh ffmpeg)"
    cp -p "$ROOT/prebuilt/ffmpeg-aac/$lib" "$DEST/prebuilt/ffmpeg-aac/$lib"
done
printf 'FFmpeg %s, LGPL-2.1+, built with src/ffmpeg/build-ffmpeg.sh\nsource: %s\n' \
    "$FFMPEG_TAG" "$FFMPEG_REPO" > "$DEST/prebuilt/ffmpeg-aac/SOURCE"

# --- re-verify the payload we just staged.  A release that ships libs without
# AAC, or tools that will not load on Rocky 8, is worse than no release: the
# patcher would succeed and the user would get silence.
info "Verifying payload"
if [ "$ALLOW_NEWER_GLIBC" = 1 ]; then
    MAX_GLIBC=99.99
    printf '\033[33m[warn]\033[0m --allow-newer-glibc: NOT a shippable tarball.\n' >&2
    printf '       Binaries built here may fail to load on Rocky Linux 8,\n' >&2
    printf '       which is the oldest platform Resolve 21 supports.\n' >&2
fi
"$HERE/check-elf-compat.sh" "$MAX_GLIBC" \
    "$DEST/vendor/e9patch/e9patch" "$DEST/vendor/e9patch/e9tool"
MAX_GLIBC="$MAX_GLIBC" "$HERE/check-ffmpeg-libs.sh" "$DEST/prebuilt/ffmpeg-aac"
"$HERE/check-trampoline.sh" "$DEST/vendor/aacadd"

# --- integrity manifest.  aac-patch-tree refuses to run if this does not match,
# so a tampered or truncated download cannot be applied to /opt/resolve.
info "Writing SHA256SUMS"
( cd "$DEST" && find vendor prebuilt -type f ! -name SHA256SUMS -print0 \
    | sort -z | xargs -0 sha256sum > SHA256SUMS )
( cd "$DEST" && sha256sum --quiet -c SHA256SUMS ) || die "manifest does not verify"

mkdir -p "$OUTDIR"
info "Packing"
tar -czf "$OUTDIR/$NAME.tar.gz" -C "$STAGE" "$NAME"
( cd "$OUTDIR" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )

info "Wrote:"
ls -1sh "$OUTDIR/$NAME.tar.gz" "$OUTDIR/$NAME.tar.gz.sha256" | sed 's/^/  /'
