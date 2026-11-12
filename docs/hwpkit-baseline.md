# hwpkit baseline (Assignment)

Feeds `capabilities.json`, the GUI automation script, and the corpus. Design: [hwp-to-hwpx-converter.md](hwp-to-hwpx-converter.md).

## Step 0: Accessibility probe (R1 / T1)

Probe run 2026-11-12 from a Claude Code session in the Claude desktop app:

```
osascript -e 'tell application "System Events" to get name of every menu bar item of menu bar 1 of process "Hancom Office HWP"'
→ execution error: System Events got an error: osascript is not allowed assistive access. (-1719)
```

Process chain: `zsh` ← `~/Library/Application Support/Claude/claude-code/2.1.321/ddbdf2b334e1/claude.app` ← `Claude.app/Contents/Helpers/disclaimer` ← `Claude.app`.

The `disclaimer` helper usually makes its child (the bundled `claude.app`) the process macOS holds responsible. If so, the Accessibility grant belongs to that bundled app, whose path contains a version number and may need re-granting after each Claude Code update. Unverified: confirm with the steps below.

**Confirmed 2026-11-12 from the tccd log** (`/usr/bin/log show --predicate 'process == "tccd"'`; note that zsh shadows `log` with a builtin):
`static code for: identifier com.anthropic.claude-code ... at ~/Library/Application Support/Claude/claude-code/2.1.321/ddbdf2b334e1/claude.app` → `AUTHREQ_RESULT: authValue=0` (denied). The responsible identity is the bundled `com.anthropic.claude-code`, not `Claude.app`. Granting Claude.app alone does not help.

To grant: System Settings → Privacy & Security → Accessibility → `+` → Cmd+Shift+G → paste `~/Library/Application Support/Claude/claude-code/2.1.321/ddbdf2b334e1/claude.app` → Open → toggle on. TCC keys grants by bundle identifier and code-signing requirement, so the grant should survive updates that keep the same signing identity even though the path changes (verify after the next update).

- [x] Entry macOS checks: `com.anthropic.claude-code` (bundled claude.app)
- [x] Granted the bundled claude.app; probe passes (rc=0), 2026-11-12.
- Menu bar (Korean locale): `Apple, 한글, 파일, 편집, 보기, 입력 , 서식, 쪽, 보안, 도구, 표, 창, 도움말` (note the trailing space in `입력 `).
- 파일 menu (read-only listing, nothing clicked):
  - `불러오기...` (open), `저장하기`, `다른 이름으로 저장하기...` (Save As; HWPX/HWP choice is inside its dialog, not yet seen)
  - `PDF로 저장하기...`: a dedicated PDF export exists, so the Print → PDF fallback is not needed
  - `문서 닫기` (close document), plus `블록 저장...`, `편집 용지...`, `인쇄...`, `문서 정보…` (Unicode ellipsis, unlike the others' `...`), `CCL 넣기...`, `최근 사용 문서 ▸`
- Indirect evidence for HWPX Save As: the recent-documents list contains `.hwpx` files. Confirm in the Save As dialog during Step 2.
- [ ] After the next Claude Code update, re-run the probe. Still works? yes / no

## Step 1: Corpus (5 files)

| # | File | Feature it covers | Pages |
|---|------|-------------------|-------|
| 1 | 2-016.한국어대학강의데이터_데이터설명서.hwp | long, multi-section | |
| 2 | | merged-cell tables | |
| 3 | | images | |
| 4 | | memo / footnote | |
| 5 | | numbered lists (1. → 가. → 1)) | |

## Step 2: Manual runs in Hancom (per file)

Write the menu path exactly as shown, including locale (Korean or English).

| Action | Menu path (exact labels) | Dialog sequence (buttons pressed) | Seconds | Works? |
|---|---|---|---|---|
| Open | | | | |
| Save As .hwpx | | | | |
| Save As .hwp | | | | |
| Export PDF (or Print → PDF) | | | | |
| Close without saving | | | | |

Notes on differences between files (dialogs that only appear sometimes, warnings about compatibility, font substitution prompts):

-

## Step 3: Expected inventory (R12 / T4)

For each file, while it's open in Hancom, count what's really there. This becomes `expected-inventory.json`.

| # | Paragraphs (approx.) | Tables (merged cells?) | Images | Memos | Footnotes | Numbering styles used |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |

## Java engine render check (2026-11-12)

Java-made `.hwpx` of the 31-page report opened in Hancom 12.30 without errors and exported to PDF via
`hancom_export.py --to pdf`. Compared with the user's manual Hancom PDF of the same document:

- Pages 31 = 31; extracted text identical on all 31 pages (PDFKit).
- Pixels (100 dpi): 27 pages identical. Page 1: 13.8 %, page 7: 10.5 % (both a uniform 1.0 pt vertical
  shift of the whole page, plus residual line-edge noise), page 3: 1.6 %, page 5: 0.4 % (sub-point table
  border positions, no shift). Side-by-side renders show no visible content, table or highlight change.
- Likely cause: the converter writes slightly different page/table geometry on the two cover-style pages
  (1, 7). Not investigated further; worth a look if a form must align to a pre-printed page.
