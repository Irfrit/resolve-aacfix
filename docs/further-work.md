# Continuing this work

Written for whoever picks this up next — quite possibly the same person, months
later, with the scratch directory long deleted. It covers what to reach for
first, what actually worked, what wasted days, and concrete starting points for
the formats that are still unsupported.

Read [`reverse-engineering.md`](reverse-engineering.md) first for what the
existing patches do.

---

## The macOS binary is the answer key

**If you do one thing before starting, do this one.**

Blackmagic build Resolve from the same source on every platform. The macOS build
still has AAC. So the macOS binary is the Linux binary *with the removed code
still in it* — a reference implementation of exactly the behaviour you are trying
to reconstruct.

This is not a marginal advantage. The Matroska path had already consumed roughly
twelve build-and-test cycles, with static reading producing three successive
wrong models, and it was stuck. Diffing against the macOS build resolved it in
one pass: the missing piece was that macOS's AAC handler calls an
AudioSpecificConfig parser instead of frame-probing like AC-3 — and that parser
was still sitting in the Linux binary at `0x5b5d9f0`, with only its caller
removed. That is not something static reading of the Linux binary would ever
have suggested.

### Getting it

```sh
# Download the macOS Resolve installer from Blackmagic (same version as your
# Linux install), mount the .dmg, then:
cp "/Volumes/.../DaVinci Resolve.app/Contents/MacOS/Resolve" ./Resolve.universal
lipo -thin x86_64 Resolve.universal -output Resolve.x86_64   # it is a universal binary
```

Take the **x86_64 slice**. Comparing arm64 against x86-64 wastes the whole
advantage — you want the same instruction selection, so the two builds read
almost identically.

No macOS binary is kept anywhere in this repo or on the machine this was
developed on. It has to be re-fetched.

### Using it

Addresses do not correspond between the builds, so match by *content*:

- **String and constant anchors.** Find the same string literal or immediate in
  both, then read outward. `tools/scan/imm.py` counts 4-byte immediates —
  running it for `0x15002` on both binaries is what revealed macOS has 15
  `cmp …,0x15002` sites where Linux's shared constructor has none, i.e. macOS
  has a *dedicated* AAC extradata path and the Linux gate-3 reuse is a
  workaround.
- **RTTI.** `tools/scan/cls.py` finds a class's vtable from its mangled typeinfo
  name in either binary. Same class, same virtual layout, different addresses.
- **Structural landmarks.** Once one function is aligned, the surrounding ones
  usually follow in order.

Worked example from the Matroska fix: Linux `0x5b5d71e` (the shared "add stream"
finalize) is macOS `0x102796e20`. Anchoring there made the whole AAC handler
readable, and the trampoline design fell out of it directly.

The other thing the macOS build gives you is **negative** information — knowing
which of your patches are faithful restorations and which are inventions. gate 1
and gate 2 match what the un-gated macOS binary does. gate 3 plus the esds trim
does not. That distinction is recorded in
[`notes/PATCH_REVIEW.md`](notes/PATCH_REVIEW.md) and is worth keeping accurate.

---

## Method: what actually worked

### Ghidra did not

A headless Ghidra import of the 653 MB stripped binary was attempted and
abandoned. Everything in [`reverse-engineering.md`](reverse-engineering.md) was
found with:

1. **Direct `.text` scanning** — `tools/scan/`, numpy over the executable
   segments. Finding every RIP-relative reference to an address across 154 MB of
   code takes about two seconds.
2. **gdb on the live process** — `tools/run-resolve-gdb`.
3. **Differential block coverage** — `tools/cov.c` + `cov-run` + `cov-diff.py`.
4. **An `LD_PRELOAD` tracer on the FFmpeg API** — `tools/trace.c`.

Do not spend a day on a decompiler before trying these. Details of each are in
[`tooling.md`](tooling.md).

### The triage question, and the one command that answers it

For any format that does not work, everything depends on which side of FFmpeg the
problem is on:

```sh
make -C tools trace.so
tools/run-resolve-trace          # import the file, quit, read /tmp/avtrace.log
```

- A line like `find_decoder(id=86018 aac) -> aac` means the file **reached
  FFmpeg**. Dispatch is fine; the problem is extradata or downstream, and is
  usually small.
- **No `find_decoder` line at all** for that file means Resolve's own dispatch
  dropped it before codec selection. There is a gate to find, of the same shape
  as gate 1.

That single distinction scoped both the Matroska work and the still-open formats,
in one Resolve launch each.

### Differential coverage: the heavy artillery

When you know format X fails and format Y works, instrument every branch and call
in a region, run Resolve twice, and diff the bitmaps. Blocks Y reached that X did
not are, in order, the gates.

```sh
make -C tools cov                      # needs vendor/e9patch (scripts/dev-setup.sh e9patch)
vendor/e9patch/e9tool -M 'jmp or call' -P 'before mark(addr)@tools/cov' \
    resolve.aac-orig -o resolve.cov
# install resolve.cov, then:
tools/cov-run ac3       # import ONE working clip, quit
tools/cov-run aac       # import ONE failing clip, quit
tools/cov-diff.py -b resolve.aac-orig /tmp/cov_ac3.bin /tmp/cov_aac.bin
```

This localised the Matroska divergence to a single function in one run, after
static reading had produced three wrong models. Import exactly one clip per run —
every extra interaction adds blocks to both sides and dilutes the diff.

**Its blind spot, learned the hard way.** Coverage diffing only sees *control
flow*. One Matroska layer turned out to be a data-flow decision: a stream
"support category" at `[rsp+0x4c]` was 1 for AAC and 0 for AC-3 with no branch
divergence anywhere upstream, because it was filled by a struct copy or a callee
rather than a comparison. No amount of coverage diffing could find where it was
set. When the diff goes quiet but the behaviour still differs, switch to a gdb
**watchpoint on the value** rather than running more diffs.

### Mistakes that cost real time

Recorded because they are all repeatable:

- **Forcing a gate cascades.** Overriding the support category to force AAC
  through advanced it ~194 blocks and then forked it into container
  track-management code it was never meant to reach. Pushing a value through a
  gate that was not designed for it produces a *new* wrong path, not progress.
  Find the root categorisation instead.
- **Blind probes disprove models one cycle at a time.** `0x5b5e14b` was modelled
  as reading `codec_type`; it reads a category field that is always 2.
  `0x5b5e585` was modelled as a codec reject; it is a hot per-block loop that
  fired 233 times. Each wrong model cost a build and a live test.
- **A corrupted build produces convincing garbage.** One probe reported
  `tag10=2`, which shaped the model for a while. The trampoline had been placed
  inside that hot loop and the binary was subtly broken. If a probe result
  surprises you, re-verify the patched binary before believing it.
- **Errors that were not Resolve's.** "Could not find video stream" at import
  looked like Resolve rejecting audio-only files, and a whole theory was built on
  it. The only "video stream" string in the binary belongs to unrelated SRT
  streaming code — the messages came from the desktop thumbnailer. Grep the
  binary for an error string before theorising about it.

### Free diagnostics from Resolve itself

Raise the IO log category to DEBUG in
`~/.local/share/DaVinciResolve/configs/log-conf.xml` (no root needed). Resolve
then reports things like "codec_id is not supported" directly, which is often the
fastest way to find the equivalent site on a new build.

---

## Open format: raw `.aac` (ADTS)

The most likely next win, and the cheapest.

**What is already done for you.** ADTS carries its configuration in-band, in
every frame header — there is no `esds` and no `CodecPrivate`. So gate 3 and gate
4 are simply irrelevant here. And `IOADTSAudioDecoder` routes into the same
`IOAudioFFMPEGCodec` constructor as everything else, so gate 2 (the codec table)
and the extradata gates are already patched for it. Realistically only a
*dispatch* gate — the gate-1 equivalent — should be missing.

**Step 1, ten minutes.** Generate the clip and run the triage:

```sh
tools/make-testdata.sh -o /tmp/td
make -C tools trace.so
tools/run-resolve-trace          # import /tmp/td/ao_aac.aac, quit
grep -E 'find_decoder|open2|find_stream_info' /tmp/avtrace.log
```

If `find_decoder(id=86018 aac)` appears, dispatch already works and whatever
fails next is downstream and visible in the same log. If it does not appear, it
is a dispatch gap — patchable with the same `redirect()` trampoline as gate 1.

**Step 2, find the class.**

```sh
python tools/scan/cls.py -b /opt/resolve/bin/resolve.aac-orig 20IOADTSAudioDecoder
```

Adjust the length prefix to match the real name. That gives the vtable; the
virtuals start at `vtable+16`, and the codec-selection virtual is the one to read.

**Step 3, if it is not obvious.** Coverage-diff a working audio-only format
against the `.aac`. `ao_mp3.mkv` is not a great control (different container);
better is to compare `ao_aac.aac` against itself with and without whatever
change you are testing, or to find a raw format Resolve does accept.

**Step 4, expected shape of the fix.** A new `redirect(reg, 0x61616320, handler)`
or `redirect(reg, 0x15002, handler)` trampoline, a signature for it in
`aacpatch/sites.py`, and one more `-M`/`-P` pair in
`aacpatch/additive.py:build_patch_args`. Put it behind its own flag (like
`--mkv`) until it is verified.

## Open formats: `.ts`, `.flv`, `.latm`

Same procedure, lower expected value. Prior observation: `.ts` **imports** — the
transport-stream demux works — but no `open2` ever fires, so the audio is not
being routed to FFmpeg. That is the signature of a dispatch gap in whichever
decoder class handles it, not a demux problem. `.latm` additionally needs
`aac_latm` rather than `aac`, which our libraries do provide.

## Improving what exists

- **HE-AAC implicit SBR.** `mkv_asc` doubles the sample rate only for explicit
  SBR (audioObjectType 5) and PS (29). Implicit SBR — base AOT 2 with SBR
  detected by the decoder — and the AOT=31 escape (real type = 32 + the next 6
  bits) both report the base rate. The place to extend is marked in
  `src/trampoline/aacadd.c`.
- **A faithful gate 3.** The current AAC extradata handling reuses FLAC's
  fetch/attach path and then trims the blob. macOS has a dedicated AAC path.
  Reconstructing that would be more faithful, though the present version works
  for every case tested.
- **Older Resolve versions.** The signatures have only been exercised on 21.0.0,
  21.0.3 and 21.0.4. Running `aacpatch.locate` against a 20.x or 18.x build would
  show where they break, and widening the wildcards is preferable to adding
  per-version overrides.

## When a new Resolve release breaks the patcher

```sh
python -m aacpatch.locate /opt/resolve/bin/resolve
```

That is the whole diagnosis. It reports every site in both groups as `LOCATED` /
`ORIGINAL` / `PATCHED`, or `MISS` (not found, or found more than once). Then:

- **`MISS` — not found.** Some byte inside the signature changed. Disassemble the
  surrounding region on the old and new builds side by side and widen the pattern
  to wildcard whatever moved. Anchor only on bytes the patch never touches.
- **`MISS` — ambiguous.** The pattern now matches more than once. Lengthen it, or
  anchor on a neighbouring instruction. Never resolve this by taking the first
  match.
- **`BADVERIFY`.** The pattern matched but the capstone check disagrees about the
  instruction. Almost always means it matched the wrong place.

The patcher refuses on any of these and writes nothing, so an unrecognised build
is safe by default — it is a broken feature, not a broken install.

## Keep these outside the repo

They are far too large to commit, and re-obtaining them is slow:

- **A pristine `bin/resolve`.** `aac-patch-tree` keeps one as
  `bin/resolve.aac-orig` when you install, which doubles as the RE baseline.
  Always analyse the *unpatched* binary — e9patch output has an extra LOAD
  segment and its trampolines will show up in scans.
- **The `.run` installers**, one per version you care about. Extracting
  `bin/resolve` without executing the untrusted 10.9 GB installer: it is an
  AppImage — an ELF runtime with a squashfs appended. The superblock sits at
  `e_shoff + e_shnum * e_shentsize`, so

  ```sh
  off=$(readelf -h X.run | awk '/Start of section headers/{s=$5} /Size of section headers/{z=$5} /Number of section headers/{n=$5} END{print s+z*n}')
  unsquashfs -o "$off" -d ./extracted X.run bin/resolve
  ```

- **Test media.** `tools/make-testdata.sh` regenerates the whole matrix in
  seconds. The only thing it cannot synthesise is HE-AAC/SBR — FFmpeg's native
  encoder is LC-only — so keep one HE-AAC file around and pass it with
  `--he-source`. The original work used the EBU `ChID-BLITS-EBU-Narration.mp4`
  test signal (HE-AAC 5.1, 44.1 kHz).
