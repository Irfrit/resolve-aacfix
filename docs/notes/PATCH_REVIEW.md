# Patch review (after MKV + macOS-binary study)

## Current patch set — all needed, no redundancy

QuickTime (4 trampolines, `resolve_addresses`):
- **gate1** route `"aac "` fourcc -> IOAudioFFMPEGCodec. Matches Mac (un-gated dispatch has an aac case).
- **gate2** (`build5`) 5-entry fourcc->AVCodecID table (aac=86018, keeps AC-3). Matches Mac (its table has aac).
- **gate3a / gate3b** fetch + attach the config blob for AAC. **Shared by MKV too** (MKV routes into the same IOAudioFFMPEGCodec ctor).

MKV (3 trampolines, `resolve_mkv`, now signature-located):
- **mkv_undrop** un-drop A_AAC at the parser.
- **mkv_asc** parse CodecPrivate (ASC) instead of frame-probing (+ SBR sample-rate ×2).
- **dispatch-redirect** route codec_type 1 -> the FFMPEG ctor arm.

Shim: **aacfix.so** (LD_PRELOAD) extracts the ASC from the esds at avcodec_open2.

Every item covers a distinct gate; removing any breaks a case. gate3a/gate3b and
gate2 are shared across both containers.

## macOS diff finding — gate3+shim is a WORKAROUND

Mac's gate3 is byte-identical to Linux's: it handles **only ALAC (0x15010) and FLAC
(0x1500c), never AAC (0x15002)**. So Mac does NOT route AAC through gate3 — it has a
**dedicated AAC extradata path** (15 `cmp …,0x15002` sites in the Mac text, none at
the ctor/gate3 region). My Linux patch instead *reuses the FLAC fetch/attach path*
for AAC; that path attaches the raw esds, which FFmpeg rejects — hence the shim
unwraps esds->ASC at open2. It works for every case (MP4/MOV/M4A/MKV, LC + HE-AAC),
but it is not how the original code does it.

## ★ Shim ELIMINATED — pure binary patch (done)

Both Mac and Linux gate3 handle only ALAC/FLAC, so there was no in-binary AAC
esds->ASC path to "restore". Instead the shim's logic was moved into the binary as
an e9patch trampoline `aac_esds_fix` at the extradata COPY site (`a['copy']`, the
gate3c je target): if the config blob is an esds (starts with 0x03) it moves the ASC
(DecSpecificInfo, tag 0x05) to the buffer start in place and shortens the length, so
COPY attaches just the ASC. Serves BOTH containers (both reach this COPY).

VERIFIED: with NO aacfix.so preloaded, `open2(aac … extradata=4/5) -> 0` (ASC, not
the 35-41 byte esds), audio plays in MP4 and MKV.

Result: **no LD_PRELOAD, no shim .so** — the entire feature is 8 additive e9patch
trampolines on one binary:
  QuickTime: gate1 dispatch, gate2 build5 table, gate3a fetch, gate3b attach, gate4
             aac_esds_fix (esds->ASC)
  MKV:       mkv_undrop, mkv_asc (CodecPrivate + SBR), dispatch-redirect
`aacfix.so` / `trace.so` are now debug-only; the shipped patch needs neither, and no
launcher wrapper is required (run resolve directly).

## gate1/gate2 — confirmed matching Mac
The dispatch routing and the codec table are exactly what the un-gated Mac binary
does; those are faithful restorations, not workarounds.
