# Design: hwpkit, verified HWP → HWPX conversion and editing on macOS

## Status

| Part | State |
|---|---|
| `.hwp` → `.hwpx` with the headless Java engine (`engine_java.py`, `java/`) | implemented, default route (~0.1–0.2 s per small document, no GUI) |
| Hancom Office GUI export to `.pdf`, `.hwp`, `.docx`, `.odt`, … (`hancom_export.py`) | implemented |
| Hancom GUI as fallback when the Java engine fails on `.hwp` → `.hwpx` | implemented (notice on stderr; never for encrypted input) |
| HWPX content comparer (`hwpx_compare.py`) | implemented |
| Job directory, `edit` ops, `verify` (inventory + render diff), `rollback`, `doctor`, Claude skill | **planned** (design below, not built) |

Everything under "Planned v1" describes intended behavior. Where the code differs from the
original plan, the "Implemented" section is authoritative.

## Problem

Legacy `.hwp` (HWP 5.x, OLE binary) is opaque to an LLM. `.hwpx` (OWPML, zipped XML) is editable text.
Goal: let Claude convert a `.hwp`, edit the document, and hand back a file that looks right in
Hancom Office, as `.hwpx` by default or `.hwp` when a recipient needs it, with **proof** of what changed:
a report that separates *conversion damage* (original `.hwp` vs converted `.hwpx`) from *intended edits*
(`.hwpx` before vs after), backed by page renders from Hancom itself.

Context: Korean public-sector systems are moving to HWPX (Hancom has announced HWPX as required on the
government On-Nara document system from May 2026), so this conversion is becoming routine.

## Constraints

- macOS only (Apple Silicon assumed). LibreOffice cannot write HWPX. A JDK is needed (`brew install openjdk`).
- Hancom Office HWP for Mac (tested: 12.30) has **no AppleScript dictionary**; automation is System
  Events GUI scripting, which needs the Accessibility permission for the app that runs the script.
- Licenses: neolord0 hwplib / hwpxlib / hwp2hwpx are Apache-2.0 (used). pyhwpxlib is mostly BSL-1.1 and
  hwp2md is GPL-3.0 (both avoided).
- Encrypted / password-protected / DRM HWP files are not supported by hwplib and must fail loudly.

## Implemented

### Routing (`hancom_export.py INPUT --to FMT [-o OUT] [--force] [--engine auto|java|hancom]`)

- `auto` (default): `.hwp` → `.hwpx` goes to the Java engine. Every other conversion, and `.hwp` → `.hwpx`
  when the Java engine fails, goes to Hancom. Encrypted input never falls back (Hancom would hang on a
  password prompt).
- `--engine java` only accepts `.hwp` → `.hwpx`; `--engine hancom` forces the GUI.
- Lossy targets (`docx`, `odt`, `html`, `rtf`, `txt`) print a warning; Hancom's compatibility prompt is accepted.
- An existing output is refused unless `--force`; the output may never be the input file.

Exit codes: 0 ok, 10 encrypted, 11 bad input, 16 output exists, 20 environment, 21 Hancom cleanup
failed, 22 Hancom busy, 23 Hancom unavailable (no Accessibility grant, foreign documents open, GUI
timeout), 24 Java conversion failed. Every nonzero exit prints one line
`hwpkit: <CODE>: <file>: <reason>` to stderr.

### Java engine

- `java/build.sh` (run by `make install`) fetches upstream `neolord0/hwp2hwpx` at the exact commit in
  `java/UPSTREAM_SHA` (sparse, shallow), reads the hwplib / hwpxlib versions from that commit's `pom.xml`,
  downloads both JARs from Maven Central and verifies their `.sha1`, fails if either gains runtime
  dependencies, compiles everything with plain `javac --release 17` (upstream's pom targets Java 7, which
  current JDKs reject), and writes a fat JAR to `~/.local/share/hwpkit/hwp2hwpx-all.jar`
  (`HWPKIT_JAR` overrides). The upstream license is copied into the JAR's `META-INF`.
- The build smoke-converts upstream's own `test/table/from.hwp` and checks `--version` reports the pinned
  SHA before replacing the installed JAR.
- `kr.hwpkit.Convert` checks the file header before the full parse: HWP 3.x and ZIP input → unsupported
  (12), bad OLE / header → corrupt (11), password or DRM → encrypted (10). Output is written to a temp
  file and moved into place atomically.
- `engine_java.py` finds the JDK from `JAVA_HOME` or the Homebrew keg (never `/usr/bin/java`), runs the
  JAR with startup-friendly JVM flags and a 120 s timeout, and replaces the destination atomically.
- Pinned upstream: `62d95b980a` or later. That commit re-applies the fixes from upstream PR #18 (memo
  bodies, numbering, border/fill references, compatibility / pagination). Moving the pin requires
  re-running the corpus check.

### Hancom automation rules

- Refuses to run while Hancom has any document open that hwpkit did not open (Hancom's own empty
  "untitled" window is ignored).
- Works on a temp copy named `hwpkit-<random>.<ext>`; never opens the user's file. Leftover `hwpkit-*`
  windows from a crashed run are closed without saving.
- AppleScript values are passed as argv, never interpolated into script source.
- Re-activates Hancom and targets windows by exact title before each action; closes only its own windows;
  on failure cancels its own dialogs and closes its window without saving.
- One run at a time: `~/.cache/hwpkit/hancom.lock` holds PID + heartbeat; a lock is reclaimed only when
  the PID is dead or the heartbeat is older than 120 s; otherwise wait up to 300 s, then exit 22.
- Detects a missing Accessibility grant (osascript -1719 / -25211) and prints where to grant it.
- Menu labels are the Korean UI labels recorded in [hwpkit-baseline.md](hwpkit-baseline.md); other
  locales are not supported yet.

### Comparer (`hwpx_compare.py A.hwpx B.hwpx [--json]`)

Compares body text, tables (shape, merged cells, cell text), images (binary hashes), footnotes / endnotes,
memos, headers / footers, equations, text in shapes / captions and resolved numbering. Ignores layout
caches, IDs and style ID numbers. Exit 0 equivalent, 3 differences, 11 unreadable.

## Planned v1: verified edit loop

### Job directory (no in-place mutation)

```
<name>.hwpkit/
  00-original.hwp        # byte copy of the input
  01-converted.hwpx      # convert output, immutable
  02-edit-001.hwpx       # each edit writes the next snapshot
  edits.jsonl            # one line per op: seq, op, target, payload, before_sha, after_sha
  meta.json              # engine, upstream SHA, library versions, input sha256
  report.md / report.json
```

`rollback --to N` copies snapshot N (0 = `01-converted.hwpx`) to a new head and appends a manifest line
with its `after_sha`; ops after N stop counting as declared targets. Per-job lock; `edit` / `rollback` /
`export` first check that the head's sha256 equals the last manifest `after_sha` (else
`UNTRACKED_MODIFICATION`); snapshots are written to a temp name, fsynced, renamed, then the manifest line
is appended.

### Edit ops (v1)

Targets use positional container paths: `s0/p12` (section 0, paragraph 12), `s0/t2/r1c3/p1` (paragraph in
a table cell, merged cells addressed by their top-left), nested tables, `hd`/`ft` (header/footer), `m`
(memo), `fn` (footnote).

- `set-text <para-path> <text>` and `set-cell <cell-path> <text>` (cell with exactly one paragraph).
- A paragraph with inline controls (footnote / memo anchors, fields, images) or mixed-format runs is refused
  (`INLINE_CONTENT`) unless `--text-only`, which rewrites only the text nodes in place; a paragraph with
  several text nodes then needs exactly one replacement per node.
- Safe-edit rules: keep style ID references pointing at existing `header.xml` entries (add, never mutate
  shared ones), strip stale `linesegarray` from edited paragraphs, keep `mimetype` first and stored
  uncompressed, update the `content.hpf` manifest for added binaries.

### Verification model

- **Inventory**: one Java extractor (hwplib for `.hwp`, hwpxlib for `.hwpx`) with one normalizer emits
  `{"objects":[{key, type, fields}]}`: paragraph / cell text, list level and resolved numbering (format,
  start, level, restart; never IDs), table rows / cols / merged spans, image hash and size, memo / note
  bodies and anchors, header / footer text, equations, shapes. Normalization: NFC, collapsed whitespace,
  ±1 HWPUNIT tolerance. Each corpus file gets a hand-audited `expected-inventory.json` so a mismatch is
  attributed to the extractor or to Hancom.
- **Stage 1, conversion damage** (original vs converted): inventory diff must be empty; Hancom render must
  have equal page count and every page under the cross-format threshold.
- **Stage 2, intended edits** (converted vs head): every changed object must be a declared target, and each
  target's text must equal the recorded payload (`EDIT_NOT_APPLIED` otherwise). Pages before the first
  edited page must render under the same-format threshold; later pages are advisory and are saved as
  before / after PNG pairs that Claude must look at before calling the edit verified.
- **Stage 3, export check** (after `export --to hwp`): inventory diff of the exported `.hwp` vs head must
  be empty; page count must match; pages under the cross-format threshold.
- **Render diff**: 100 dpi RGB, page pairs streamed one at a time, stop at the first gating failure,
  500-page cap.
- **Thresholds** (`make calibrate`, rerun after Hancom updates): same-format = max diff of rendering the
  same file twice × 1.5. Cross-format = 95th percentile over audited hwp / Hancom-saved-hwpx pairs × 1.5,
  capped at 2 % of page pixels; fewer than 3 surviving pairs or a value over the cap means
  "uncalibrated". Stored with the Hancom version they were measured on.
- **Exit codes** (planned additions): 2 verified-partial (only from `verify`: content verified, layout
  not), 3 damage or out-of-scope change, 13 ambiguous target, 15 inline content; `doctor` 0 ok /
  30 degraded / 20 environment. Without Hancom, verify reports `RENDER NOT VERIFIED` and never exits 0.

### Planned CLI

`convert`, `inspect`, `diff`, `edit`, `rollback`, `render`, `export --to hwp`, `verify`, `doctor`,
plus a Claude skill that runs `verify` before declaring any edit done.

## Key decisions

1. Deliverable: a Claude skill backed by a macOS CLI, not an app.
2. Default converter: headless Java (hwp2hwpx at a pinned commit). Hancom is used for rendering, for
   non-HWPX targets, and as fallback. Rejected "Hancom for every conversion": GUI-bound, serial, fragile
   across Hancom updates.
3. "Verified" means a content inventory diff **and** a Hancom page-render diff, reported separately for
   conversion damage and intended edits. "Opens in Hancom" is necessary, not sufficient.
4. Prove the Accessibility grant from the real caller (Claude Code) before building GUI automation.
5. Build with `javac --release 17`, ignoring upstream's Java 7 target; smoke-test the JAR at install.
6. One Java inventory extractor for both formats instead of Java + Python twins that could drift.
7. Exit codes each mean one thing (no shared "partial" code for "no file produced").
8. v1 edit ops are `set-text` / `set-cell` only. Paragraph insert / delete, character formatting and the
   keymap that tracks shifted paths are deferred (see `TODOS.md`).
9. Tests: pytest only; the Java extractor is tested through golden JSON.
10. Test documents: only synthetic fixtures are committed; real documents stay outside the repo.

## Approaches considered

- **A, headless core**: CLI + pinned JAR + inventory diff, no Hancom. Built first (milestone 1).
- **B, verified edit loop (chosen)**: A plus Hancom render / export, edit helpers, page diff, two-stage
  report, rollback.
- **C, Hancom-first**: rejected (see decision 2).

## Open questions

1. Hancom's own `.hwp` → `.hwpx` Save As is not assumed lossless; calibration audits each pair.
2. PDF text search may split Korean text across lines, making "first edited page" detection miss; search
   a short normalized prefix and measure the hit rate.
3. Menu labels for non-Korean Hancom locales.

## Success criteria

- On a 5-document corpus (merged tables, images, memo, numbered lists, long multi-section): report the
  Java engine's zero-damage count.
- One real edit per document passes `verify` with only the declared targets changed.
- `export --to hwp` passes stage 3.
- Fresh-machine setup: `brew install openjdk`, `make install`, one Accessibility grant.

## Distribution

No binaries are committed. `make install` builds the converter JAR locally from upstream source and
Maven Central artifacts (all Apache-2.0); the upstream commit and library versions are recorded in the
JAR manifest.
