# Packaging and installation

How the pieces fit together, for anyone modifying the installer or building a
release.

---

## Two tools

| tool | job |
|---|---|
| **`aac-patch-tree`** | the actual work: apply / revert / status on *any* Resolve tree — a directory containing `bin/resolve` and `libs/`. Knows nothing about sudo, installers or `.deb`s. |
| **`aac-fix`** | a thin wrapper for the two ways you would drive it: a live install, or an installer via makeresolvedeb. |

The split exists so the interesting logic never has to care about privilege or
packaging. `aac-patch-tree` is equally happy with `/opt/resolve`, an extracted
installer tree, or a scratch copy.

```sh
aac-patch-tree apply  [--originals keep|remove] [--no-mkv] [-v] <ROOT>
aac-patch-tree revert <ROOT>
aac-patch-tree status <ROOT>

aac-fix install   [RESOLVE_ROOT]     # default /opt/resolve; re-execs via sudo if needed
aac-fix status    [RESOLVE_ROOT]
aac-fix uninstall [RESOLVE_ROOT]
aac-fix build-deb INSTALLER.run [MAKERESOLVEDEB.sh]
```

## `--originals keep` vs `remove`

`keep` (the default) leaves the untouched originals in the tree as
`bin/resolve.aac-orig` and `libs/_bmd_orig/`, so `revert` restores them in place.
That is what you want for a live install — and the kept binary doubles as the
baseline for any future reverse engineering.

`remove` patches without leaving them behind. That is for staging a package,
where the backups would add roughly 670 MB of dead weight (a second copy of the
653 MB binary). `revert` will not work afterwards; you revert by reinstalling the
stock package.

Re-running `apply` is safe either way: if the binary is already patched it
re-patches **from the kept original**, never from the patched binary.

With `--originals remove` there is no kept original, so `apply` and `revert` both
refuse and say so. That is not a gap to be closed — **e9patch has no unpatch**,
and nothing here can reconstruct a 653 MB binary it did not keep. The patched
output does happen to leave the original bytes intact and append to them, but
that is observed behaviour of a tool we do not control rather than a documented
contract, so it is not something to build a rollback on.

The workflows that mode is for do not need in-place re-patching anyway:

| you have | to get back to stock | to re-patch |
|---|---|---|
| a live install, `--originals keep` | `aac-fix uninstall` | `aac-fix install` |
| a live install, `--originals remove` | reinstall the package | reinstall, then `aac-fix install` |
| a patched `.deb` | install the stock `.deb` | `aac-fix build-deb` again from the `.run` |

`build-deb` always starts from a freshly-extracted installer, so it never has an
already-patched binary to deal with.

## Safety properties worth preserving

Everything below exists because the tool writes into a working Resolve install.
If you change this code, keep them.

- **Locate before write.** Every patch site is resolved by signature first. A
  missing *or ambiguous* site aborts before anything is touched. More than one
  match is an error, never "take the first" — a build we cannot identify must stop
  the tool.
- **No partial patches.** e9tool can decline a site and still exit 0. The driver
  parses `num_patched = N / M`, checks it against the number of patches it asked
  for, and deletes the output if they disagree. A binary with only some gates
  installed is worse than no binary: a redirect can jump into a gate that was
  never installed.
- **Atomic everything.** The patched binary is written to `bin/resolve.aac-tmp`
  and renamed. Backups are written to `.tmp` and renamed. A `cp` interrupted by
  Ctrl-C, a full disk, or an OOM kill must never leave a truncated backup that a
  later `revert` would install over a working binary.
- **Backups are verified before use.** `revert` checks that the kept original is
  a valid, complete, unpatched ELF, and matches the SHA-256 recorded beside it,
  before restoring it. If it does not, it refuses and leaves the current binary
  alone. Restoring a truncated backup over a working install is the worst thing
  this tool could do.
- **A refused patch leaves nothing behind.** Including the 653 MB backup it took
  earlier in the same run. The most likely reason to hit a refusal is a Resolve
  version this tool does not know, and the user should be able to retry a newer
  release without hunting down a stray copy of their binary.
- **Fixed permissions, never `--reference`.** The patched binary is `chmod 0755`
  and, under sudo, `chown 0:0`. Copying the mode from a developer's
  world-writable working copy would make `/opt/resolve/bin/resolve`
  world-writable — local code execution for anyone who launches Resolve. The libs
  use `cp` rather than `cp -p` for the same reason: `cp -p` would preserve the
  *source* ownership, which under sudo actually succeeds.
- **Environment overrides are ignored under sudo.** `AAC_E9TOOL`,
  `AAC_TRAMPOLINE` and `AAC_LIBS` all select executable payload. Honouring them
  when running as root via sudo would turn a restricted NOPASSWD sudoers rule for
  this tool into a trivial root-code-execution primitive. Set
  `AAC_ALLOW_ENV_OVERRIDE=1` to opt in deliberately.
- **The payload is checksummed.** A release tarball carries `SHA256SUMS` covering
  everything in `vendor/` and `prebuilt/`, and the tool refuses to patch if it
  does not verify. A tampered or truncated download cannot reach `/opt/resolve`.
- **Patch before launch.** Writing a running binary fails `ETXTBSY`. Close
  Resolve first.

## Detecting a patched binary

Stock Resolve is an ELF `EXEC` with exactly **4** `PT_LOAD` segments. e9patch
preserves those and appends a 5th for its trampolines, so the segment count is
the whole test. No marker string is injected.

An earlier version also grepped for an "AAC Fix" marker. That only ever worked
because of a stale prebuilt trampoline containing a long-deleted symbol, and it
silently degraded to "not patched" once the trampoline was rebuilt from source —
which is exactly the sort of quiet wrongness this tool must not have.

## `build-deb`

Builds a patched `.deb` straight from Blackmagic's `.run` installer, on
Debian/Ubuntu/Mint:

```sh
aac-fix build-deb DaVinci_Resolve_Studio_21.0.4_Linux.run
```

It reuses Daniel Tufvesson's [makeresolvedeb] **unmodified**. Run with
`CI_TEST=1`, makeresolvedeb extracts the `.run` and stages the whole
`opt/resolve` tree but stops before packaging. We then run
`aac-patch-tree apply --originals remove` on the staged tree, mark the control
file (`+aac1`, plus a note in the description), and run `dpkg-deb -b`.

Not forking makeresolvedeb is deliberate — it stays compatible across
Blackmagic's releases. It is downloaded pinned to 1.10.0 and **verified against a
SHA-256 before being executed**; a mismatch refuses rather than running an
unverified script.

Needs `fakeroot`, `dpkg-deb`, `tar` and `curl`, and a lot of disk: the staged
tree plus two `.deb`s runs to roughly 8 GB.

[makeresolvedeb]: https://www.danieltufvesson.com/makeresolvedeb

## Repo layout is release layout

`vendor/` and `prebuilt/` are gitignored and filled in by CI (or
`scripts/dev-setup.sh`). The release tarball is simply the repo with those two
populated, plus a `SHA256SUMS` manifest — so paths behave identically in a git
checkout and in an extracted tarball, and there is no separate packaging layout
to keep in sync.

```
aac-fix  aac-patch-tree      entry points
aacpatch/                    signature engine + e9tool driver
src/trampoline/aacadd.c      the patch itself
src/ffmpeg/                  build recipe + AV3A backport patch
vendor/e9patch/{e9patch,e9tool}    built by CI
vendor/aacadd                      compiled trampoline
vendor/pylibs/{capstone,elftools}  so a release needs only python3
prebuilt/ffmpeg-aac/*.so           built by CI
tools/  docs/  scripts/  tests/
```

## Building a release

```sh
scripts/dev-setup.sh          # pylibs, e9patch + trampoline, ffmpeg
scripts/make-release.sh
```

`make-release.sh` stages the tree, re-verifies the payload (glibc floor, sonames,
codec inventory, trampoline exports), writes `SHA256SUMS`, and packs the tarball.
It refuses if anything is missing or fails a check.

The FFmpeg libraries come out targeting glibc 2.28 wherever you build them —
`build-ffmpeg.sh` containerises itself when the host is newer, so
`scripts/dev-setup.sh ffmpeg` produces shippable libraries locally. See
[`ffmpeg-libs.md`](ffmpeg-libs.md#targeting-glibc-228).

e9patch and the trampoline go through the same container, so
`scripts/dev-setup.sh` on a modern distro produces a fully shippable payload and
`make-release.sh` passes without any escape hatch. CI runs the identical code
path — its jobs have no `container:` of their own and no package list to drift.
Tag `v*` to trigger a release.

`--allow-newer-glibc` skips the floor check if you deliberately built natively
with `AAC_NO_CONTAINER=1` / `FFMPEG_NO_CONTAINER=1`; it prints a loud warning and
the result must never be handed to anyone.

## Requirements

**To install from a release tarball:** `python3` and `binutils` (for `readelf`).
capstone, pyelftools, e9patch and the FFmpeg libraries are all bundled.

**To run from a git checkout:** additionally `capstone` and `pyelftools`, or run
`scripts/dev-setup.sh pylibs` to vendor them.

**To build:** `git`, and `docker` or `podman`. The whole toolchain — gcc, nasm,
`xxd`, zlib headers — lives in the container image, so nothing but those two is
needed on the host. On a host that is already glibc ≤ 2.28 no container is used
and the host toolchain is needed instead.

**For `build-deb`:** `fakeroot`, `dpkg-deb`, `tar`, `curl`.
