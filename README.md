# hwpkit

Convert HWP documents on macOS so Claude can edit them.

```bash
python3 hancom_export.py report.hwp --to hwpx            # headless Java engine, no GUI
python3 hancom_export.py report.hwp --to pdf             # Hancom Office GUI (brings Hancom forward ~4 s)
python3 hancom_export.py report.hwpx --to hwp --force    # GUI; --force replaces an existing output
```

| Conversion | Engine | Interrupts you |
|---|---|---|
| `.hwp` → `.hwpx` | hwp2hwpx (Java, pinned upstream) | no |
| anything → `.pdf`, `.hwp`, `.docx`, `.odt`, … | Hancom Office HWP for Mac via GUI scripting | yes, a few seconds |
| `.hwp` → `.hwpx` when Java fails | Hancom GUI fallback (notice on stderr) | yes |

`--engine java|hancom` forces one engine. Exit codes: 0 ok, 10 encrypted, 11 bad input,
16 output exists, 20 environment, 21 cleanup failed, 22 Hancom busy, 23 Hancom unavailable, 24 Java conversion failed.

## Setup

1. `brew install openjdk`, then `make install` (builds `~/.local/share/hwpkit/hwp2hwpx-all.jar`).
2. For the GUI engine: Hancom Office HWP for Mac, and Accessibility permission for the app that
   runs the script. From Claude Code that is the bundled `claude.app` under
   `~/Library/Application Support/Claude/claude-code/<version>/<hash>/` (see `docs/hwpkit-baseline.md`).
3. Close your own documents in Hancom before a GUI export; the script refuses to run otherwise.

## Tools

- `hwpx_compare.py A.hwpx B.hwpx` compares content (paragraphs, tables, images, notes, numbering).

Design and review: `docs/hwp-to-hwpx-converter.md`. Deferred work: `TODOS.md`.
