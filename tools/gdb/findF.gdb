# ---------------------------------------------------------------------------
# WARNING: every address below is pinned to DaVinci Resolve Studio 21.0.3/21.0.4.
# On any other build they will land mid-instruction and gdb will report nonsense.
# Re-derive them first:  python -m aacpatch.locate /opt/resolve/bin/resolve
# See docs/tooling.md for which site each address corresponds to.
#
# Run via tools/run-resolve-gdb (Resolve must be gdb's child under ptrace_scope=1).
# ---------------------------------------------------------------------------
# At the per-track decoder call (0x5b269d4, `call [rax+0x38]`), log the resolved
# function address F and continue.  Also break the dispatch to see which tracks
# reach it.  This tells us F's entry so we can find the internal AAC filter.
set pagination off
set confirm off
set breakpoint pending on
set logging file /tmp/gdb-resolve.log
set logging overwrite on
set logging on

break *0x5b269d4
commands
  silent
  set $vt = *(long *)$rax
  printf "call-F: F=0x%lx\n", *(long *)($vt + 0x38)
  continue
end

break *0x5b5e2e5
commands
  silent
  printf "  --> dispatch reached, codec_type=%d\n", *(int *)($r15 + 8)
  continue
end

run
