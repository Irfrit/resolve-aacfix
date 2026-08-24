# ---------------------------------------------------------------------------
# WARNING: every address below is pinned to DaVinci Resolve Studio 21.0.3/21.0.4.
# On any other build they will land mid-instruction and gdb will report nonsense.
# Re-derive them first:  python -m aacpatch.locate /opt/resolve/bin/resolve
# See docs/tooling.md for which site each address corresponds to.
#
# Run via tools/run-resolve-gdb (Resolve must be gdb's child under ptrace_scope=1).
# ---------------------------------------------------------------------------
# Log a backtrace every time the IOAudioFFMPEGCodec constructor (0x5b56150) is
# reached, and every avcodec_find_decoder call, then continue so Resolve keeps
# running.  Import an AC3 mkv, then an AAC mkv, and diff the backtraces.
set pagination off
set confirm off
set breakpoint pending on

# log to file (and stdout)
set logging file /tmp/gdb-resolve.log
set logging overwrite on
set logging on

break *0x5b56150
commands
  silent
  printf "\n===== IOAudioFFMPEGCodec ctor (0x5b56150) =====\n"
  bt 25
  continue
end

# audio decoder lookup in system libavcodec — audio codec ids only (>=0x15000),
# so h264 video lookups don't flood the log
break avcodec_find_decoder if (int)$rdi >= 0x15000
commands
  silent
  printf "\n----- avcodec_find_decoder(id=%d) -----\n", (int)$rdi
  bt 8
  continue
end

run

