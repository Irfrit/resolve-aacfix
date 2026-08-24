# The tools

Everything in `tools/` is for reverse engineering and debugging. **None of it is
needed to install the AAC fix** — that is `./aac-fix install` and nothing else.

Build what you need:

```sh
make -C tools trace.so          # LD_PRELOAD FFmpeg tracer (needs FFmpeg dev headers)
scripts/dev-setup.sh e9patch    # needed before building the trampolines below
make -C tools trampolines       # cov + mkvprobe
```

A note that applies to all of it: **analyse the unpatched binary.** Keep
`bin/resolve.aac-orig` (which `aac-fix install` leaves behind) and point tools at
it. e9patch output has an extra LOAD segment and its trampolines will appear in
scans and confuse address arithmetic.

---

## `trace.c` → `trace.so` — FFmpeg API tracer

The first thing to reach for. An `LD_PRELOAD` shim that interposes six FFmpeg
entry points and logs every call:

`avcodec_find_decoder`, `avcodec_find_decoder_by_name`, `avcodec_open2` (with a
hexdump of the first 300 bytes of extradata), `avcodec_send_packet` and
`avcodec_receive_frame` (errors only, so it is quiet when things work),
`av_find_best_stream`, and `avformat_find_stream_info` (dumping every stream's
type, codec id, tag, profile, channels and sample rate).

```sh
make -C tools trace.so
tools/run-resolve-trace                    # import clips, quit Resolve
cat /tmp/avtrace.log
```

What to look for is covered in
[`further-work.md`](further-work.md#the-triage-question-and-the-one-command-that-answers-it):
a `find_decoder` line for your file means it reached FFmpeg; no line at all means
Resolve dropped it earlier. The extradata hexdump in `open2` is also how the
`esds`-versus-`AudioSpecificConfig` problem was identified — you can see the
41-byte descriptor going in and the 4-byte config that should have.

`AVTRACE_LOG` overrides the log path.

## `cov.c` + `cov-run` + `cov-diff.py` — differential block coverage

The heavy artillery, and the technique that cracked Matroska.

`cov.c` is an e9patch trampoline exporting `mark(addr)`. It sets one bit per
byte-address in a file-backed `mmap` of `/tmp/cov.bin`, so coverage survives even
if Resolve is force-quit. It covers `[BASE, BASE+SPAN)` — `0x5b00000` for 2 MB by
default, which is the audio/container region. **Retarget `BASE`/`SPAN` in
`tools/cov.c` for a different region** and rebuild.

```sh
vendor/e9patch/e9tool -M 'jmp or call' -P 'before mark(addr)@tools/cov' \
    resolve.aac-orig -o resolve.cov
# install resolve.cov as bin/resolve, then:
tools/cov-run ac3        # import ONE clip that works, quit
tools/cov-run aac        # import ONE clip that fails, quit
tools/cov-diff.py -b resolve.aac-orig /tmp/cov_ac3.bin /tmp/cov_aac.bin
```

`cov-diff.py` prints both directions, disassembled: blocks the working run
reached that the failing one missed (read from the top — the first is the gate),
and blocks the failing run reached instead.

Two rules. **One clip per run** — extra interactions add blocks to both bitmaps
and dilute the diff. And **`--base` must match `cov.c`'s `BASE`**, or every
address is reported off by a constant.

Its blind spot is documented in
[`further-work.md`](further-work.md#differential-coverage-the-heavy-artillery):
control flow only. A gate implemented as data flow is invisible to it.

## `mkvprobe.c` — tagged runtime probe

Twenty lines: `probe(tag, val)` appends `probe tag=N val=V (0xV)` to
`/tmp/mkvprobe.log`. The tag lets many instrumented sites share one trampoline
build, which matters because each rebuild-and-test cycle needs a live Resolve
launch.

```sh
python -m aacpatch.additive resolve.aac-orig -o resolve.probe \
    --mkv --probe-mkv 0x5b5e2e5 --probe-tag 3 --probe-reg r15
```

Read the probe results sceptically. More than one wrong model in this project's
history came from a probe firing in a hot loop, or from a trampoline that had
subtly corrupted the binary it was measuring.

## `run-resolve-gdb` and `gdb/*.gdb`

Launches Resolve as gdb's **child** — with the usual `ptrace_scope=1` you cannot
attach to a running Resolve, and it re-execs itself in ways that make attaching
unreliable anyway. Breakpoints log to `/tmp/gdb-resolve.log` while Resolve stays
interactive, so you can import several clips in one session.

```sh
tools/run-resolve-gdb                        # default: gdb/ctor-bt.gdb
tools/run-resolve-gdb tools/gdb/findF.gdb
```

| script | what it does |
|---|---|
| `ctor-bt.gdb` | backtrace on every `IOAudioFFMPEGCodec` constructor call, and on every `avcodec_find_decoder` with `$rdi >= 0x15000` (the filter keeps h264 noise out). Import a working clip then a failing one and diff the backtraces. |
| `dispatch-bt.gdb` | breaks the MKV codec dispatch, printing `codec_type` from `[r15+8]`, plus the cmp chain and the constructor. |
| `findF.gdb` | at the per-track virtual `call [rax+0x38]`, resolves and prints the callee out of the vtable. This is how `F`, the per-track decoder function, was found. |
| `descr.gdb` | dumps the stream-descriptor fields per track (`+8` type, `+0x10` codec, `+0x14` rate). |

**Every address in these is pinned to Resolve Studio 21.0.3/21.0.4** and each
file says so at the top. On another build they will land mid-instruction and gdb
will report nonsense. Re-derive first with `python -m aacpatch.locate`; the site
table in [`reverse-engineering.md`](reverse-engineering.md#6-site-table) maps
names to addresses.

The backtrace-diff is the technique to remember. It is what proved that for AAC
in MKV, `F` was **never called at all** — which meant the stream was being
dropped upstream of the dispatch that was being patched, and redirected the whole
investigation.

## `scan/` — direct `.text` scanners

Numpy over the executable segments. Fast enough to be interactive on 653 MB
(seconds), and the reason a decompiler was never needed. All take `-b/--binary`
(or `$RESOLVE_BIN`) and derive their geometry from the ELF program headers via
`aacpatch.elfmap`, so nothing is pinned to one build.

Requires `numpy` (`cls.py` does not).

### `xref.py` — who references this address?

```sh
python tools/scan/xref.py -b resolve.aac-orig 0xa497f0c
```

Finds any 4-byte displacement that, taken as RIP-relative, points at the target —
so it catches `lea`, `mov`, `call [rip+…]` alike, because it matches on the
arithmetic rather than an opcode. Pointing it at the gate-2 table finds the two
`movups` that load it. The reported instruction address is approximate;
disassemble around it.

### `callxref.py` — who calls this function?

```sh
python tools/scan/callxref.py -b resolve.aac-orig ctor=0x5b56150
```

Only matches `E8 rel32`, so every hit is a real call. This is what established
that `IOAudioFFMPEGCodec`'s constructor has 9 callers across several decoder
classes — the fact that makes gate 2 and gate 3 shared across containers and
makes every remaining format cheaper than the first.

### `imm.py` — where does this constant appear?

```sh
python tools/scan/imm.py -b resolve.aac-orig                  # the audio codec IDs
python tools/scan/imm.py -b resolve.aac-orig 0x61616320       # the 'aac ' fourcc
```

Blunt and effective. Counting `0x15002` sites in the Linux and macOS builds is
what showed macOS has a dedicated AAC extradata path where Linux's shared
constructor has none.

### `cls.py` — RTTI to vtable

```sh
python tools/scan/cls.py -b resolve.aac-orig 18IOAudioFFMPEGCodec 20IOQuickTimeAudioDecoder
```

Takes the **mangled** type name (length prefix plus identifier, i.e. what
`typeid(T).name()` returns) and follows the Itanium ABI chain: typeinfo-name
string → pointer to it (`_ZTI`, name at +8) → pointer to that (`_ZTV`, virtuals
at +16). In a stripped binary this is the only structural handle on the class
hierarchy, and it is how every `IO*AudioDecoder` class was located.

It reports false positives — any 8-byte value that happens to equal an address.
Confirm a candidate vtable by disassembling the function at +16 and checking it
looks like a destructor.

## `make-testdata.sh` — the test matrix

```sh
tools/make-testdata.sh -o testdata
tools/make-testdata.sh -o testdata --he-source ChID-BLITS-EBU-Narration.mp4
```

Generates AAC clips across containers, plus AC-3 / FLAC / MP3 / PCM regression
controls, in both audio-only (`ao_`) and with-video (`av_`) variants. The
controls are not optional decoration: the whole claim of this project is that the
patch is additive, and they are what tests it. The audio-only MKVs are also the
standing evidence that audio-only MKV silence is Resolve's own bug.

HE-AAC/SBR cannot be synthesised — FFmpeg's native encoder is LC only — so pass
`--he-source` pointing at a file that already has it. Without that, the explicit-SBR
sample-rate path in the `mkv_asc` trampoline goes untested.

## `legacy/aacfix.c` — the retired `LD_PRELOAD` shim

Kept for reference, not built by default. It interposed `avcodec_open2` and
replaced an `esds` extradata blob with the `AudioSpecificConfig` inside it. Its
`find_asc()` descriptor walk is the readable version of what now runs in-binary as
`aac_esds_fix` in `src/trampoline/aacadd.c`.

It was worth keeping because it is the cleanest illustration of the gate-4 problem
and, more generally, of a good way to *prototype* a binary patch: implement the fix
in C against the public API first, confirm it works, then move it into the binary.
