# Plan: shippable, multi-version AAC support for DaVinci Resolve on Linux

## Goal

A single installer script that, run against a Resolve installation, adds AAC decode
support by:

1. Applying signature-located binary patches to `/opt/resolve/bin/resolve`.
2. Installing replacement FFmpeg `.so` files that have AAC compiled in (and stay a
   true drop-in for the bundled build).
3. Installing the `esds -> ASC` `LD_PRELOAD` shim + launcher.
4. Supporting several Resolve versions where possible, and offering clean rollback.

Non-destructive is the target: the finished tool should **add** AAC without
sacrificing AC-3, FLAC, the `NONE` fourcc slot, or the bundled AV3A demuxer.

---

## Current state (done — one build only)

Fully reverse-engineered and verified against a single build:
`/opt/resolve/bin/resolve`, 653,047,344 bytes, ELF64 EXEC non-PIE stripped,
Linux Mint 22.3, system FFmpeg 6.1.1.

Four gates, all cleared:

| Gate | What | Mechanism today | File offset | Cost |
|---|---|---|---|---|
| Lib | Bundled libavcodec has AAC stripped | Move bundled libs aside; loader finds system FFmpeg via `DT_RPATH` | — | loses bundled AV3A demux |
| 1 · dispatch | `IOQuickTimeAudioDecoder` fourcc switch has no `"aac "` case | `patch.py` — repoint `NONE` branch to `"aac "` → `IOAudioFFMPEGCodec` | `0x576167d` (13 B) | `NONE` fourcc |
| 2 · codec id | fourcc→AVCodecID table missing AAC row | `patch3.py` — repoint table entry 3 (`ac-3`/86019 → `aac `/86018) | `0xa097f24` (8 B) | AC-3 decode |
| 3 · extradata | Config attached only for ALAC/FLAC | `patch4.py` — 3 edits: fetch (A `0x57567a9`), attach (B `0x5756803`), drop size gate (C `0x5756814`) | 14 B total | FLAC decode |
| 4 · esds | Resolve passes whole 41-byte `esds`, FFmpeg wants 4-byte ASC | `aacfix.so` `LD_PRELOAD` hook on `avcodec_open2` | — | nothing |

Verified result: HE-AAC 5.1 @ 44.1 kHz decodes, `open2 -> 0`, zero packet errors
(`aacfix.log`). Prior to patches: 2,400+ decode failures per session.

Discarded dead end: `patch2.py` / `unpatch2.py` — the "allowlist bypass" theory was
wrong (skipping the red-black tree lookup left `r13` stale → `find_decoder(0)`).
Reverted. **Do not ship.** Note the report's warning: that revert restored the
leftover code to live use, so it is **not** a safe code cave.

Patch-script discipline already in place and worth preserving everywhere:
verify-before-write, idempotent, refuse on mismatch, patch-before-launch
(writing a running binary fails `ETXTBSY`).

Key address-translation fact every offset depends on: four `LOAD` segments, the
**last one uses delta `0x401000`** not `0x400000` — mishandling it silently
corrupts every vtable/typeinfo read.

---

## Tool decision (grounded in `evaluation_of_rewriters.txt`)

Target profile is the eval's worst case: 653 MB, stripped, **non-PIE**, no
relocation info. Only category-4 tools (ddisasm, e9patch, zipr) handle that class
at all.

- **Phase 1 (locate) + Phase 2 (equal-length in-place edits): custom, no rewriter.**
  Locating a *semantic* site across versions and swapping equal-length bytes is not
  rewriting — no tool in the eval does it. Running ddisasm/zipr on a 653 MB binary is
  impractical (super-linear memory; already 0.5–1 GB and 70–230 s on *small*
  benchmarks) and both *modify existing code*, the eval's flagged reliability-killer.
- **Phase 3 (additive code): adopt e9patch.** The eval singles out trampoline
  rewriters as *"very reliable … if only additive instrumentation and no modification
  of existing code is required"* — 100% EXE, fastest (2.7 s), lowest memory (105 MB),
  works on stripped non-PIE. Replaces the fragile hand-rolled code-cave hunt.
  **Status: e9patch source requested from user.**

## Phase 1 — Version-independent patch location (load-bearing) — STARTED

The single biggest gap. Every offset is hardcoded to one build; the report confirms
offsets differ per version. Replace fixed offsets with runtime discovery.

**Built and validated** (`aacpatch/`, run via `.venv/bin/python -m aacpatch.locate
<binary>`):

- `elfmap.py` — derives file-offset ↔ vaddr from PT_LOAD headers at runtime;
  confirmed it reproduces the 4-segment layout incl. the `0x401000` delta on the
  last (data/vtable) segment with no hardcoding.
- `signature.py` — masked byte-pattern engine; anchors on the longest fixed run,
  enforces **exactly one hit** (refuses on 0 or >1), optional capstone instruction
  verifier per site.
- `sites.py` — signatures for all six sites (gate1 dispatch, gate2 table, gate2
  count, gate3 A/B/C), keyed only on version-invariant bytes (fourcc constants,
  `0x1500x` codec IDs, table count); volatile disp32/rel32/imm32 pointers wildcarded.
- `locate.py` — reports found / one-hit / verified / ORIGINAL-vs-PATCHED per site.

Against the current build all six resolve by signature and reconcile **exactly** with
the report's hand-found offsets. Remaining Phase 1 work:

**1.1 ELF-driven address translation.** ✅ done (`elfmap.py`).

**1.2 Signature-based scanning.** ✅ done for all six sites (`signature.py`,
`sites.py`). Optional extra cross-check anchor still available if a future version
proves ambiguous: the `"mp4a" -> "aac "` rename site
(`mov dword [rbx+0x218], 0x61616320`).

**1.3 Computed-patch layer + applier.** ✅ done (`patch.py`, `apply.py`). Two sites
are not static swaps; their replacement is computed from the decoded instruction:

- **gate1 dispatch** — located via the stable `.mp3` branch anchor (which jumps to
  the same FFmpeg handler); the `je` rel32 is recomputed to that target and the imm
  set to `'aac '`. Reproduces the report's exact bytes on 21.0.3
  (`…02 01 00 00`) and computes its own correct target on 21.0.0 (`je 0x5b60e1c`).
- **gate3c size gate** — `je rel32` (6 B) → `jmp rel32+1; nop`, same absolute target.

Signatures were reworked to anchor **only on bytes the patch never touches** and
wildcard exactly what each edit mutates, so location is **patch-state-independent**.
Verified end-to-end on both builds: dry-run patches a temp copy and re-disassembles
the computed sites; **idempotent** (second apply = 0 written / 5 already,
byte-identical); `locate` reports all five `PATCHED` on a patched copy; a corrupted
site is **refused** with `verify-before-write`. In-place mode writes a `.orig`
backup.

**1.4 Multi-version validation.** ✅ validated across two real builds:
- **21.0.3** (installed, `/opt/resolve/bin/resolve`, 653,047,344 B)
- **21.0.0.0048 Studio** (extracted from the `.run`, 652,750,088 B)

All six signatures resolve to exactly one hit on *both*, at completely different
offsets (e.g. gate1 `0x576167d` vs `0x5760d0d`), and every located site disassembles
to the expected instruction on 21.0.0 (`cmp r15d,"NONE";je`, `cmp rdx,0x22;je`, the
alac/flac/.mp3/ac-3 table, both FLAC `cmp` gates). No per-version overrides needed so
far. Still worth widening to a 20.x / 18.x build to find where signatures break.

Extraction method (no need to run the untrusted 10.9 GB AppImage): the `.run` is an
AppImage — ELF runtime + appended squashfs. Superblock sits at the ELF image end
(`e_shoff + e_shnum*e_shentsize`); `unsquashfs -o <that offset> -d <dest> <run>
bin/resolve` pulls out just the binary. (`~/Downloads/makeresolvedeb_1.10.0_multi.sh`
instead uses `--appimage-extract`/`xorriso` to unpack the whole tree.)

Maintain a small per-version manifest (version string, build hash, any overrides) as
more builds are added.

**Deliverable:** ✅ **Phase 1 complete.** `aacpatch/` locates all sites by signature,
computes the two non-static edits, and applies the destructive AAC patch set with
verify-before-write, idempotency, and rollback — validated on **21.0.0** and
**21.0.3** Studio. 21.0.3 output is byte-identical to the report's proven-working
patches. Remaining before it's a shippable installer: FFmpeg `.so` handling
(Phase 2), the shim + launcher (Phase 5), packaging (Phase 6). Optional hardening:
run against a more distant build (20.x/18.x) if we ever widen past 21.x.

---

## Phase 2 — Drop-in replacement `.so` files — ✅ DONE (built, not yet installed)

**Blackmagic's build recipe, recovered verbatim.** Every bundled lib embeds
FFmpeg's `--prefix` configuration string; all four agree:

```
--prefix=/media/datastore1/build/bmd-eng-ub_FTKZ/tmp/install/linux/
--enable-runtime-cpudetect --disable-lzma --disable-xlib --enable-shared
--disable-programs --disable-doc --disable-avdevice --disable-postproc
--disable-avfilter --disable-pixelutils --disable-static --disable-swresample
--disable-iconv
--disable-decoder='aac*' --disable-encoder='aac*,ac3*' --disable-parser='aac*'
--disable-muxer='aac*,ac3*' --disable-demuxer='aac*,ac3'
--extra-ldflags=-L<their zlib-1.2.13> --extra-cflags=-I<their zlib-1.2.13>
```

Note what this proves: the strip is purely a configure-time exclusion of `aac*`
(plus the AC-3 *encoder*/muxer/demuxer — the AC-3 **decoder** was left in). No
source patching, no external codec libs, no `--enable-gpl`.

**Version.** Bundled sonames are `libavcodec.so.60.3.100`, `libavformat.so.60.3.100`,
`libavutil.so.58.2.100`, `libswscale.so.7.1.100` → FFmpeg **6.0**. `n6.0.1` is the
last 6.0.x patch and carries byte-identical lib version numbers, so we build
**n6.0.1** (`ffmpeg-src/` is a git worktree of `~/3rdparty/FFmpeg` at that tag).

**Our configure line** is BMD's with the five stripping flags reduced to their AC-3
halves — AAC decode/encode/parse/mux/demux comes back, AC-3 stays exactly as
Blackmagic had it. Build script: `build_ffmpeg.sh`.

**2.1 Build FFmpeg 6.0 matching the bundled soname/version.** ✅ Sonames, filenames,
`LIBAVCODEC_60`-style version nodes and `RUNPATH=$ORIGIN` all match. (The `$ORIGIN`
needs `'$$$$ORIGIN'` injected into `ffbuild/config.mak` — `library.mak` expands its
link recipes twice, so nothing survives `--extra-ldflags`.)

**2.2 Reproduce Blackmagic's AVS3 / "Audio Vivid" backport.** ✅ Not re-implemented —
*superseded*. Since all four libs are replaced, BMD's `libavformat` (the only
consumer of `read_av3a_frame_header` / `avs3_samplingrate_table` /
`codecBitrateConfigTable`) is gone, so those three exports are not needed. The
*feature* is preserved instead by porting the upstream AV3A demuxer back to the 6.0
API (source: OpenHarmony's FFmpeg fork, © 2024 Shuai Liu, LGPL):

- `libavcodec/av3a.h` (new, header-only tables)
- `libavformat/av3adec.c` (new; `FFInputFormat` → 6.0 `AVInputFormat`, plus a
  short-buffer guard in `av3a_probe`)
- `AV_CODEC_ID_AVS3DA` appended after `AV_CODEC_ID_RKA` → **86119**, which is
  exactly the id BMD used (read out of the bundled `AVCodecDescriptor`), with the
  same `name`/`long_name`/`props`; demuxer `name`/`long_name`/`flags`/`extensions`/
  `mime_type`/`raw_codec_id` all read out of BMD's `ff_av3a_demuxer` and matched.

**2.3 Decide libavutil/libswscale.** ✅ Rebuild all four. Mixing is what created the
AVS3 link dependency in the first place; a consistent set removes it.

**2.4 Installer library handling.** ✅ `install_libs.sh` (+ `--uninstall`), backs up
to `/opt/resolve/libs/_bmd_orig/`, installs the four `.so`s and eight symlinks, then
`ldd`-checks. Needs root; **not yet run**.

**Verification.** Exported-symbol diff against the bundled libs:

| lib | missing vs BMD | extra |
|---|---|---|
| libavcodec | the 3 AVS3 helpers (by design, see 2.2) | none |
| libavformat / libavutil / libswscale | none | none |

Every FFmpeg symbol imported by *any* ELF under `/opt/resolve` (61 in total) is
exported by our build. Smoke test: `aac`/`aac_fixed`/`aac_latm` decoders + native
`aac` encoder present, AC-3 decoder present, AC-3 encoder/demuxer absent (matching
BMD), `av3a` demuxer present, `AV_CODEC_ID_AVS3DA == 86119`.

**Caveat:** because our `libavcodec` does not export the three AVS3 helpers, it is
*not* a drop-in for BMD's `libavcodec` alone — the four libs must be installed as a
set. Note also that with AAC now resolvable, Phase 1's Gate-1/2/3 binary patches are
still required; the libs only clear the "Lib" gate.

## Phase 3 — Additive (non-destructive) binary patches

Currently every patch trades a codec away. Report sketches the mechanical fix; not
yet built. All three need a **genuine code cave from real alignment padding** — the
`patch2` leftover is explicitly unsafe.

**3.0 e9patch adopted — code caves no longer needed.** ✅ Built and de-risked:
rewrites the 653 MB binary in 3.3 s / ~10 MB RAM, emits valid ELF, preserves the
original 4 LOAD segments, appends trampolines as a new segment. The "find a safe
code cave" problem is moot — e9patch supplies loadable code+data. See
`PHASE3_RE.md`. Packaging decision recorded there too (strip bundled FFmpeg from the
`.deb`, `Depends:` distro ffmpeg).

**3.1 gate2 / AC-3 (highest uncertainty) — RE + PoC DONE.** Reversed the codec-map
static init and `insert_range` (a clean, reusable `(map, table, count)` builder that
re-inits the map on entry). Additive fix: an e9patch trampoline at the after-call
site calls `insert_range(map, TABLE5, 5)` (`aacext/gate2_table.c`), fully replacing
the 4-entry build with 5 — AC-3 kept, AAC added. PoC compiles, emits a valid patched
binary, and the Phase-1 locator still resolves all sites on it. **Remaining: run
Resolve to confirm AC-3+AAC both decode** (needs a live install).

**3.2 Grow the codec table 4 → 5.**
Change `mov edx,0x4` → `0x5` at the table-load site; point `rsi` at a 40-byte table
in the cave holding the original four rows plus `{"aac ", 86018}`. (The inline
`movups` pair only copies 32 bytes, so the table must live at a loadable address.)
**Restores AC-3.**

**3.3 Two-way extradata gates.**
Rewrite each Gate-3 check as `cmp …,0x1500c` **and** `cmp …,0x15002` (jump-out to
cave) instead of overwriting the FLAC compare. **Restores FLAC.**

**3.4 Dedicated `"aac "` dispatch branch.**
Give `"aac "` its own branch in the cave rather than consuming the `NONE` slot.
**Restores `NONE`.**

If a build lacks a usable cave, fall back to the current in-place destructive patches
for that version and record the trade-off in its manifest.

---

## Phase 4 — Non-QuickTime decode paths

Current dispatch patch only touches `IOQuickTimeAudioDecoder` (MP4/MOV). Resolve also
has `IOADTSAudioDecoder`, `IOMKVAudioDecoder`, `IOMXFAudioDecoder`. AAC in `.mkv`,
`.mxf`, or raw `.aac` (ADTS) likely routes through those classes, which may have
their own missing wiring. Untested.

- Audit each class (RTTI names already recovered) for an AAC gap.
- For each, determine whether Gate-1/2/3 equivalents exist and patch analogously.
- Raw ADTS carries config in-band, so Gate 4 (the shim) may be unnecessary there —
  confirm.

Treat as a coverage phase after the MP4 path is solid across versions.

---

## Phase 5 — The `esds -> ASC` shim

Recommendation: **keep `LD_PRELOAD`**, do not fold into the binary. It hooks the
versioned symbol import (`avcodec_open2@LIBAVCODEC_60`), so it is already
version-independent and works; an in-binary MPEG-4 descriptor parser would need a
cave + hand-written parser per version for no gain.

- Installer builds `aacfix.so` (`aacfix.c` is final) and installs it plus a launcher
  wrapper (`~/.local/bin/resolve-aac`) or a patched `.desktop` entry.
- Strip the debug-logging path or gate it behind `AACFIX_LOG` (already env-gated).

---

## Phase 6 — Installer + rollback

Consolidate everything into one idempotent script.

- Detect Resolve version; select matching signature set / manifest.
- Verify and store a byte-identical backup of `resolve` before any write.
- Apply binary patches via Phase 1 locator, keeping verify-before-write + refuse
  on mismatch; guard `ETXTBSY` (patch before launch, refuse if running).
- Install replacement `.so`s (Phase 2), with load smoke-test.
- Build + install shim and launcher (Phase 5).
- `--uninstall`: restore binary from backup, restore bundled libs from `_bmd_orig/`,
  remove shim + launcher.
- Refuse cleanly on unknown version rather than writing blind.

---

## Phase 7 — Testing

Only one asset / one version verified so far
(`ChID-BLITS-EBU-Narration.mp4`, HE-AAC 5.1 44.1 kHz).

- **Codec matrix:** LC-AAC stereo, HE-AAC (SBR), HE-AACv2 (PS), various sample
  rates / channel configs.
- **Container matrix:** `.mp4`, `.mov`, `.m4a`, `.mkv`, `.mxf`, raw `.aac` (ADTS) —
  exercises the Phase 4 paths.
- **Version matrix:** every Resolve version claimed as supported.
- **Regression:** confirm AC-3 and FLAC still decode after Phase 3 (additive),
  and that a `NONE`-fourcc / AVS3 asset still behaves.
- Automate with the `LD_PRELOAD` trace shim (`trace.c`/`trace.so`) counting
  `send_packet`/`receive_frame` errors per clip.

---

## Suggested order

1. **Phase 1** (signature patcher) and **Phase 2** (drop-in `.so`) — load-bearing for
   "runs on a real install across versions."
2. **Phase 6** packaging — straightforward once Phase 1 exists.
3. **Phase 3** additive patches and **Phase 4** extra paths — quality/coverage layers.
4. **Phase 7** testing throughout.

The two genuinely hard, exploratory chunks: **building signatures against multiple
real Resolve versions** (Phase 1.3) and **reproducing the AVS3 backport in a
self-built libavcodec** (Phase 2.2). Everything else is mechanical.

## Cheap debugging win (per new version)

Raise the IO log category to DEBUG in
`~/.local/share/DaVinciResolve/configs/log-conf.xml` (no root needed) to surface
"codec_id is not supported" and related messages directly — the fastest way to
locate equivalent sites on each new build.

## Licensing note

AAC and AC-3 were removed from a shipping product for licensing, not technical,
reasons. This is interoperability work on a locally installed binary; whether
re-enabling these codecs is appropriate for a given use — especially distributed
output — is a licensing question, out of scope here.
