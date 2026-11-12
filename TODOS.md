# TODOS

## hwpkit

### Replace committed real corpus before this repo is pushed anywhere

**What:** Swap the 5 committed real `.hwp` test documents for synthetic Hancom-authored fixtures, move the real corpus to `~/.cache/hwpkit/corpus/`, and purge it from git history.

**Why:** D10 (decision 2fbfa741) committed private documents as fixtures. A push would publish them, and history keeps them after deletion.

**Pros:** Removes the only privacy risk in the test setup; tests become runnable on any machine.

**Cons:** ~1h authoring 4 synthetic fixtures in Hancom plus a history rewrite (`git filter-repo`).

**Context:** Decided in /plan-eng-review 2026-11-12, docs/hwp-to-hwpx-converter.md ledger R7. Trigger: before any `git remote add` or push.

**Effort:** S (human: ~2h / CC: ~15 min plus manual fixture authoring)
**Priority:** P1
**Depends on:** nothing

### v2 structural edit ops: insert/delete paragraph, set-charpr, keymap

**What:** Add `insert-para-after`, `delete-para` (refuses anchored content, exit 14), `set-charpr`, and the per-snapshot keymap so stage 2 can align shifted paths.

**Why:** v1 (D1) ships only `set-text`/`set-cell`; Claude cannot add or remove paragraphs or change formatting.

**Pros:** Design already reviewed (doc "Edit ops and addressing", "Keymap", R3-1..R3-3 fixes).

**Cons:** Keymap and ordinal shifting are the riskiest logic in the design (human ~3 days / CC ~1.5h).

**Context:** docs/hwp-to-hwpx-converter.md, Recommended Approach sections and Eng Review D1.

**Effort:** M
**Priority:** P2
**Depends on:** v1 `verify` passing on the corpus.

### v2 Hancom conversion engine (`--engine hancom|auto`)

**What:** Use Hancom Save As `.hwpx` as a fallback converter when the Java engine's stage 1 inventory diff is non-empty.

**Why:** Self-heal documents the Java converter damages.

**Pros:** Design exists (doc "Hancom capability fallback").

**Cons:** Second GUI-driven conversion path to maintain.

**Context:** Deferred by D2. Trigger: v1 `make check` reports any Java conversion loss on the corpus.

**Effort:** S-M (human ~1 day / CC ~30 min)
**Priority:** P3
**Depends on:** R1 Accessibility probe passing; v1 corpus loss count.

## Completed
