# FFmpeg build inputs

- **`build-ffmpeg.sh`** — the build recipe. Reproduces Blackmagic's own configure
  line (recovered verbatim from the `--prefix` string embedded in their libraries)
  with only the AAC half of their five stripping flags removed.
- **`patches/0001-av3a-demuxer-backport.patch`** — restores the AVS3-P3 /
  "Audio Vivid" demuxer that Blackmagic's `libavformat` has and upstream FFmpeg
  6.0 does not. Without it, replacing the four libraries would silently drop a
  format Resolve ships with.

Both are explained in full in
[`../../docs/ffmpeg-libs.md`](../../docs/ffmpeg-libs.md).

```sh
scripts/dev-setup.sh ffmpeg     # clone n6.0.1, apply the patch, build, verify
```

`build-ffmpeg.sh` targets **glibc 2.28** wherever it runs: if the host glibc is
newer it re-runs itself inside a `rockylinux:8` container
(`../../scripts/old-glibc-build.sh`), and either way it checks the glibc floor of
the libraries it produced. That is what Resolve's own baseline requires — see
[`../../docs/ffmpeg-libs.md`](../../docs/ffmpeg-libs.md#targeting-glibc-228).

It refuses to start if the AV3A patch is not applied to the source tree, and
refuses to finish if the result would not load on Rocky Linux 8.
