# hwpkit baseline: Hancom for Mac findings and engine checks

Manual observations that the GUI automation and the planned verifier depend on.
Design: [hwp-to-hwpx-converter.md](hwp-to-hwpx-converter.md).

## Accessibility permission

Probe (from a Claude Code session in the Claude desktop app, before any grant):

```
osascript -e 'tell application "System Events" to get name of every menu bar item of menu bar 1 of process "Hancom Office HWP"'
→ execution error: System Events got an error: osascript is not allowed assistive access. (-1719)
```

Process chain: `zsh` ← bundled `claude.app` (under `~/Library/Application Support/Claude/claude-code/<version>/<hash>/`)
← `Claude.app/Contents/Helpers/disclaimer` ← `Claude.app`.

The `tccd` log (`/usr/bin/log show --predicate 'process == "tccd"'`; zsh shadows `log` with a builtin)
shows the request attributed to identifier `com.anthropic.claude-code` at that bundled `claude.app` path,
with `authValue=0` (denied). The responsible identity is the bundled `com.anthropic.claude-code`, not
`Claude.app`; granting `Claude.app` alone does not help.

To grant: System Settings → Privacy & Security → Accessibility → `+` → Cmd+Shift+G → paste the bundled
`claude.app` path → Open → toggle on. After the grant the probe passes (rc=0). TCC keys grants by bundle
identifier and code-signing requirement, so the grant should survive updates that keep the signing
identity even though the path changes; not yet re-verified after an update.

When running from Terminal or another app instead, grant that app.

## Hancom Office HWP 12.30 menus (Korean UI)

- Menu bar: `Apple, 한글, 파일, 편집, 보기, 입력 , 서식, 쪽, 보안, 도구, 표, 창, 도움말` (note the trailing
  space in `입력 `).
- 파일 menu: `불러오기...` (open), `저장하기`, `다른 이름으로 저장하기...` (Save As; the format choice is in
  its dialog), `PDF로 저장하기...` (dedicated PDF export, so no Print → PDF fallback is needed),
  `문서 닫기` (close), plus `블록 저장...`, `편집 용지...`, `인쇄...`, `문서 정보…` (Unicode ellipsis,
  unlike the others), `CCL 넣기...`, `최근 사용 문서 ▸`.
- Labels used by the script (`hancom_export.py`): Save As dialog `다른 이름으로 저장하기`, PDF dialog
  `PDF로 저장하기`, buttons `저장` / `취소` / `저장 안 함` / `계속`, file-name field `별도 저장:`,
  compatibility prompt `호환 문서`.

## Test corpus (kept outside the repository)

| # | Document | Feature it covers |
|---|---|---|
| 1 | a 4-page dataset description | long, multi-section |
| 2 | a 31-page government report form | tables, cover-style pages (used in the render check below) |
| 3 | (to pick) | images |
| 4 | (to pick) | memo / footnote |
| 5 | (to pick) | numbered lists (1. → 가. → 1)) |

Real documents are not committed. Committed fixtures, when added, must be synthetic documents authored
for the purpose.

Still to record per document: manual Hancom timings for Open / Save As `.hwpx` / Save As `.hwp` / PDF
export / close, dialogs that appear only sometimes, and a hand-audited expected inventory (paragraphs,
tables and merged cells, images, memos, footnotes, numbering styles).

## Java engine render check (2026-11-12)

The Java-made `.hwpx` of the 31-page report form opened in Hancom 12.30 without errors and exported to PDF
via `hancom_export.py --to pdf`. Compared with a PDF exported manually from Hancom for the original `.hwp`:

- Pages: 31 = 31. Extracted text identical on all 31 pages (PDFKit).
- Pixels (100 dpi): 27 pages identical. Page 1: 13.8 %, page 7: 10.5 % (both a uniform 1.0 pt vertical
  shift of the whole page, plus residual line-edge noise); page 3: 1.6 %, page 5: 0.4 % (sub-point table
  border positions, no shift). Side-by-side renders show no visible content, table or highlight change.
- Likely cause: the converter writes slightly different page / table geometry on the two cover-style pages
  (1, 7). Not investigated further; worth a look if a form must align to a pre-printed page.

Conversion time with the Java engine is about 0.1–0.2 s for a small document (measured on upstream's
`test/table/from.hwp`, including JVM start).
