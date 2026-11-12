# TODOS

## hwpkit

### Synthetic test fixtures

**What:** Author 4 small fixtures in Hancom (merged-cell table, footnote + bold run, inline image,
numbered list), commit them with their Hancom-saved `.hwpx` twins under `tests/fixtures/`, and keep the
real corpus outside the repo (e.g. `~/.cache/hwpkit/corpus/`) for a local-only `make check`.

**Why:** Tests must run on any machine without committing real documents. No real `.hwp` has been
committed so far; keep it that way.

**Effort:** S (needs Hancom by hand). **Priority:** P1.

### v1 verified edit loop

**What:** Job directory, `inspect` / `diff` inventory extractor, `edit` (`set-text`, `set-cell`),
`rollback`, `render`, `export --to hwp`, `verify` (stages 1-3), `doctor`, Claude skill.

**Context:** docs/hwp-to-hwpx-converter.md, "Planned v1". **Priority:** P1.

### v2 structural edit ops: insert/delete paragraph, set-charpr, keymap

**What:** Add `insert-para-after`, `delete-para` (refuses paragraphs holding anchored content),
`set-charpr`, and a per-snapshot keymap so verification can align paths that shift after inserts and deletes.

**Why:** v1 ships only `set-text` / `set-cell`; Claude cannot add or remove paragraphs or change formatting.

**Cons:** Keymap and ordinal shifting are the riskiest logic in the design.

**Effort:** M. **Priority:** P2. **Depends on:** v1 `verify` passing on the corpus.

### Inventory-gated Hancom fallback

**What:** Today the Hancom fallback for `.hwp` → `.hwpx` triggers only when the Java engine errors.
Also fall back when the Java output's inventory diff against the original is non-empty.

**Why:** Self-heal documents the Java converter damages silently.

**Effort:** S-M. **Priority:** P3. **Depends on:** inventory extractor (v1).

### Re-check the Accessibility grant after a Claude Code update

**What:** Re-run the probe in docs/hwpkit-baseline.md after the bundled `claude.app` path changes.

**Priority:** P3.

## Completed

- Headless Java engine for `.hwp` → `.hwpx` (`make install`, `engine_java.py`), default route with
  Hancom GUI fallback.
- Hancom GUI export to pdf / hwp / docx / odt / … with lock, temp copies and foreign-document guard.
- `hwpx_compare.py` content comparer.
