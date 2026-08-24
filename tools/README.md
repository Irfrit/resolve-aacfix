# Reverse-engineering and debugging tools

**None of this is needed to install the AAC fix.** That is `./aac-fix install`.

These are the instruments the fix was built with, kept because the work is not
finished: raw `.aac`, `.ts`, `.flv` and `.latm` are still unsupported, and a
future Resolve release may move a patch site.

Full usage, with worked examples from the original investigation, is in
[`../docs/tooling.md`](../docs/tooling.md). Start with
[`../docs/further-work.md`](../docs/further-work.md) for how they fit together.

| | |
|---|---|
| `trace.c` | `LD_PRELOAD` FFmpeg API tracer — the first triage tool. Run via `run-resolve-trace`. |
| `cov.c`, `cov-run`, `cov-diff.py` | differential basic-block coverage: run a working codec and a failing one, diff the reached blocks. This is what cracked Matroska. |
| `mkvprobe.c` | tagged runtime probe, so several instrumented sites can share one build. |
| `run-resolve-gdb`, `gdb/*.gdb` | Resolve under gdb (as gdb's child — `ptrace_scope=1`), logging backtraces while it stays interactive. |
| `scan/` | numpy scanners over the executable segments: xrefs, call sites, immediates, and RTTI→vtable. Fast enough to be interactive on 653 MB. |
| `make-testdata.sh` | generates the codec × container test matrix, including the AC-3/FLAC/MP3 regression controls. |
| `legacy/aacfix.c` | the retired `LD_PRELOAD` `esds→ASC` shim, superseded by an in-binary trampoline. Kept as the readable reference version. |

```sh
make -C tools trace.so         # needs FFmpeg dev headers
scripts/dev-setup.sh e9patch   # then:
make -C tools trampolines      # cov + mkvprobe
```

Two things that apply throughout:

- **Point them at the unpatched binary** (`bin/resolve.aac-orig`). e9patch output
  has an extra LOAD segment, and its trampolines show up in scans.
- **Every address in `gdb/*.gdb` is pinned to Resolve 21.0.3/21.0.4.** Re-derive
  with `python -m aacpatch.locate` before using them on another build; each file
  says so at the top.
