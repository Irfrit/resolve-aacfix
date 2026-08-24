#!/usr/bin/env bash
#
# check-elf-compat.sh MAX_GLIBC FILE...
#
# Fail if any FILE references a glibc symbol version newer than MAX_GLIBC.
#
# Why this exists: everything we ship gets installed next to, or run against, a
# Resolve install.  Resolve's own binary needs at most GLIBC_2.27 and Blackmagic
# support Rocky Linux 8 (glibc 2.28), so a lib or tool built on a modern distro
# -- which happily emits GLIBC_2.35 references -- would simply fail to load on a
# platform Resolve itself supports.  Building in an old-glibc container is the
# fix; this is the check that proves the container did its job.
#
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
MAX_GLIBCXX=""
# shellcheck source=versions.sh
[ -r "$HERE/versions.sh" ] && . "$HERE/versions.sh"

[ $# -ge 2 ] || { echo "usage: $0 MAX_GLIBC FILE..." >&2; exit 2; }
MAX="$1"; shift

# "2.28" -> 2028 so a plain numeric compare orders 2.9 before 2.28
key() { awk -F. '{ printf "%d%03d\n", $1, $2 }' <<<"$1"; }
MAXK="$(key "$MAX")"

rc=0
for f in "$@"; do
    if [ ! -e "$f" ]; then echo "missing: $f" >&2; rc=1; continue; fi
    # Undefined versioned symbols live in .gnu.version_r, which readelf -V dumps.
    worst=""; worstk=0
    while read -r v; do
        k="$(key "$v")"
        if [ "$k" -gt "$worstk" ]; then worstk="$k"; worst="$v"; fi
    done < <(readelf -V "$f" 2>/dev/null |
             grep -oE 'GLIBC_[0-9]+\.[0-9]+' | sed 's/GLIBC_//' | sort -uV)

    if [ -z "$worst" ]; then
        printf '  %-40s no versioned glibc references\n' "$(basename "$f")"
    elif [ "$worstk" -gt "$MAXK" ]; then
        printf '  %-40s GLIBC_%s  > %s  FAIL\n' "$(basename "$f")" "$worst" "$MAX" >&2
        rc=1
    else
        printf '  %-40s GLIBC_%s  (<= %s)  ok\n' "$(basename "$f")" "$worst" "$MAX"
    fi

    # A C++ binary can be glibc-clean and still refuse to load because it wants
    # a newer libstdc++ than the target has -- so check that ceiling too.
    if [ -n "$MAX_GLIBCXX" ] && readelf -dW "$f" 2>/dev/null | grep -q 'libstdc++'; then
        cxxworst=""; cxxworstk=0
        while read -r v; do
            k="$(printf '%s' "$v" | awk -F. '{ printf "%d%03d%03d\n", $1, $2, $3 }')"
            if [ "$k" -gt "$cxxworstk" ]; then cxxworstk="$k"; cxxworst="$v"; fi
        done < <(readelf -V "$f" 2>/dev/null |
                 grep -oE 'GLIBCXX_[0-9]+\.[0-9]+\.[0-9]+' | sed 's/GLIBCXX_//' | sort -uV)
        maxcxxk="$(printf '%s' "$MAX_GLIBCXX" | awk -F. '{ printf "%d%03d%03d\n", $1, $2, $3 }')"
        if [ -z "$cxxworst" ]; then
            :
        elif [ "$cxxworstk" -gt "$maxcxxk" ]; then
            printf '  %-40s GLIBCXX_%s > %s  FAIL\n' "" "$cxxworst" "$MAX_GLIBCXX" >&2
            rc=1
        else
            printf '  %-40s GLIBCXX_%s (<= %s)  ok\n' "" "$cxxworst" "$MAX_GLIBCXX"
        fi
    fi
done
exit $rc
