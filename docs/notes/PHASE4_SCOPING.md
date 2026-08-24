# Phase 4 scoping — MKV and raw .aac (ADTS)

## Correction: the "Could not find video stream" errors were NOT Resolve
The only "video stream" string in the binary is `"Failed to add video stream to
SRT sender client"` (streaming/SRT code). There is no import-time "requires video"
rejection. Those errors came from another app (Cinnamon/thumbnailer). So the theory
that audio-only files are rejected for lacking video is **dropped**.

## Architecture finding that makes this cheaper than expected
The `IOAudioFFMPEGCodec` constructor (`0x5b56150`) has **9 callers across several
decoder classes**. gate2 (the fourcc→AVCodecID table) and gate3 (extradata attach)
live *inside* that constructor — so they are **already patched for every class that
routes AAC through it**. Only the per-class *dispatch* (the gate1 equivalent —
"decide to build an FFMPEG codec for this codec-id") is class-specific.

## ★ MKV AAC — SOLVED (via the macOS binary diff)

The Mac build (21.0.3.0007 universal — has an x86_64 slice) has AAC-in-MKV working:
it is the same source WITH the AAC gates intact, so diffing it against Linux gave the
exact removals. Root cause + fix (three additive e9patch trampolines, `--mkv`):

1. **Parser un-drop** (`mkv_undrop` @ 0x5b5d3cb): Linux repointed the `A_AAC` match
   `je` to the skip loop. Redirect to the shared descriptor-setup proceed path with
   codec_type=1 (Mac confirms 1 is AAC's value).
2. **CodecPrivate ASC parse** (`mkv_asc` @ 0x5b5d525) — THE key fix. Mac's AAC handler
   does NOT frame-probe like AC3; it calls an AudioSpecificConfig parser (still present
   in Linux at **0x5b5d9f0**, but its only caller — the handler — was removed) on the
   MKV CodecPrivate ([r12+0x90]) to fill the descriptor, then joins the common "add
   stream" finalize (0x5b5d71e = Mac 0x102796e20). The trampoline: for codec_type==1,
   call 0x5b5d9f0(rsi=&CodecPrivate, r8=&descriptor) and goto the finalize.
3. **Dispatch-redirect** (@ 0x5b5e33d): F's codec dispatch routes types 2/3/7 to the
   IOAudioFFMPEGCodec ctor; add a type-1 (AAC) case -> ctor arm 0x5b5e493. Then
   getCodecInfo -> "aac ", gate2 -> AVCodecID 86018, gate3 -> extradata, and the
   `aacfix.so` shim extracts the ASC.

VERIFIED WORKING: `find_decoder(id=86018 aac) -> aac`, `open2(aac ch=2 sr=48000) -> 0`,
audio plays. (Method that cracked it: differential block-coverage diff AC3-vs-AAC to
localize each divergence, + macOS binary as the answer key for the correct behavior.)

HE-AAC (SBR) follow-up: the ASC parser fills the AAC *base* sample rate (e.g.
22050); SBR output is doubled (44100). `mkv_asc` now doubles descriptor+0x14 when
the CodecPrivate audioObjectType is 5 (SBR) or 29 (PS). Verified `open2 sr=44100`.

Audio-only MKV = pre-existing RESOLVE bug (NOT ours), now precisely scoped:

  * It is limited to the **source/preview viewer**.  The same clip on a TIMELINE
    plays audio fine, on Linux and macOS alike.
  * It reproduces on **stock macOS Resolve**, which ships AAC support and has none
    of our patches -- so it is Blackmagic's bug, upstream of anything we do.
  * It is codec-independent: native FLAC/AC3/MP3 audio-only MKVs are equally
    silent in the preview window.

Earlier note here claimed "audio-only MKV plays no audio" outright; that was based
on a CONFOUNDED test set -- every working MKV was 48kHz *and* had video, every
failing one was 44.1kHz *and* had none, so "44.1kHz is broken" fit the data just as
well.  testdata/av441_aac.mkv (44.1kHz + video -> plays) and testdata/ao48_aac.mkv
(48kHz, no video -> silent in preview) separate the variables: the video-less clip
is the one that misbehaves and the sample rate is irrelevant.  AAC-in-MKV itself is
correct at both rates.  Out of scope: it is not an AAC issue.

REMAINING: the 3 MKV addresses are hardcoded for 21.0.3 (MKV_2103 in additive.py) —
signaturize them like the QuickTime sites for version independence. Also retest the
other MKV variants (he_5p1.mkv HE 5.1, lc_stereo.mkv) and, separately, raw .aac.

## MKV GRIND — progress log (multi-LAYER removal; partially cleared)

AAC was removed from the MKV path at MULTIPLE independent layers. Runtime-probed
each. State as of this session (all findings are 21.0.3 vaddrs):

CLEARED (via 2 trampolines in `aacadd.c` + hardcoded MKV_2103 addrs in additive.py,
run with `--mkv`):
1. **Parser un-drop** — `A_AAC` branch `0x5b5d3b4` did `je 0x5b5d2d0` (skip) on match.
   `mkv_undrop(rdx,&rax,0x5b5d4f0)` sets codec_type=**1** and proceeds. VERIFIED
   (probe tag1=1).
2. **Frame-probe passes** for AAC (probe tag2=1) — not codec-specific enough to block.
3. **Descriptor added** to the track list (probe tag5 fired at `0x5b5d719`
   `call 0x5b5de30`; tag6 loop-continue fired).

KEY MAPPING (why codec_type=1 is right): MKV `getCodecInfo` (`0x5b601f0`) already has
an "aac " case — jump table at `0xa4988f8` maps `[ps+0x30]` (== codec_type): **1→"aac
", 2→".mp3", 3→"ac-3", 7→"flac"**. So codec_type=1 → "aac " → gate2 → 86018. The
packets source ALREADY supports AAC; only the surrounding gates were stripped.

METHOD NOTE (important): the decoder-side region `0x5b5e0e0..0x5b5e2e5` was
mis-modelled. Probes showed `0x5b5e14b` reads a CATEGORY field (always 2 = audio),
not codec_type, and `0x5b5e585` is a HOT per-block loop (fired 233×), not a codec
reject. The map-bypass attempt there was therefore misguided (no effect). AC3 DOES
reach the codec dispatch `0x5b5e2e5` with codec_type=3 (probe tag12=3) and decodes;
AAC (codec_type 1 from the un-drop) does not — but WHERE it diverges on the decoder
side is not reliably mappable by static reading + blind e9patch probes (each cycle
disproved the prior model). The right tool from here is **gdb on the live process**
(as the original report used): breakpoint the codec ctor `0x5b56150` /
`avcodec_find_decoder`, import an AC3 mkv and an AAC mkv, and diff the backtraces to
see exactly where AAC's path forks. That is interactive and needs the user to run
gdb (ptrace_scope=1 → launch Resolve as gdb's child, `-nx`).

DIFFERENTIAL COVERAGE (the right method — finds gates in bulk):
`aacext/cov.c` + `cov-run` + `cov-diff.py`. Instrument all jcc/call in
0x5b00000..0x5d00000, import AC3 (works) and AAC (un-dropped) in separate runs,
diff the block bitmaps. Blocks AC3 hit but AAC didn't = AC3's good path AAC missed;
AAC-only = where AAC forked. This localizes ALL control-flow gates at once.

Results:
- Run 1 (un-drop only): AC3-only=410, **AAC-only=4** — divergence localized to ONE
  function `0x5c6ad30` (container stream-setup). Gate: `0x5c6b7bc cmp eax,1; je` where
  `eax=[rsp+0x4c]` is the stream's support-category: **0 = decodable** (→ `0x5c6b7ca`
  create decoder + add to `[r14+0x1670]`, the array F reads), **1 = skip** (→
  `0x5c6bb20`, no decoder). AC3 audio=0, AAC audio=1.
- `mkv_cat` trampoline (force cat 1->0 at 0x5c6b7b0) advanced AAC ~194 blocks
  (AC3-only 410->216) but AAC still never reaches F/ctor and forked anew into
  container track-management (0x5b274f0, 0x5b5d0d0, ...). Forcing gates cascades —
  AAC gets pushed through paths not meant for it.

THE WALL: the category value `[rsp+0x4c]` is 1 for AAC vs 0 for AC3 with **NO
upstream branch divergence** — it's set by data-dependent logic (a codec->support
lookup), not a branch. Control-flow coverage-diff cannot find where it's set. Root
cause = almost certainly a "supported audio codecs" table/predicate that AAC was
removed from (like the QuickTime table + getCodecInfo, but for stream admission).
Finding it needs DATA-FLOW tracing: a hardware watchpoint on the codec-support value
/ breakpoint the setter, not more coverage diffs. `[rsp+0x4c]` has no plain
`mov [rsp+0x4c],reg` writer — it's filled via a struct copy or a callee, so trace
the writer with a watchpoint. If that one categorization is fixed at root, the whole
downstream chain likely clears at once (all forks stem from "AAC = unsupported").

CONCLUSION: coverage-diff is the correct methodology and works; ~2 gates cleared with
measurable progress; but the admission gate is data-flow, and MKV AAC is a deep chain
rooted in a codec-support predicate. Resumable via data-flow debugging.

GDB FINDINGS (decisive):
- Working AC3 path to the codec ctor: `ctor 0x5b56150` <- `0x5b5e491` (MKV dispatch
  type-3 arm) <- `0x5b269d7` (per-track loop, virtual `call [rax+0x38]` = "F", the
  per-track decoder func at **0x5b5e060**) <- ... . find_decoder(id=86019 ac3). ✓
- F reads a stream-descriptor array at `[this+0x50]` (48-byte entries; field +8 =
  codec_type, +0x10 = codec, +0x14 = rate) and dispatches on codec_type at
  `0x5b5e2e5`. For AC3: descriptor {+8=3, +0x10=32, +0x14=48000} -> dispatch=3 -> ctor.
- **AAC: F is never called for its audio stream** (descriptor bp `0x5b5e0b5` and
  dispatch `0x5b5e2e5` never fire for AAC). So AAC's stream is absent from F's
  `[this+0x50]` array / track count — filtered UPSTREAM of F, even though the parser
  un-drop accepted A_AAC and added a descriptor (tag5). i.e. there is at least one
  more removal layer between the parser accept and the F stream-array population.
- Corollary: my earlier probe `tag10=2` was a corrupted-build artifact (the
  map-bypass at 0x5b5e2df hit a hot loop) — disregard it. The dispatch-redirect
  (type1->ctor) is correct-in-principle but unreachable because F isn't called.

STATUS: STOPPED. MKV AAC was excised at MULTIPLE deep, independent layers (parser +
stream-array population + the F dispatch that also lacks a type-1 arm + CodecPrivate
extradata). Cleared the parser; but AAC's stream is dropped again before F. Each
layer needs a separate live gdb/test cycle (~12 done). Not converging — a genuine
rabbit hole. Recommend: ship the working QuickTime path; treat MKV as a large,
resumable-from-here research task (next step: find where [this+0x50] is populated
and why AAC's stream is excluded — breakpoint F entry 0x5b5e060 to confirm it's the
track-count vs an early F bail).

CURRENT BLOCKER (superseded by GDB findings above):
4. **Decoder-construction rejects codec_type 1.** In the decoder ctor, `[r15+r13+8]`
   (codec_type) drives a jump table `0x5b5e162` (base `0xa498892`, cases 0..6) that
   builds a CodecID string per type, then a map lookup (`0x5b5e2c2`); mismatch →
   `jbe 0x5b5e585`. Probe tag3 (at the post-lookup dispatch `0x5b5e2e9`) never fired
   for AAC → type 1 is filtered here. (`0x5b5e585` looks like a PCM/default fallback,
   not a hard reject — needs confirming.) Also note the dispatch routing trampoline I
   added at `0x5b5e33d` (route type1→ctor arm `0x5b5e493`) is downstream of this and
   hasn't been exercised yet.
5. Likely FURTHER layers after that (dispatch arm setup, CodecPrivate→extradata).

ASSESSMENT: genuinely a multi-layer excision — Blackmagic removed AAC from every
codec-type-keyed structure in the MKV path, not just one. Each layer = find + patch +
one live test cycle (can't run Resolve headless). 4 cycles in, 3 layers cleared, ≥1
more confirmed + probably more. Converging but slow/expensive. Resumable from here
via the probe harness (`additive.py --mkv --probe-mkv`, tagged `mkvprobe.c`).

### (superseded) earlier scoping
## MKV DEEP-DIVE RESULT (after runtime probing): multi-point build-out required.

Traced the full MKV audio path. AAC is removed at **multiple** points, mirroring
the QuickTime removal but spread across the pipeline:

1. **Parser deliberately drops A_AAC.** The MKV CodecID parser (jump-table on
   CodecID length, `0x5b5d312`) has a length-5 handler with an **inverted** A_AAC
   branch at `0x5b5d3b4`: it matches `"A_AAC"` and does `je 0x5b5d2d0` — jumping to
   the *skip-track* loop. AC3/FLAC do the opposite (`jne skip`, then proceed and set
   a `codec_type`). This is the smoking gun — AAC is recognized and intentionally
   discarded. (Runtime probe at the codec dispatch `0x5b5e2e5` fired for
   mp3/ac3/flac = codec_type 2/3/7, and **never** for AAC.)

2. **Un-dropping alone is insufficient (tested).** Retargeting the A_AAC `je` to the
   AC3 proceed path (`0x5b5d4eb`, codec_type=3) still produced no decode — the
   proceed path frame-probes (`0x5b5dd5c0`) in a codec-specific way that rejects AAC
   before it reaches the codec dispatch.

3. **fourcc comes from a virtual, keyed on codec_type.** The shared FFMPEG ctor gets
   its gate2 fourcc key from `call [rcx+0x48]` (packets-source `getCodecInfo`) at
   `0x5b5624c`. codec_type 3 → "ac-3"; there is no codec_type that yields "aac ".

Full fix therefore needs, together: (a) un-drop A_AAC at the parser AND give it a new
codec_type; (b) route that codec_type through the dispatch to the FFMPEG ctor;
(c) make the packets-source `getCodecInfo` return "aac " for it; (d) handle AAC
frame-probing; (e) CodecPrivate (raw ASC) as extradata. gate2/gate3 then finish it.
Each step needs a live Resolve test cycle (can't run it headless here).

Estimate: several more focused sessions. This is real RE, above the "if it's easy"
bar. The QuickTime path (shipped, working) remains the recommended scope.

### (earlier, now-refined) verdict
## VERDICT (after tracing): MKV = MEDIUM effort, NOT a quick win. .aac = deferred.

Empirical (clean 48 kHz A/B test): `av_2s.mp4` audio decodes (2× `open2 aac -> 0`);
`av_2s.mkv` produces **zero** decode calls. So MKV audio never reaches FFmpeg.

Root cause found: `IOMKVAudioDecoder` (`0x5b5d8xx`–`0x5b5exx`) dispatches on an
**integer codec-type index** at `[r15+8]`:
- `cmp eax,7; ja reject` (>7 rejected outright), then a sub-switch:
  `==2 → FFMPEG ctor`, `==3 → FFMPEG ctor`, `==7 → FFMPEG ctor`, `==6 → cond`,
  everything else → `0x5b5e585` (reject).
- Types 2/3/7 already route to the shared `IOAudioFFMPEGCodec` ctor (`0x5b56150`,
  where gate2/gate3 already live). **AAC's type index is not among them → rejected.**

Why it's not the same one-liner as QuickTime:
1. Need to find AAC's codec-type index (more RE of the MKV track parser). If it's
   >7 it's killed at the *first* `cmp eax,7` — would need trampolines at two sites.
2. The FFMPEG-ctor arms depend on a setup object built by `0x5b5ee70` on the
   `eax<7` path; AAC must go through that setup, not just jump to the ctor.
3. **Extradata differs:** MKV carries AAC config in `CodecPrivate` as a **raw
   AudioSpecificConfig**, not an `esds`. So gate3 (esds extraction) and the
   `aacfix.so` shim (esds→ASC) are QuickTime-specific and don't apply — the MKV
   path needs its own extradata handoff.

Estimate: ~1–2 focused RE sessions + likely an MKV-specific extradata tweak. Doable
as additive trampolines (same `redirect` pattern), but above the "if it's easy" bar.
Recommendation: ship MP4/MOV/M4A now; do MKV as a scoped follow-up if wanted.

### (superseded) earlier optimistic read
## MKV — LOW difficulty, likely already close to working
- `IOMKVAudioDecoder` has a CodecID string table that **includes `A_AAC`** (with
  A_MPEG/L3, A_AC3, A_PCM…, A_FLAC). Unlike IOQuickTime (where the AAC *row was
  deleted*), the MKV path already knows AAC exists.
- If the MKV dispatch maps `A_AAC` → a codec that flows into `IOAudioFFMPEGCodec`,
  then the already-installed gate2/gate3 handle it and MKV-AAC may **just work**.
- **Decisive test:** `testdata/av_1s.mkv` (h264 + LC-AAC). If it decodes, MKV-with-
  video is done. If audio-only MKV still fails while av_1s.mkv works, the gap is in
  MKV import/demux, not codec — investigate separately.
- If a dispatch gap exists, it's the same shape as gate1 (a codec-id compare that
  omits AAC) → one more additive `redirect(...)` trampoline. Est: small.

## raw .aac / ADTS — UNKNOWN until traced, likely LOW value
- `IOADTSAudioDecoder` exists. ADTS is self-describing (in-band config, no esds), so
  gate3/the esds shim are irrelevant here; only a dispatch gate would matter.
- Least-common format (user has never used it). Defer unless the trace shows it's a
  trivial dispatch add.

## Next data needed (one launch, trace shim already wired)
Re-run `./resolve-aac` and drag in: `av_1s.mkv`, an audio-only `.mkv`
(`he_5p1.mkv`/`lc_stereo.mkv`), and `he_5p1.aac`. Then read `/tmp/avtrace.log`:
- `find_decoder(id=… aac) -> aac` appearing for a file ⇒ it reached the shared codec
  path (dispatch OK; any failure is downstream/extradata).
- No `find_decoder` for a file ⇒ dispatch gap in that class (patchable like gate1).
This distinguishes, per format, "dispatch gap (patchable)" vs "demux issue (harder)".
