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

## Install as a Claude Code skill

This repo is a Claude Code plugin and its own marketplace. In Claude Code:

```text
/plugin marketplace add <git-url-or-owner/repo>    # or a local path: /plugin marketplace add ./hwpkit
/plugin install hwpkit@hwpkit-marketplace
```

Then ask Claude things like "convert this .hwp to hwpx" or "export report.hwp to PDF". The skill
(`skills/hwpkit/SKILL.md`) runs a setup check and offers the one-time build below.

To have teammates get it for a project, run once in that project and commit `.claude/settings.json`:

```bash
claude plugin marketplace add <git-url-or-owner/repo> --scope project
```

Each teammate still runs `/plugin install hwpkit@hwpkit-marketplace` once.

## Setup

Requirements: macOS, Python 3. The Java engine needs Homebrew OpenJDK; the GUI engine needs
Hancom Office HWP for Mac (tested with 12.30, Korean UI).

1. `brew install openjdk`, then `make install` (builds `~/.local/share/hwpkit/hwp2hwpx-all.jar`).
2. For the GUI engine: Hancom Office HWP for Mac, and Accessibility permission for the app that
   runs the script. From Claude Code that is the bundled `claude.app` under
   `~/Library/Application Support/Claude/claude-code/<version>/<hash>/` (see `docs/hwpkit-baseline.md`).
3. Close your own documents in Hancom before a GUI export; the script refuses to run otherwise.

## Tools

- `hwpx_compare.py A.hwpx B.hwpx` compares content (paragraphs, tables, images, notes, numbering).

Design and review: `docs/hwp-to-hwpx-converter.md`. Deferred work: `TODOS.md`.
