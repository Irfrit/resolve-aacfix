#!/usr/bin/env bash
#
# make-testdata.sh [-o OUTDIR] [--he-source FILE]
#
# Generate the codec x container test matrix used to check the AAC fix.
#
# The clips are synthesised with the system ffmpeg rather than committed, both
# to keep the repo small and because the originals were stream-copied from the
# EBU "ChID-BLITS-EBU-Narration.mp4" test signal, which carries its own terms.
#
# What each group is for:
#
#   av_aac.*  audio-only_aac.*   the actual feature.  av_* have a video track,
#                                ao_* do not.
#   av_ac3.*  av_flac.*  av_mp3.*  ao_*   REGRESSION controls.  The patch is
#                                additive, so these must keep working exactly as
#                                they did before it was applied.  They are also
#                                the "known good" side of a coverage diff (see
#                                cov-run / cov-diff.py).
#   ao_*.mkv                     these are why the README says audio-only MKV is
#                                a pre-existing Resolve bug: ao_flac.mkv and
#                                ao_ac3.mkv are silent too, with no patch of
#                                ours anywhere near them.
#
# HE-AAC (SBR) and HE-AACv2 (PS) cannot be produced by FFmpeg's native AAC
# encoder, so they are only generated if you point --he-source at a file that
# already contains HE-AAC; it is stream-copied into each container.  Without it
# the matrix covers LC-AAC only, and the explicit-SBR sample-rate path in the
# mkv_asc trampoline goes untested.
#
# Not generated: MXF.  ffmpeg cannot mux AAC into MXF without a video stream,
# and Resolve's MXF audio is normally PCM, so the combination does not occur in
# practice.
#
set -euo pipefail

OUT=testdata
HE_SRC=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o|--out) OUT="$2"; shift 2 ;;
        --he-source) HE_SRC="$2"; shift 2 ;;
        -h|--help) sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//;$d'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

command -v ffmpeg >/dev/null || { echo "need ffmpeg on PATH" >&2; exit 1; }
mkdir -p "$OUT"

FF=(ffmpeg -hide_banner -loglevel error -y)
DUR=3
# A 3s 320x240 test pattern + a two-tone stereo sine.  Deliberately tiny: these
# exist to exercise decode paths, not to look at.
VSRC=(-f lavfi -i "testsrc=size=320x240:rate=25:duration=$DUR")
ASRC=(-f lavfi -i "sine=frequency=440:sample_rate=48000:duration=$DUR")

gen() {   # gen <label> <ext> <acodec-args...>
    local name="$1" ext="$2"; shift 2
    "${FF[@]}" "${VSRC[@]}" "${ASRC[@]}" -shortest \
        -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$@" \
        "$OUT/av_$name.$ext" && echo "  av_$name.$ext"
}
gen_ao() { # gen_ao <label> <ext> <acodec-args...>  -- audio only
    local name="$1" ext="$2"; shift 2
    "${FF[@]}" "${ASRC[@]}" "$@" "$OUT/ao_$name.$ext" && echo "  ao_$name.$ext"
}

echo "==> AAC (LC) -- the feature"
for ext in mp4 mov mkv; do gen aac "$ext" -c:a aac -b:a 128k; done
for ext in m4a mkv aac latm ts flv; do gen_ao aac "$ext" -c:a aac -b:a 128k; done

echo "==> regression controls (must still work after patching)"
for ext in mkv mov; do gen ac3 "$ext" -c:a ac3 -b:a 192k; done
gen flac mkv -c:a flac
gen mp3  mkv -c:a libmp3lame -b:a 192k
gen pcm  mov -c:a pcm_s16le
# The audio-only MKVs: all three are silent in Resolve, patched or not.  Keep
# them -- they are the evidence that the audio-only-MKV limitation is not ours.
gen_ao ac3  mkv -c:a ac3 -b:a 192k
gen_ao flac mkv -c:a flac
gen_ao mp3  mkv -c:a libmp3lame -b:a 192k

if [ -n "$HE_SRC" ]; then
    echo "==> HE-AAC, stream-copied from $HE_SRC"
    [ -f "$HE_SRC" ] || { echo "no such file: $HE_SRC" >&2; exit 1; }
    for ext in mp4 mov m4a mkv aac latm ts flv; do
        if "${FF[@]}" -i "$HE_SRC" -vn -c:a copy "$OUT/he_aac.$ext" 2>/dev/null; then
            echo "  he_aac.$ext"
        else
            echo "  he_aac.$ext  (skipped: container rejected the stream copy)"
        fi
    done
    # HE-AAC with a video track is the only MKV case expected to produce audio.
    "${FF[@]}" "${VSRC[@]}" -i "$HE_SRC" -shortest -map 0:v -map 1:a \
        -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a copy \
        "$OUT/av_he_aac.mkv" && echo "  av_he_aac.mkv"
else
    echo "==> HE-AAC: skipped (pass --he-source FILE to include SBR/PS coverage)"
fi

echo
echo "wrote $(find "$OUT" -type f | wc -l) files to $OUT"
echo "verify:  for f in $OUT/*; do ffprobe -v error -show_entries stream=codec_name,codec_type -of csv \"\$f\"; done"
