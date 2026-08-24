# Phase 3 — reverse-engineering notes (additive AAC, non-destructive)

Goal: add AAC without sacrificing AC-3 (gate2), FLAC (gate3), or the NONE fourcc
(gate1). Mechanism: **e9patch** trampolines (source at `~/3rdparty/e9patch`, built)
— appends a new LOAD segment and leaves the original image byte-identical.

Offsets below are for **21.0.3** (installed). All are located version-independently
by the Phase 1 signature engine; the RE here is to design the additive edits.

## Packaging decision (Phase 2/6, recorded for later)

On Linux Mint, ship via a modified `makeresolvedeb`: build the `.deb`, **delete the
duplicated bundled FFmpeg `.so`s from the package**, and add a hard `Depends:` on the
distro `ffmpeg` / `libavcodec` (which has AAC). Revisit building our own `.so`s later.
This removes the DT_RPATH shadowing problem (the bundled libs simply aren't in the
package) without moving files at runtime.

## e9patch de-risk (done)

`e9tool -M 'addr=0x..' -P 'print' resolve -o out` on the full 653 MB binary:
rewrote in **3.3 s, ~10 MB RSS**, produced a valid ELF EXEC. The original 4 LOAD
segments are preserved at identical offsets/vaddrs; a 5th LOAD (trampolines) is
appended. Implications:
- Phase-2 in-place byte edits and Phase-3 trampolines **compose** on one binary.
- Signature offsets stay valid on e9patch output.
- ~20 "failed to disassemble byte" warnings = data-in-code; none at our sites
  (handled by e9patch's `--Dsync`).

## GATE 2 — codec table (restore AC-3). HARDEST; now de-risked.

### How the map is built (static init, `0xcb2590`)
```
movups xmm0,[rip->0xa497f1c]   ; table[16:32]  (.mp3, ac-3 rows)
movaps [rsp+0x20],xmm0
movups xmm0,[rip->0xa497f0c]   ; table[0:16]   (alac, flac rows)
movaps [rsp+0x10],xmm0         ; 32-byte stack copy of the 4-row table
lea    rbx,[rip->0x2759fdb0]   ; rbx = the std::map<u32,int> object
lea    rsi,[rsp+0x10]          ; rsi = &table
lea    rcx,[rsp+0xf]           ; DEAD — insert_range never reads rcx
mov    edx,4                   ; count
mov    rdi,rbx
call   0x5b57710               ; insert_range(map=rdi, table=rsi, count=rdx)
```
Table rows (8 bytes each, `{u32 fourcc_le, u32 codec_id}`), at file `0xa097f0c`:
`alac`/0x15010  `flac`/0x1500c  `.mp3`/0x15001  `ac-3`/0x15003. AAC would be 0x15002.

### insert_range @ `0x5b57710` — the reusable builder
```
insert_range(rdi=map, rsi=table, rdx=count):
  init map: [rdi+8..+0x18] = 0 ; [rdi] = rdi+8          <-- RE-INITS every call
  if rdx==0 return
  end = table + count*8                                 <-- confirms 8-byte entries
  for e in table[0..count):
     walk red-black tree; call 0xdc6270 (alloc+link node, copies the 8 bytes)
     [map+0x10]++ (size)
```
Contract: **clean `(map, table, count)`**, 8-byte entries, `rcx` unused, and it
**resets the map on entry** — so calling it once with a 5-entry table fully replaces
the 4-entry build.

### Additive plan (chosen)
e9patch call trampoline positioned **after** the original `call insert_range`
(`0xcb25d7`): call our `build5(map)` which does
`insert_range(map, TABLE5, 5)` where `TABLE5` = the 4 original rows + `{'aac ',
0x15002}`, and `insert_range` is the binary's own function at `0x5b57710`.
- Runs after the stock 4-entry build; re-init makes our 5-entry build win.
- One-time leak of 4 nodes at startup (re-init doesn't free) — negligible.
- No lea/rcx/register surgery; reuses a fully-understood function.
- `map` pointer = `rbx` at the call site → pass as the arg via e9tool.

### PoC — DONE (mechanism proven, functional test needs a running Resolve)
`aacext/gate2_table.c` compiles (via `e9compile.sh`) to:
```
build5(rdi=map, rsi=insert_range):
  mov rax,rsi ; mov edx,5 ; lea rsi,[rip->TABLE5] ; jmp rax   ; tail-call, map passthrough
```
TABLE5 emitted byte-perfect: the 4 original rows + `20 63 61 61 02 50 01 00` (aac→0x15002).
Wired with `e9tool -M 'addr=0xcb25dc' -P 'build5(rbx,0x5b57710)@gate2_table'` — patches
1/1 in 3.2 s, emits a valid 676 MB ELF. `rbx`=map is preserved across the call
(callee-saved), so the map pointer is correct at the after-call site. The
insert_range address is a plain arg → the driver bakes in the signature-resolved
value per build (version-independent).

### e9patch page-remap — composability rule (important)
e9patch does NOT edit on-disk bytes at the patch site; it preserves the original
image and **remaps the patched 4 KB page(s) at load time** via added program
headers (verified: bytes at `0xcb25dc` identical in input vs output; only the ELF
header page and an appended region differ on disk). Consequence for a HYBRID of
Phase-2 in-place edits + Phase-3 trampolines:
- **Apply Phase-2 static byte edits FIRST, then run e9patch on the result**, so the
  edits are baked into the pages e9patch copies into its remap.
- Or go fully-additive (all changes via e9patch). Either way the Phase-1 locator
  still resolves every site on e9patch output (verified).

## GATE 3 — extradata gates (restore FLAC). SOLVED with 2 trampolines.
Reversed the whole extradata function (`0x5b5679b`..`0x5b56973`). Flow: load
codec_id; ALAC/FLAC → FETCH (virtual `[rax+0x50]` fills a stack blob [rsp]/[rsp+8]);
then ALAC → cookie path, FLAC → `sub rdx,rsi; cmp rdx,0x22; je COPY`. **Key finding:
COPY (`0x5b5691f`) recomputes the length from the stack (`[rsp+8]-[rsp]`), NOT from
rdx** — so AAC can jump straight to COPY, skipping the size gate, and it still copies
correctly. Additive design (`redirect(reg,0x15002,target)` via `if CALL goto`):
- **gate3a** @ `cmp ecx,0x1500c` (0x5b567a7): AAC → FETCH (`0x5b567b3`).
- **gate3b** @ `cmp edi,0x1500c` (0x5b56801): AAC → COPY (`0x5b5691f`) directly.
- **gate3c** (the `cmp rdx,0x22` size gate): **left untouched** — FLAC/ALAC unchanged.

## GATE 1 — dispatch (preserve NONE). SOLVED, additive.
Match the stable `.mp3` cmp (`0x5b61670`); `if redirect(r15,0x61616320,FFmpeg_handler)
goto`. AAC gets routed to the same IOAudioFFMPEGCodec handler as `.mp3`/`flac`; the
NONE branch and everything else are untouched. Handler addr = the `.mp3` je target.

## FINAL DESIGN — fully additive, upstream-preserving
Four e9patch trampolines, one shared trampoline object (`aacext/aacadd.c` →
`redirect()` + `build5()`), all addresses resolved per build by the Phase-1
signatures (`aacpatch/additive.py`):
| gate | site (21.0.3) | trampoline | preserves |
|---|---|---|---|
| 1 | 0x5b61670 | `if redirect(r15,'aac ',FFmpeg) goto` | NONE + all fourccs |
| 2 | 0xcb25dc  | `build5(rbx,insert_range)` (5-row table) | AC-3 |
| 3a| 0x5b567a7 | `if redirect(rcx,AAC,FETCH) goto` | FLAC/ALAC |
| 3b| 0x5b56801 | `if redirect(rdi,AAC,COPY) goto` | FLAC/ALAC (+size gate untouched) |
`redirect` returns target iff `(u32)reg==wanted` else 0 → original insn runs.

## STATUS — installed
`aacpatch/additive.py` produced the patched binary (e9tool 4/4, valid ELF, orig 4
LOAD segments preserved). **Installed to `/opt/resolve/bin/resolve`**; original
backed up byte-identical to `resolve.orig` (653,047,344 B). Locator + all Phase-1
sites still compose on the installed binary.

### FUNCTIONAL TEST — PASSED for the QuickTime path (2026-08-20, 21.0.3.0007)
Bundled libs moved aside; launched via `resolve-aac`. `/tmp/aacfix.log`: **6/6
`open2(aac) -> 0`** — HE-AAC 5.1 (41→4 ASC) and LC stereo/5.1 (42→5). All six
mp4/mov/m4a clips **import, decode, and play**. AC-3/FLAC preservation not yet
directly retested (no AC-3/FLAC clip imported), but by construction untouched.

Not yet working (all NON-QuickTime container paths → Phase 4):
- `.ts` — imports (TS demux ok) but no `open2` fires → audio not routed to FFmpeg;
  its codec dispatch is a different class than IOQuickTimeAudioDecoder.
- `.mkv/.aac/.flv/.latm` — `Error: Could not find video stream` at import → Resolve's
  Matroska/ADTS/FLV demuxers reject audio-only input before codec selection.
These are separate decoder classes (IOADTS/IOMKV/…); the QuickTime gates don't cover
them. `trace.so` now wired into `resolve-aac` (TRACE=1) to log find_decoder /
find_stream_info to pinpoint dispatch-vs-demux for each.

### Remaining for a functional test (needs the user — sudo / launching Resolve)
1. `./move-bundled-libs.sh` — move bundled FFmpeg aside so system ffmpeg (has AAC,
   soname-compatible, AVS3 symbols unreferenced) loads. Reversible (`restore`).
2. Launch via `./resolve-aac` (sets `LD_PRELOAD=aacfix.so` gate4 shim + `AACFIX_LOG`).
3. Import `testdata/*` and check `/tmp/aacfix.log` for `open2(aac) … -> 0` and
   verify AC-3/FLAC clips still import (the additive guarantee).

## Priority (done, most-impactful first)
1. gate2 / AC-3 — highest uncertainty, de-risked + implemented.
2. gate3 / FLAC — 2 trampolines, gate3c untouched.
3. gate1 / NONE — additive branch, NONE preserved.

## Test data (`testdata/`, from ChID-BLITS-EBU-Narration.mp4, HE-AAC 5.1)
Containers (stream-copy, HE-AAC 5.1): `he_5p1.{mp4,mov,m4a,mkv,ts,flv,aac,latm}`.
Re-encoded LC: `lc_stereo.{mp4,mov,mkv,aac}`, `lc_5p1.mp4`. MXF+AAC not producible
via ffmpeg (needs a video stream) and Resolve MXF audio is normally PCM — skipped.
Decode-path coverage: mp4/mov/m4a → IOQuickTimeAudioDecoder; aac(ADTS) →
IOADTSAudioDecoder; mkv → IOMKVAudioDecoder; ts/flv/latm → other demux paths.
