# Pinned third-party versions, shared by scripts/ and .github/workflows/.
# Sourced, not executed.

# e9patch: v1.0.1 plus "Fix UB on empty trampoline template entries" (#112).
# That fix is not in any tag, so we pin the commit.
E9PATCH_REPO=https://github.com/GJDuck/e9patch
E9PATCH_COMMIT=682ab1f7f0480aa45edf1fd94457e5ee2dd01043

# FFmpeg 6.0.1 -- the last 6.0.x, and the one whose library version numbers are
# byte-identical to the ones Blackmagic bundles (libavcodec.so.60.3.100 etc).
FFMPEG_REPO=https://github.com/FFmpeg/FFmpeg
FFMPEG_TAG=n6.0.1

# Highest glibc symbol version the shipped binaries may reference.  Resolve's
# own binary tops out at GLIBC_2.27, and Blackmagic's supported baseline for
# Resolve 21 is Rocky Linux 8 == glibc 2.28.  Anything we ship must load there.
MAX_GLIBC=2.28

# The four bundled FFmpeg libraries we replace, by exact filename.
FFMPEG_LIBS="libavcodec.so.60.3.100 libavformat.so.60.3.100 libavutil.so.58.2.100 libswscale.so.7.1.100"

# The symbols the trampoline must export -- one per e9patch patch site.
TRAMPOLINE_SYMS="redirect build5 aac_esds_fix mkv_undrop mkv_asc"

# Container image providing MAX_GLIBC.  Builds run inside this whenever the host
# glibc is newer, so a local build produces the same shippable libraries CI does.
GLIBC_CONTAINER=rockylinux:8
# Derived builder image (base + build deps), cached between runs.
GLIBC_BUILDER_IMAGE=resolve-aacfix-builder:el8
