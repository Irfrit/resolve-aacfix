# The replacement FFmpeg libraries

Half the fix. Blackmagic's bundled FFmpeg has the AAC decoder compiled out, so a
patched `bin/resolve` would ask for a decoder that does not exist. These four
libraries put it back without changing anything else.

```
libavcodec.so.60.3.100     libavformat.so.60.3.100
libavutil.so.58.2.100      libswscale.so.7.1.100
```

---

## Which FFmpeg

The bundled sonames are `libavcodec.so.60.3.100`, `libavformat.so.60.3.100`,
`libavutil.so.58.2.100`, `libswscale.so.7.1.100` — that is FFmpeg **6.0**.
`n6.0.1` is the last 6.0.x patch release and carries byte-identical library
version numbers, so that is what we build. Same filenames, same sonames, same
`LIBAVCODEC_60`-style version nodes: Resolve's existing symlinks keep pointing at
the same names and nothing else in the install notices.

## The configure line

Every FFmpeg library embeds its configure string. Read out of Blackmagic's
`libavcodec`, theirs is:

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

`src/ffmpeg/build-ffmpeg.sh` reproduces every flag **identically** except the five
stripping flags, reduced to only their `ac3` halves:

```
--disable-encoder='ac3*' --disable-muxer='ac3*' --disable-demuxer='ac3'
```

So AAC decode, encode, parse, mux and demux come back, and AC-3 stays exactly as
Blackmagic had it — decoder on, encoder and muxer and demuxer off. Keeping the
rest identical is the point: this is meant to be the same library with one
configure change, not a different FFmpeg that happens to fit.

`scripts/check-ffmpeg-libs.sh` asserts all of that on every release build,
functionally — it `dlopen`s the libraries and asks them, because FFmpeg's codec
tables have hidden visibility and `nm -D` finds none of them.

## The `$ORIGIN` RUNPATH

Blackmagic's libraries carry `RUNPATH=$ORIGIN` so `libavformat` and `libavcodec`
resolve each other from `/opt/resolve/libs` rather than from the system. Ours must
too, or a machine with a system FFmpeg 6 would get a mismatched set.

Getting a literal `$ORIGIN` through configure → `config.mak` → make → sh
unmangled is not possible via `--extra-ldflags`, so the build script injects it
into `ffbuild/config.mak` directly:

```sh
sed -i "s|^LDFLAGS=|LDFLAGS= -Wl,-rpath,'\$\$\$\$ORIGIN' |" ffbuild/config.mak
```

Four dollars, single-quoted: `library.mak` expands its link recipes twice.

## The AV3A backport

Blackmagic's `libavformat` contains an AVS3-P3 / "Audio Vivid" demuxer that
upstream FFmpeg 6.0 does not have. Because we replace all four libraries as a
set, shipping without it would silently remove a format Resolve came with — and
nothing would notice until a user opened one.

`src/ffmpeg/patches/0001-av3a-demuxer-backport.patch` adds it back:

- `libavcodec/av3a.h` — header-only tables
- `libavformat/av3adec.c` — the demuxer, ported from the `FFInputFormat` API back
  to 6.0's `AVInputFormat`, plus a short-buffer guard in `av3a_probe`
- `AV_CODEC_ID_AVS3DA` appended after `AV_CODEC_ID_RKA`, giving **86119**

86119 is not an arbitrary choice — it is the exact id Blackmagic used, read out of
the `AVCodecDescriptor` in their bundled build, with the same `name`, `long_name`
and `props`. The demuxer's `name`, `long_name`, `flags`, `extensions`,
`mime_type` and `raw_codec_id` were likewise read out of their `ff_av3a_demuxer`
and matched.

Source: OpenHarmony's FFmpeg fork, © 2024 Shuai Liu, LGPL-2.1-or-later.

An earlier plan was to reproduce Blackmagic's three exported AVS3 helper functions
(`read_av3a_frame_header`, `avs3_samplingrate_table`, `codecBitrateConfigTable`)
so their `libavformat` could keep working against our `libavcodec`. That became
unnecessary once all four libraries were replaced: the only consumer of those
exports was their `libavformat`, which is gone.

**The consequence is worth stating plainly: our `libavcodec` is not a drop-in for
Blackmagic's `libavcodec` alone.** It does not export those three helpers. The
four libraries must be installed as a set, never mixed. `aac-patch-tree` always
installs all four together.

## Targeting glibc 2.28

Resolve's own binary references at most `GLIBC_2.27`, and Blackmagic's supported
baseline for Resolve 21 is Rocky Linux 8 — glibc 2.28.

Libraries built on a current distro reference `GLIBC_2.35`. They would install
cleanly and then fail to load on a platform Resolve itself runs on perfectly
well, producing a Resolve that no longer starts.

So **`build-ffmpeg.sh` builds against glibc 2.28 regardless of the host.** If the
host glibc is newer, it re-runs itself inside a `rockylinux:8` container
(`scripts/old-glibc-build.sh`), and either way it checks the glibc floor of what
it produced and fails if it is too high. A local `scripts/dev-setup.sh ffmpeg` on
a modern distro therefore produces genuinely shippable libraries, identical in
this respect to a release build.

Building on a glibc 2.39 host, the result tops out at `fcntl64@GLIBC_2.28` and
`glob64@GLIBC_2.27` — the Rocky 8 baseline exactly.

The first container build creates a small derived image with the build
dependencies baked in (`resolve-aacfix-builder:el8`); later builds reuse it, and
the e9patch build uses the same image. It installs `which` and `xxd` explicitly:
neither is in the `rockylinux:8` base image, and e9patch's `build.sh` probes for
its tools with `which`, so without it the build fails claiming gcc is missing
when gcc is installed and working.
`nasm` is pulled from the PowerTools/CRB repo — without it FFmpeg quietly
configures `--disable-x86asm` and produces a much slower library that no longer
matches what Blackmagic shipped, so the image build asserts `nasm -v` succeeds.

`FFMPEG_NO_CONTAINER=1` builds with the host toolchain instead. That is for
iterating locally; the glibc check at the end of the script will say so, loudly.

### Why not symbol-version pinning?

The usual alternative is `.symver` directives pinning `exp`, `pow`, `pthread_*`,
`dlopen` and friends back to `GLIBC_2.2.5`. It was considered and rejected:

- It only covers the symbols you thought of. It happens to fail loudly here
  because of the floor check, but it needs maintenance every time the build
  changes.
- It **silently substitutes older implementations**. glibc 2.29's `exp`/`pow` and
  2.35's `hypot` are different code from the 2.2.5 versions. That cuts directly
  against the point of this build, which is to be Blackmagic's library with one
  configure flag changed and nothing else.
- It cannot express `fstat64` at all: glibc before 2.33 has no such symbol, only
  `__fxstat64`, so that one needs a hand-written wrapper passing the right
  `_STAT_VER` — real glibc-internals surgery in a library that decodes untrusted
  media.

Compiling against the real 2.28 headers and symbols has none of those problems
and needs no maintenance.

## Verification

`scripts/check-ffmpeg-libs.sh DIR` runs on every release build and checks:

- the four exact filenames, with the sonames Resolve's binaries link against
- `RUNPATH=$ORIGIN` on each
- glibc floor ≤ 2.28
- AAC decoder, `aac_fixed`, `aac_latm`, and the native AAC encoder all present
- AC-3 decoder present; AC-3 encoder, muxer and demuxer absent (matching Blackmagic)
- `av3a` demuxer present and `AV_CODEC_ID_AVS3DA == 86119` — i.e. the backport was
  actually applied
- a plain LGPL build: no `--enable-gpl`, no `--enable-nonfree`

That last group is not paranoia. An earlier hand-built set of these libraries was
missing the AV3A demuxer entirely — the patch had not been applied to the tree
that produced them — and nothing caught it until this check existed.

Beyond that, the original work diffed exported symbols against the bundled
libraries: every FFmpeg symbol imported by *any* ELF under `/opt/resolve` (61 in
total) is exported by this build, and the only symbols missing relative to
Blackmagic's are the three AVS3 helpers discussed above.

## Building them

```sh
scripts/dev-setup.sh ffmpeg      # clone n6.0.1, apply the patch, build, verify
```

Or by hand:

```sh
git clone --branch n6.0.1 https://github.com/FFmpeg/FFmpeg _ffmpeg/src
git -C _ffmpeg/src apply "$PWD/src/ffmpeg/patches/0001-av3a-demuxer-backport.patch"
FFMPEG_SRC=_ffmpeg/src ./src/ffmpeg/build-ffmpeg.sh
```

Needs `git` and a working `docker` or `podman` on the host; the compiler,
`nasm` and the rest live in the container image. On a host that is already
glibc ≤ 2.28 no container is used and the host toolchain is needed instead.

The build script refuses to start if the AV3A patch is not applied, and refuses
to finish if the libraries it produced reference too new a glibc.
