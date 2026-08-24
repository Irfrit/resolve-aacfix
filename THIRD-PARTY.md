# Third-party components

The code in this repository is MIT-licensed (see `LICENSE`). Release tarballs
additionally bundle prebuilt third-party binaries, listed here with their
licenses and exact sources. Nothing is vendored into git — everything below is
built from source by `.github/workflows/release.yml` (or `scripts/dev-setup.sh`),
and the pins live in `scripts/versions.sh`.

## e9patch / E9Tool — GPL-3.0

`vendor/e9patch/{e9patch,e9tool}` in a release tarball.

- Source: <https://github.com/GJDuck/e9patch>
- Pinned commit: `682ab1f7f0480aa45edf1fd94457e5ee2dd01043`
  (v1.0.1 plus "Fix UB on empty trampoline template entries", #112)
- License text ships alongside the binaries as `vendor/e9patch/LICENSE`.

These are used as a *tool* — invoked as a separate process to rewrite
`bin/resolve`. Nothing in this repository links against them.

The one exception is the trampoline: `src/trampoline/aacadd.c` includes
e9patch's `examples/stdlib.c`, which its authors placed under the **MIT**
license by explicit exception ("NOTE: As a special exception, this file is under
the MIT license. The rest of the E9Patch/E9Tool source code is under the GPLv3
license."). So the compiled `vendor/aacadd` is not GPL-encumbered.

e9patch is by Gregory J. Duck et al., National University of Singapore.

## FFmpeg 6.0.1 — LGPL-2.1-or-later

`prebuilt/ffmpeg-aac/libav{codec,format,util}.so.*`, `libswscale.so.*`.

- Source: <https://github.com/FFmpeg/FFmpeg>, tag `n6.0.1`
- Build recipe: `src/ffmpeg/build-ffmpeg.sh` (reproducible; it is the exact
  script CI runs)
- Local modification: `src/ffmpeg/patches/0001-av3a-demuxer-backport.patch`

This is a plain LGPL build: **no** `--enable-gpl`, **no** `--enable-nonfree`, no
external codec libraries. AAC decode uses FFmpeg's own native decoder.
`scripts/check-ffmpeg-libs.sh` asserts this on every release build.

The libraries are otherwise unmodified upstream FFmpeg. See
`docs/ffmpeg-libs.md` for why these particular versions and configure flags.

### AV3A demuxer backport — LGPL-2.1-or-later

`src/ffmpeg/patches/0001-av3a-demuxer-backport.patch` adds
`libavcodec/av3a.h` and `libavformat/av3adec.c`, ported back to the FFmpeg 6.0
API from OpenHarmony's FFmpeg fork.

- Copyright (c) 2024 Shuai Liu, LGPL-2.1-or-later

It exists because Blackmagic's bundled `libavformat` carries an AVS3 / "Audio
Vivid" demuxer that upstream 6.0 does not have. Since we replace all four
libraries as a set, dropping it would silently remove a format Resolve ships
with.

## capstone — BSD-3-Clause

`vendor/pylibs/capstone/` in a release tarball. Upstream
<https://www.capstone-engine.org/>, installed from the official
`capstone==5.0.9` manylinux wheel. Used to disassemble and verify each located
patch site before anything is written.

## pyelftools — public domain

`vendor/pylibs/elftools/` in a release tarball. Upstream
<https://github.com/eliben/pyelftools>, from the `pyelftools==0.33` wheel. Used
to read Resolve's program headers so file offsets and virtual addresses are
derived rather than hardcoded.

## makeresolvedeb — not redistributed

`aac-fix build-deb` downloads Daniel Tufvesson's `makeresolvedeb` at runtime,
pinned to 1.10.0 and verified against a SHA-256 before it is executed. It is
deliberately **not** vendored or forked, so it stays compatible across
Blackmagic's releases. <https://www.danieltufvesson.com/makeresolvedeb>

## DaVinci Resolve — not redistributed

Nothing in this repository or its releases contains any part of DaVinci Resolve.
The patcher operates on a copy you already have installed.

## Reference

The choice of a purely-additive trampoline rewriter over the alternatives is
grounded in:

> Eric Schulte, Michael D. Brown, and Vlad Folts. 2022.
> *A Broad Comparative Evaluation of x86-64 Binary Rewriters.*
> CSET 2022. <https://doi.org/10.1145/3546096.3546112>

Cited, not redistributed.
