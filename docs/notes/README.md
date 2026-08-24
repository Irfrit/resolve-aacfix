# Working notes (unedited)

These are the original session notes, kept verbatim. They are messier than the
curated documents and parts of them are wrong — that is the point of keeping
them. The dead ends, the models that were disproved, and the "STOPPED, this is a
genuine rabbit hole" verdicts are the most expensive information in the repo, and
they are exactly what a summary would delete.

For the current state of things, read
[`../reverse-engineering.md`](../reverse-engineering.md) and
[`../further-work.md`](../further-work.md) instead.

| file | what it is | how much is still true |
|---|---|---|
| [`PLAN.md`](PLAN.md) | the original phased plan | **partly superseded.** Its phases 1 and 2 landed roughly as described. Its phase 5 (an `LD_PRELOAD` `esds→ASC` shim plus a launcher wrapper) was later eliminated entirely — that logic moved into the binary as the `aac_esds_fix` trampoline, and no launcher is needed. Its phase 3 sketch of "find a code cave" was replaced by e9patch. The licensing note at the end is still the position taken in the README. |
| [`PHASE3_RE.md`](PHASE3_RE.md) | designing the additive QuickTime patches | **mostly accurate**, and the best account of the gate-2 `insert_range` reasoning and the e9patch page-remap composability rule. Its "Packaging decision" section describes an abandoned approach (ship a `.deb` with the bundled FFmpeg deleted and a hard `Depends:` on the distro ffmpeg); we build matching-soname replacements instead. |
| [`PHASE4_SCOPING.md`](PHASE4_SCOPING.md) | the Matroska investigation, in chronological order | **the most useful file here.** It is written newest-first, with each superseded section left in place below the one that replaced it. Read it top-down to watch four successive models get disproved, including the coverage-diff wall and the moment the macOS binary resolved it. Its "REMAINING" and "STATUS: STOPPED" notes were written before the fix landed — MKV works now. |
| [`PATCH_REVIEW.md`](PATCH_REVIEW.md) | audit of the final patch set against the macOS binary | **accurate and worth reading.** It is where the honest assessment lives that gate 3 plus the esds trim is a *workaround* rather than a faithful restoration, and it records the elimination of the `LD_PRELOAD` shim. |

Two things to keep in mind while reading:

- **Every address is a 21.0.3 or 21.0.4 virtual address.** In the shipped tool
  nothing is hardcoded; sites are located by signature at run time.
- **Several files describe patches that were later replaced by better ones.** A
  design described as "chosen" in an earlier file may have been superseded in a
  later one. `PATCH_REVIEW.md` is the most recent.
