---
name: hwpkit
description: Convert Hangul (HWP/HWPX) documents on macOS. Use when the user wants to turn a .hwp file into .hwpx (editable OWPML XML), export .hwp/.hwpx to PDF, .docx, .odt or back to .hwp, or compare two .hwpx files for lost content. Also use whenever a task needs to read, review or edit the contents of a .hwp file, even if conversion is not mentioned — convert it to .hwpx first (headless, the original is kept) and work on the XML. Triggers include "hwp", "hwpx", "한글 파일", "한글 문서 변환", "PDF로 저장", "hwp to pdf".
---

# hwpkit

Converts HWP documents with two engines:

| Conversion | Engine | Effect on the user's screen |
|---|---|---|
| `.hwp` → `.hwpx` | headless Java (hwp2hwpx), about 0.2 s | none |
| anything → `.pdf`, `.hwp`, `.docx`, `.odt`, `.html`, `.rtf`, `.txt` | Hancom Office HWP for Mac, driven by GUI scripting | Hancom comes to the front for about 4-7 s |
| `.hwp` → `.hwpx` when Java fails | Hancom GUI fallback | same as above |

Scripts live in the plugin root, `${CLAUDE_PLUGIN_ROOT}`. Always quote paths: Korean
file names often contain spaces and parentheses.

## Setup check (once per session, before the first conversion)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/engine_java.py" --check
```

- Exit 0: Java engine ready.
- Otherwise: tell the user it needs a one-time build, then with their OK run
  `brew install openjdk` (if missing) and `make -C "${CLAUDE_PLUGIN_ROOT}" install` (about 3 s after downloads).

GUI exports additionally need Hancom Office HWP for Mac and the macOS Accessibility permission
for the app running Claude. Do not try to change that permission yourself; if a GUI export
exits 23 with "Accessibility not granted", give the user the message's System Settings path.

## Convert

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hancom_export.py" "<input>" --to <hwpx|pdf|hwp|docx|odt|html|rtf|txt> [-o "<output>"] [--force]
```

- Output defaults to the input path with the new extension. The script never overwrites:
  pass `--force` only when the user asked to replace an existing file.
- `--engine java|hancom` forces an engine; the default `auto` is right almost always.
- Before a GUI export, tell the user Hancom will take over the screen briefly, and that their
  own Hancom documents must be saved and closed (the script refuses otherwise, exit 23).
- Converting several files to PDF: run them back to back in one go so the interruption is a
  single burst, not spread out.

## Exit codes and what to tell the user

| Code | Meaning | Action |
|---|---|---|
| 0 | done; stdout is the output path | report the path |
| 10 | encrypted / password-protected | cannot convert; ask the user to remove the password in Hancom |
| 11 | corrupt or not an HWP file | report; do not retry |
| 12 | unsupported (e.g. HWP 3.0) | suggest opening and re-saving it in Hancom once |
| 16 | output exists | ask before re-running with `--force` |
| 20 | environment (JDK/JAR/Hancom missing) | run the setup check |
| 21 | a Hancom window could not be closed | tell the user which window is left open |
| 22 | Hancom busy with another hwpkit run | wait and retry once |
| 23 | Hancom unavailable (user documents open, permission missing, GUI timeout) | relay the message; nothing was written |
| 24 | Java conversion failed | `auto` already fell back to Hancom; if `--engine java` was forced, retry with `auto` |

## Checking a conversion

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hwpx_compare.py" "<a.hwpx>" "<b.hwpx>"
```

Compares body text, tables (size, merged cells, cell text), images, foot/endnotes, memos,
headers/footers and numbering. Exit 0 equivalent, 3 differences listed, 11 unreadable. Use it
when the user asks whether a conversion lost anything (for example, Java output vs a .hwpx
saved by Hancom).

## Editing a document

Convert to `.hwpx` first; it is a zip of XML (`Contents/section*.xml`, `Contents/header.xml`).
When editing text, change only `hp:t` text nodes, keep every `hp:run`, its `charPrIDRef` and any
inline controls, keep `mimetype` as the first, uncompressed zip entry, and remove the
`hp:linesegarray` of each paragraph you changed so Hancom re-lays it out. Afterwards run
`hwpx_compare.py` against the pre-edit file and confirm only the intended paragraphs changed.
