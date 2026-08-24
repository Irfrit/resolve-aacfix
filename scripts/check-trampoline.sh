#!/usr/bin/env bash
#
# check-trampoline.sh FILE
#
# The trampoline is the code e9patch splices into /opt/resolve/bin/resolve, and
# e9tool resolves each patch site's handler out of it by name.  A missing symbol
# shows up only as a cryptic e9tool failure at patch time, and an EXTRA symbol
# means the binary was not built from the source in this repo -- which is how a
# stale prebuilt trampoline exporting a long-deleted `about_html` went unnoticed
# for a while.  So: assert the exact set.
#
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# shellcheck source=versions.sh
. "$HERE/versions.sh"

F="${1:?usage: $0 FILE}"
[ -f "$F" ] || { echo "no such file: $F" >&2; exit 1; }

# stdlib.c contributes `nanosleep`; everything else must be ours.
ALLOWED_EXTRA="nanosleep"

got="$(readelf --dyn-syms -W "$F" | awk '$4=="FUNC" && $8!="" {print $8}' | sort -u)"
rc=0
for s in $TRAMPOLINE_SYMS; do
    if grep -qx "$s" <<<"$got"; then
        printf '  \033[32mok\033[0m    exports %s\n' "$s"
    else
        printf '  \033[31mFAIL\033[0m  missing %s\n' "$s" >&2; rc=1
    fi
done
while read -r s; do
    [ -n "$s" ] || continue
    grep -qx "$s" <<<"$(printf '%s\n' $TRAMPOLINE_SYMS $ALLOWED_EXTRA)" && continue
    printf '  \033[31mFAIL\033[0m  unexpected export %s (not built from src/trampoline/aacadd.c?)\n' "$s" >&2
    rc=1
done <<<"$got"
exit $rc
