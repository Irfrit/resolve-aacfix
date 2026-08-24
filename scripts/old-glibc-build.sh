#!/usr/bin/env bash
#
# old-glibc-build.sh [--mount DIR]... [--env K=V]... -- COMMAND [ARG...]
#
# Run COMMAND inside a glibc 2.28 container (see GLIBC_CONTAINER in
# versions.sh), with the given directories bind-mounted at their *same absolute
# paths* so the command needs no path translation.
#
# Why: anything we install next to Resolve has to load on the oldest platform
# Resolve itself supports.  Resolve's own binary references at most GLIBC_2.27,
# and Blackmagic's baseline for Resolve 21 is Rocky Linux 8 -- glibc 2.28.  A
# library built on a current distro references GLIBC_2.35 and would install
# cleanly and then fail to load, leaving Resolve unable to start.  There is no
# reliable way to retarget an older glibc from a newer one: symbol-version
# pinning covers the symbols you thought of and silently substitutes older libm
# implementations, and it cannot express `fstat64` at all (glibc < 2.33 has only
# `__fxstat64`).  Compiling against the real 2.28 headers and symbols is the only
# approach that is correct by construction and stays correct.
#
# The first run builds a small derived image with the build dependencies baked
# in; later runs reuse it, so only the first pays for the package install.
#
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# shellcheck source=versions.sh
. "$HERE/versions.sh"

info() { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

MOUNTS=()
ENVS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --mount) MOUNTS+=("$2"); shift 2 ;;
        --env)   ENVS+=("$2"); shift 2 ;;
        --)      shift; break ;;
        *)       die "unknown option: $1" ;;
    esac
done
[ $# -gt 0 ] || die "no command given (use -- COMMAND ...)"

# --- container runtime
RUNTIME="${CONTAINER_RUNTIME:-}"
if [ -z "$RUNTIME" ]; then
    for r in podman docker; do
        command -v "$r" >/dev/null 2>&1 || continue
        "$r" info >/dev/null 2>&1 || continue
        RUNTIME="$r"; break
    done
fi
[ -n "$RUNTIME" ] || die "need a working podman or docker to build against glibc $MAX_GLIBC.
       Install one, or run inside a glibc <= $MAX_GLIBC environment yourself and
       set FFMPEG_NO_CONTAINER=1 (the result is only shippable if it really is
       that old -- scripts/check-elf-compat.sh will tell you)."

# --- builder image: base + build deps, cached
if ! "$RUNTIME" image inspect "$GLIBC_BUILDER_IMAGE" >/dev/null 2>&1; then
    info "Building $GLIBC_BUILDER_IMAGE from $GLIBC_CONTAINER (first run only)"
    "$RUNTIME" build --platform linux/amd64 -t "$GLIBC_BUILDER_IMAGE" - >&2 <<DOCKERFILE
FROM $GLIBC_CONTAINER
# \`which\` and \`xxd\` (vim-common) are NOT in the rockylinux:8 base image, and
# e9patch's build.sh probes for its tools with \`which\`: without it the build
# fails claiming gcc is missing when gcc is installed and fine.
# nasm is not in the base repos -- it lives in PowerTools (renamed CRB in
# later EL8-alikes), so try both names before installing.  Without it FFmpeg
# silently configures with --disable-x86asm and builds a far slower library
# that no longer matches what Blackmagic shipped.
RUN dnf -y -q install dnf-plugins-core \
    && (dnf config-manager --set-enabled powertools \
        || dnf config-manager --set-enabled crb \
        || dnf config-manager --set-enabled PowerTools) \
    && dnf -y -q install \
        git make gcc gcc-c++ nasm binutils zlib-devel \
        diffutils findutils perl python3 tar xz which vim-common \
    && nasm -v \
    && dnf clean all
DOCKERFILE
fi

# --- mounts, deduplicated and with nested paths dropped (the parent covers them)
declare -a ARGS=()
canon=()
for m in ${MOUNTS[@]+"${MOUNTS[@]}"}; do
    [ -d "$m" ] || die "not a directory to mount: $m"
    canon+=("$(cd "$m" && pwd)")
done
# sort shortest-first so a parent is seen before its children
readarray -t canon < <(printf '%s\n' ${canon[@]+"${canon[@]}"} | awk '{print length, $0}' | sort -n | cut -d' ' -f2-)
kept=()
for m in ${canon[@]+"${canon[@]}"}; do
    skip=0
    for k in ${kept[@]+"${kept[@]}"}; do
        case "$m" in "$k"/*) skip=1; break ;; "$k") skip=1; break ;; esac
    done
    [ "$skip" = 1 ] || kept+=("$m")
done
for m in ${kept[@]+"${kept[@]}"}; do ARGS+=(-v "$m:$m"); done

for e in ${ENVS[@]+"${ENVS[@]}"}; do ARGS+=(-e "$e"); done

# Run as root so the build can write anywhere it likes, then hand every mounted
# tree back to the invoking user -- otherwise a local build leaves root-owned
# files scattered through the caller's checkout.
UID_GID="$(id -u):$(id -g)"
CMD="$(printf '%q ' "$@")"
CHOWN=""
for m in ${kept[@]+"${kept[@]}"}; do CHOWN+="chown -R $UID_GID $(printf '%q' "$m"); "; done

exec "$RUNTIME" run --rm --platform linux/amd64 \
    "${ARGS[@]}" -w "$PWD" "$GLIBC_BUILDER_IMAGE" \
    bash -c "set -e; trap '$CHOWN' EXIT; $CMD"
