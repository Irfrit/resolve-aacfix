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

The script refuses to start if the AV3A patch is not applied to the source tree.
