# resolve-aacfix

Restores **AAC audio decode** in DaVinci Resolve Studio 21.x on Linux.

Blackmagic ship Resolve with AAC removed for licensing reasons, so a great many
ordinary `.mp4` and `.mov` files import with silent audio. This puts it back —
without giving anything else up.

```sh
tar -xzf resolve-aacfix-*.tar.gz
cd resolve-aacfix-*
sudo ./aac-fix install          # close Resolve first
./aac-fix status
```

Download the tarball from [Releases](../../releases). It needs only `python3` and
`binutils`; everything else is bundled.

To undo it completely:

```sh
sudo ./aac-fix uninstall
```

> **Almost all of this work — the reverse engineering, the binary patches, the
> tooling, and these docs — was done by Claude** (Anthropic's Claude Code). It is
> published in that spirit: the reasoning is written down at length in
> [`docs/`](docs/), including the parts that were wrong.

---

## What it does

AAC is blocked in two places, and lifting either one alone achieves nothing:

1. **`bin/resolve`** never asks for AAC. Eight [e9patch] trampolines un-gate the
   `aac ` fourcc and AAC codec-id paths for QuickTime (`.mp4`/`.mov`/`.m4a`) and
   Matroska, and route them to FFmpeg.
2. **`libs/libav*.so`** — Blackmagic's bundled FFmpeg has the AAC decoder
   compiled *out*. Four drop-in replacements with identical sonames put it back.

`aac-fix status` reports both, and warns about the half-installed case, because
"binary patched, libs not" fails silently as no audio.

**Every binary change is purely additive.** Each trampoline acts only when the
value is AAC and otherwise lets the original instruction run untouched. No
existing instruction is rewritten. AC-3, FLAC, ALAC, MP3, PCM, the `NONE` fourcc
and Blackmagic's AV3A / "Audio Vivid" demuxer all keep working, and `uninstall`
restores the original binary byte-for-byte.

Patch sites are found by **version-independent byte signatures**, not hardcoded
offsets. On a build it does not recognise, the patcher refuses cleanly and writes
nothing.

## Caveats

Please read these before installing.

- **Studio only.** Built and tested against DaVinci Resolve **Studio**. The free
  version has not been tested at all.
- **Resolve 21 only.** Validated on 21.0.0, 21.0.3 and 21.0.4; the full
  end-to-end test was on **21.0.4**. Because sites are located by signature, newer
  21.x point releases *should* work — but this has not been tested beyond 21.0.4.
  **If the patcher refuses, or something misbehaves, please [file an
  issue](../../issues)** with your exact Resolve version and the output of
  `./aac-fix status`. A refusal is a broken feature, not a broken install: it
  writes nothing.
- **Linux, x86-64 only.**
- **Works:** `.mp4`, `.mov`, `.m4a`, and `.mkv` **that contain a video track**.
  LC-AAC and HE-AAC.
- **Does not work:** raw `.aac` (ADTS), `.ts`, `.flv`, `.latm`, MXF. These use
  other decoder classes inside Resolve that have not been analysed. See
  [`docs/further-work.md`](docs/further-work.md) — the groundwork makes them much
  cheaper than the first one was.
- **Audio-only MKV is silent — and that is a pre-existing Resolve bug, not this
  patch.** An MKV with no video track plays no audio in Resolve *for any codec*:
  it reproduces with native FLAC, AC-3 and MP3 audio-only MKVs, which Resolve
  supports and which this patch does not touch. It remains true for AAC. Put a
  video track in the file, or use another container.
- **HE-AAC sample rate:** only *explicit* SBR/PS signalling is handled. Implicit
  SBR and the AOT=31 escape will report the base sample rate.
- **Close Resolve before installing or uninstalling.** Writing a running binary
  fails with `ETXTBSY`.
- **No warranty.** This modifies a large proprietary binary in place. It keeps a
  verified backup and refuses to restore a corrupt one, but you should be able to
  reinstall Resolve if you need to.

## Licensing

AAC and AC-3 were removed from a shipping product for **licensing, not
technical, reasons**. This is interoperability work on a binary you have already
installed on your own machine. Whether re-enabling these codecs is appropriate
for a given use — especially for distributed output — is a licensing question,
and it is out of scope here.

The FFmpeg libraries are a plain LGPL build using FFmpeg's own native AAC
decoder: no `--enable-gpl`, no `--enable-nonfree`, no external codec libraries.

## Building a patched `.deb`

On Debian/Ubuntu/Mint you can build a patched package straight from Blackmagic's
`.run` installer, so the fix survives reinstalls:

```sh
./aac-fix build-deb DaVinci_Resolve_Studio_21.0.4_Linux.run
```

This wraps [makeresolvedeb] unmodified (downloaded pinned and SHA-256 verified).
Needs `fakeroot`, `dpkg-deb`, `curl`, and roughly 8 GB of free disk.

## Building from source

```sh
git clone https://github.com/josephg/resolve-aacfix && cd resolve-aacfix
scripts/dev-setup.sh          # e9patch + trampoline, capstone/pyelftools, FFmpeg
sudo ./aac-fix install
```

The FFmpeg build targets **glibc 2.28** whatever you build it on: Resolve's own
binary references at most `GLIBC_2.27`, and Blackmagic's supported baseline for
Resolve 21 is Rocky Linux 8 (glibc 2.28). Libraries built on a current distro
reference `GLIBC_2.35` and would install fine and then fail to load on a machine
where Resolve runs perfectly well. So `build-ffmpeg.sh` runs the compile inside a
`rockylinux:8` container unless the host is already that old, then verifies the
glibc floor of what it produced and fails if it is too high — locally and in CI
alike. That needs `docker` or `podman`; see
[`docs/ffmpeg-libs.md`](docs/ffmpeg-libs.md#targeting-glibc-228).

## Documentation

| | |
|---|---|
| [`docs/reverse-engineering.md`](docs/reverse-engineering.md) | how AAC was removed and how each removal was found and undone — the QuickTime and Matroska paths in detail |
| [`docs/further-work.md`](docs/further-work.md) | how to continue: the macOS binary as an answer key, the methods that worked, the mistakes that cost days, and concrete starting points for `.aac`/`.ts` |
| [`docs/tooling.md`](docs/tooling.md) | the tracer, the coverage differ, the gdb scripts and the `.text` scanners |
| [`docs/ffmpeg-libs.md`](docs/ffmpeg-libs.md) | Blackmagic's recovered configure line, soname matching, the AV3A backport |
| [`docs/packaging.md`](docs/packaging.md) | the installer's safety properties, `build-deb`, release layout |
| [`docs/notes/`](docs/notes/) | the original unedited session notes, dead ends included |

## Credits

- [e9patch] by Gregory J. Duck et al. — the trampoline rewriter that makes a
  purely-additive patch of a 653 MB stripped non-PIE binary possible at all.
- [makeresolvedeb] by Daniel Tufvesson.
- [FFmpeg](https://ffmpeg.org).
- The AV3A demuxer backport, © 2024 Shuai Liu, via OpenHarmony's FFmpeg fork.
- Schulte, Brown & Folts, *A Broad Comparative Evaluation of x86-64 Binary
  Rewriters*, CSET 2022 ([doi](https://doi.org/10.1145/3546096.3546112)) — the
  evidence behind choosing a purely-additive trampoline approach.

Licenses and exact pinned sources: [`THIRD-PARTY.md`](THIRD-PARTY.md).
This project's own code is MIT ([`LICENSE`](LICENSE)).

Nothing here contains any part of DaVinci Resolve. It patches a copy you already
have.

[e9patch]: https://github.com/GJDuck/e9patch
[makeresolvedeb]: https://www.danieltufvesson.com/makeresolvedeb
