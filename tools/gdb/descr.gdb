# ---------------------------------------------------------------------------
# WARNING: every address below is pinned to DaVinci Resolve Studio 21.0.3/21.0.4.
# On any other build they will land mid-instruction and gdb will report nonsense.
# Re-derive them first:  python -m aacpatch.locate /opt/resolve/bin/resolve
# See docs/tooling.md for which site each address corresponds to.
#
# Run via tools/run-resolve-gdb (Resolve must be gdb's child under ptrace_scope=1).
# ---------------------------------------------------------------------------
# At F, after the descriptor pointer is computed (0x5b5e0b5), dump the stream
# descriptor fields for each track.  Compare AC3 (works) vs AAC to see what codec
# value AAC carries and why F's jump table / dispatch skips it.
set pagination off
set confirm off
set breakpoint pending on
set logging file /tmp/gdb-resolve.log
set logging overwrite on
set logging on

# 0x5b5e0b5: r15 = stream array, r13 = index*0x30 ; descriptor = r15+r13
break *0x5b5e0b5
commands
  silent
  set $d = $r15 + $r13
  printf "F descriptor idx=%d: +8(type)=%d  +0x10(codec)=%d  +0xc=%d  +0x14(rate)=%d\n", \
    $r12, *(int *)($d + 8), *(int *)($d + 0x10), *(int *)($d + 0xc), *(int *)($d + 0x14)
  continue
end

break *0x5b5e2e5
commands
  silent
  printf "  --> DISPATCH codec_type=%d\n", *(int *)($r15 + 8)
  continue
end

run
