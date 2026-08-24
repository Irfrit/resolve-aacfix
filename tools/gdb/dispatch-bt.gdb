# ---------------------------------------------------------------------------
# WARNING: every address below is pinned to DaVinci Resolve Studio 21.0.3/21.0.4.
# On any other build they will land mid-instruction and gdb will report nonsense.
# Re-derive them first:  python -m aacpatch.locate /opt/resolve/bin/resolve
# See docs/tooling.md for which site each address corresponds to.
#
# Run via tools/run-resolve-gdb (Resolve must be gdb's child under ptrace_scope=1).
# ---------------------------------------------------------------------------
# Trace the MKV codec dispatch: does AAC reach 0x5b5e2e5, and with what codec_type
# ([r15+8])?  Compare AC3 (works) vs AAC.  Also break the ctor to confirm.
set pagination off
set confirm off
set breakpoint pending on
set logging file /tmp/gdb-resolve.log
set logging overwrite on
set logging on

# codec dispatch — reads codec_type into eax from [r15+8]
break *0x5b5e2e5
commands
  silent
  printf "\n### dispatch 0x5b5e2e5  codec_type=%d\n", *(int *)($r15 + 8)
  continue
end

# the codec-type compare chain (after setup) — where type 1 should route to ctor
break *0x5b5e33d
commands
  silent
  printf "### cmp-chain 0x5b5e33d  eax=%d\n", (int)$rax
  continue
end

# ctor reached?
break *0x5b56150
commands
  silent
  printf "### CTOR 0x5b56150 reached\n"
  bt 4
  continue
end

run
