#!/usr/bin/env python3
"""Export HWP/HWPX documents through Hancom Office HWP for Mac (GUI scripting).

    hancom_export.py INPUT --to hwpx|hwp|pdf|docx|odt [-o OUTPUT] [--force]

Hancom for Mac has no AppleScript dictionary, so this drives its menus through
System Events. The responsible app for the Accessibility grant is the process
that runs this script (from Claude Code: the bundled com.anthropic.claude-code).

Safety rules (docs/hwp-to-hwpx-converter.md, "Hancom automation rules"):
- Refuse to run while Hancom has any document open that this script did not open.
- Work on a temp copy named hwpkit-<token>.<ext>; never open the user's file itself.
- Re-activate Hancom and target windows by exact title before every action.
- Close only our own window; on any failure, cancel our dialogs and close our
  window without saving.
- One run at a time (lock with PID + heartbeat).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = "Hancom Office HWP"
LOCK = Path.home() / ".cache" / "hwpkit" / "hancom.lock"
STEP_TIMEOUT = 30.0
LOCK_WAIT = 300.0
HEARTBEAT_STALE = 120.0

# Labels recorded from Hancom Office HWP 12.30 (Korean UI), see docs/hwpkit-baseline.md.
MENU_FILE = "파일"
MENU_WINDOW = "창"
ITEM_SAVE_AS = "다른 이름으로 저장하기..."
ITEM_SAVE_PDF = "PDF로 저장하기..."
ITEM_CLOSE = "문서 닫기"
DLG_SAVE_AS = "다른 이름으로 저장하기"
DLG_SAVE_PDF = "PDF로 저장하기"
BTN_SAVE = "저장"
BTN_CANCEL = "취소"
BTN_DONT_SAVE = "저장 안 함"
FIELD_NAME = "별도 저장:"
DLG_COMPAT = "호환 문서"  # "saving may change the layout; continue?" for non-Hancom formats
BTN_CONTINUE = "계속"

# Formats that cannot hold every HWP feature; Hancom asks before saving them.
LOSSY = {"docx", "odt", "html", "rtf", "txt"}

# Save As format menu items, matched by their "(*.ext)" suffix.
FORMATS = {"hwpx", "hwp", "docx", "odt", "html", "rtf", "txt"}

EXIT = {
    "ok": 0,
    "encrypted": 10,
    "bad_input": 11,
    "output_exists": 16,
    "env": 20,
    "cleanup_failed": 21,
    "busy": 22,
    "hancom_unavailable": 23,
    "java_failed": 24,
}


class HwpkitError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------- osascript

def osa(script: str, *args: str, timeout: float = STEP_TIMEOUT) -> str:
    """Run AppleScript with values passed as argv (never interpolated into source)."""
    try:
        r = subprocess.run(
            ["osascript", "-", *args], input=script, capture_output=True,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise HwpkitError("hancom_unavailable", f"GUI step timed out after {timeout:.0f}s") from e
    if r.returncode != 0:
        err = r.stderr.strip()
        if "-1719" in err or "-25211" in err:
            raise HwpkitError(
                "hancom_unavailable",
                "Accessibility not granted to the calling app. System Settings > Privacy & "
                "Security > Accessibility: add the app running this script (from Claude Code: "
                "~/Library/Application Support/Claude/claude-code/<version>/<hash>/claude.app).",
            )
        raise HwpkitError("hancom_unavailable", f"osascript failed: {err}")
    return r.stdout.strip()


PRELUDE = f'''
on activateHancom()
  tell application "{APP}" to activate
  -- Switching Spaces takes a moment; windows are invisible to System Events until it finishes.
  set deadline to (current date) + 5
  repeat
    tell application "System Events" to tell process "{APP}"
      if frontmost and (count of windows) > 0 then exit repeat
    end tell
    if (current date) > deadline then exit repeat
    delay 0.2
  end repeat
  delay 0.2
end activateHancom
on waitGone(t, secs)
  set deadline to (current date) + secs
  repeat
    tell application "System Events" to tell process "{APP}"
      if not ((name of every window) contains t) then return true
    end tell
    if (current date) > deadline then error "timeout waiting for " & t & " to close"
    delay 0.25
  end repeat
end waitGone
on waitWindow(t, secs)
  set deadline to (current date) + secs
  repeat
    tell application "System Events" to tell process "{APP}"
      if (name of every window) contains t then return true
    end tell
    if (current date) > deadline then error "timeout waiting for window " & t
    delay 0.25
  end repeat
end waitWindow
'''


def open_documents() -> list[str]:
    """Documents Hancom has open, read from the Window menu (sees every Space)."""
    out = osa(f'''
tell application "System Events"
  if not (exists process "{APP}") then return ""
  tell process "{APP}"
    set items_ to name of every menu item of menu 1 of menu bar item "{MENU_WINDOW}" of menu bar 1
  end tell
end tell
set AppleScript's text item delimiters to linefeed
return items_ as text
''')
    lines = out.split("\n")
    # Documents follow the last separator ("missing value") in the Window menu.
    if "missing value" not in lines:
        return []
    last_sep = len(lines) - 1 - lines[::-1].index("missing value")
    return [l for l in lines[last_sep + 1:] if l]


# ---------------------------------------------------------------- lock

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Lock:
    def __enter__(self):
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + LOCK_WAIT
        while True:
            try:
                fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                self.beat()
                return self
            except FileExistsError:
                try:
                    held = json.loads(LOCK.read_text())
                except (OSError, ValueError):
                    held = {}
                stale = (not _alive(int(held.get("pid", 0)))
                         or time.time() - float(held.get("heartbeat_at", 0)) > HEARTBEAT_STALE)
                if stale:
                    LOCK.unlink(missing_ok=True)
                    continue
                if time.time() > deadline:
                    raise HwpkitError("busy", f"Hancom is busy (lock held by pid {held.get('pid')})")
                time.sleep(1)

    def beat(self):
        now = time.time()
        LOCK.write_text(json.dumps({"pid": os.getpid(), "heartbeat_at": now}))

    def __exit__(self, *exc):
        LOCK.unlink(missing_ok=True)


# ---------------------------------------------------------------- GUI steps

UNTITLED = re.compile(r"^빈 문서 \d+$")  # Hancom's own empty document on launch


def hancom_pid() -> int | None:
    r = subprocess.run(["pgrep", "-f", f"/Applications/{APP}.app/Contents/MacOS/{APP}"],
                       capture_output=True, text=True)
    pids = r.stdout.split()
    return int(pids[0]) if pids else None


def ensure_running() -> int:
    """Launch Hancom bare (no file) and wait until its menus answer.

    Hancom 12.30 can segfault in -[NSApplication _doOpenFile:] (wcscmp) when a file
    arrives while it is still launching, so never launch it with the file.
    """
    pid = hancom_pid()
    if pid:
        return pid
    subprocess.run(["open", "-g", "-a", APP], check=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        pid = hancom_pid()
        if pid:
            try:
                open_documents()
                time.sleep(1.0)  # let launch-time work (blank document, recent list) settle
                return pid
            except HwpkitError:
                pass
        time.sleep(0.5)
    raise HwpkitError("hancom_unavailable", "Hancom did not finish launching within 30s")


def gui_open(path: Path, title: str) -> None:
    for attempt in (1, 2):
        pid = ensure_running()
        subprocess.run(["open", "-a", APP, str(path)], check=True)
        try:
            osa(PRELUDE + '''
on run argv
  activateHancom()
  waitWindow(item 1 of argv, 20)
end run
''', title)
            return
        except HwpkitError:
            if hancom_pid() == pid:
                raise
            # Hancom died while opening (known 12.30 crash); relaunch once and retry.
            if attempt == 2:
                raise HwpkitError("hancom_unavailable",
                                  "Hancom crashed while opening the file (twice); see "
                                  "~/Library/Logs/DiagnosticReports/Hancom Office HWP-*.ips")


def gui_export(title: str, fmt: str) -> None:
    if fmt == "pdf":
        osa(PRELUDE + f'''
on run argv
  set t to item 1 of argv
  activateHancom()
  tell application "System Events" to tell process "{APP}"
    perform action "AXRaise" of window t
    click menu item "{ITEM_SAVE_PDF}" of menu 1 of menu bar item "{MENU_FILE}" of menu bar 1
  end tell
  waitWindow("{DLG_SAVE_PDF}", 15)
  tell application "System Events" to tell process "{APP}"
    click button "{BTN_SAVE}" of UI element 1 of window "{DLG_SAVE_PDF}"
  end tell
end run
''', title)
        return
    osa(PRELUDE + f'''
on run argv
  set t to item 1 of argv
  set suffix to "(*." & item 2 of argv & ")"
  activateHancom()
  tell application "System Events" to tell process "{APP}"
    perform action "AXRaise" of window t
    click menu item "{ITEM_SAVE_AS}" of menu 1 of menu bar item "{MENU_FILE}" of menu bar 1
  end tell
  waitWindow("{DLG_SAVE_AS}", 15)
  tell application "System Events" to tell process "{APP}"
    set g to UI element 1 of window "{DLG_SAVE_AS}"
    set fmtButton to pop up button 2 of g
    click fmtButton
    delay 0.4
    set picked to false
    repeat with mi in menu items of menu 1 of fmtButton
      if (name of mi as text) ends with suffix then
        click mi
        set picked to true
        exit repeat
      end if
    end repeat
    if not picked then
      key code 53
      error "format not offered: " & suffix
    end if
    -- Hancom rewrites the filename extension asynchronously after the format change.
    set wanted to "." & item 2 of argv
    set deadline to (current date) + 5
    repeat until (value of text field "{FIELD_NAME}" of g as text) ends with wanted
      if (current date) > deadline then error "filename never switched to " & wanted
      delay 0.2
    end repeat
    click button "{BTN_SAVE}" of g
    delay 0.5
    if exists window "{DLG_SAVE_AS}" then
      if (count of sheets of window "{DLG_SAVE_AS}") > 0 then
        error "Save As is blocked by a prompt (file exists?)"
      end if
    end if
  end tell
  waitGone("{DLG_SAVE_AS}", 15)
  if item 3 of argv is "lossy" then
    -- Hancom warns that the layout may change; the caller asked for this format.
    set deadline to (current date) + 5
    repeat
      tell application "System Events" to tell process "{APP}"
        if exists window "{DLG_COMPAT}" then
          click button "{BTN_CONTINUE}" of window "{DLG_COMPAT}"
          exit repeat
        end if
      end tell
      if (current date) > deadline then exit repeat
      delay 0.25
    end repeat
  end if
end run
''', title, fmt, "lossy" if fmt in LOSSY else "exact")


def gui_close(titles: list[str]) -> None:
    """Close our document windows only; answer 'Don't Save' only for them."""
    osa(PRELUDE + f'''
on run argv
  activateHancom()
  tell application "System Events" to tell process "{APP}"
    if exists window "{DLG_COMPAT}" then
      click button "{BTN_CANCEL}" of window "{DLG_COMPAT}"
      delay 0.4
    end if
    repeat with w in (every window)
      set n to name of w as text
      if n is "{DLG_SAVE_AS}" or n is "{DLG_SAVE_PDF}" then
        -- A prompt sheet on our dialog (e.g. "replace?") must be cancelled first.
        try
          click button "{BTN_CANCEL}" of sheet 1 of w
          delay 0.3
        end try
        try
          click button "{BTN_CANCEL}" of UI element 1 of w
        end try
      end if
    end repeat
  end tell
  set leftover to {{}}
  repeat with t in argv
    set t to t as text
    set present to false
    tell application "System Events" to tell process "{APP}"
      set present to (name of every window) contains t
      if present then
        perform action "AXRaise" of window t
        click menu item "{ITEM_CLOSE}" of menu 1 of menu bar item "{MENU_FILE}" of menu bar 1
        delay 0.6
        try
          click button "{BTN_DONT_SAVE}" of sheet 1 of window t
        end try
      end if
    end tell
    if present then
      try
        waitGone(t, 5)
      on error
        set end of leftover to t
      end try
    end if
  end repeat
  set AppleScript's text item delimiters to linefeed
  return leftover as text
end run
''', *titles)
    still_open = [t for t in open_documents() if t in titles]
    if still_open:
        raise HwpkitError("cleanup_failed", f"still open in Hancom: {still_open}")


def wait_for_file(path: Path, timeout: float = STEP_TIMEOUT) -> None:
    """Wait until the file exists and its size stops changing."""
    deadline = time.time() + timeout
    last = -1
    while time.time() < deadline:
        if path.exists():
            size = path.stat().st_size
            if size > 0 and size == last:
                return
            last = size
        time.sleep(0.4)
    raise HwpkitError("hancom_unavailable", f"Hancom did not write {path.name} within {timeout:.0f}s")


# ---------------------------------------------------------------- policy

def resolve_output_path(dest: Path, force: bool) -> Path:
    """Decide where the export lands when `dest` may already exist.

    Called once, before Hancom is touched. Return the path to write, or raise
    HwpkitError("output_exists", ...) to refuse.
    """
    if dest.is_dir():
        raise HwpkitError("output_exists", f"output is a directory: {dest}")
    if dest.exists() and not force:
        raise HwpkitError("output_exists", f"{dest.name} already exists; pass --force to replace it")
    return dest


# ---------------------------------------------------------------- main

def java_route(src: Path, fmt: str, engine: str) -> bool:
    """True when the headless Java engine should do this conversion."""
    if engine == "hancom" or fmt != "hwpx" or src.suffix.lower() != ".hwp":
        if engine == "java" and not (fmt == "hwpx" and src.suffix.lower() == ".hwp"):
            raise HwpkitError("bad_input", "--engine java only converts .hwp to .hwpx")
        return False
    return True


def export_java(src: Path, out: Path) -> Path:
    try:
        import engine_java  # local module next to this script
    except ImportError as e:
        raise HwpkitError("env", "java engine not installed (engine_java.py)") from e
    try:
        engine_java.convert(src, out)
    except engine_java.JavaEngineError as e:
        code = {"encrypted": "encrypted", "bad_input": "bad_input", "env": "env"}.get(e.code, "java_failed")
        raise HwpkitError(code, f"java engine: {e}") from e
    return out


def export(src: Path, fmt: str, dest: Path, force: bool, engine: str = "auto") -> Path:
    if not src.is_file():
        raise HwpkitError("bad_input", f"no such file: {src}")
    if src.suffix.lower() not in {".hwp", ".hwpx"}:
        raise HwpkitError("bad_input", f"not an HWP/HWPX file: {src.name}")
    if dest == src:
        raise HwpkitError("output_exists", "output would replace the input file; choose another -o")
    if java_route(src, fmt, engine):
        out = resolve_output_path(dest, force)
        try:
            return export_java(src, out)
        except HwpkitError as e:
            # Encrypted files would hang Hancom on a password prompt; never fall back.
            if engine == "java" or e.code == "encrypted":
                raise
            print(f"hwpkit: java engine failed ({e}); falling back to Hancom GUI", file=sys.stderr)
    return export_hancom(src, fmt, dest, force)


def export_hancom(src: Path, fmt: str, dest: Path, force: bool) -> Path:
    if shutil.which("osascript") is None:
        raise HwpkitError("env", "osascript not found (macOS only)")
    if not Path(f"/Applications/{APP}.app").exists():
        raise HwpkitError("env", f"{APP} is not installed")

    out = resolve_output_path(dest, force)

    with Lock() as lock, tempfile.TemporaryDirectory(prefix="hwpkit-") as tmp:
        leftovers = [d for d in open_documents() if d.startswith("hwpkit-")]
        if leftovers:  # windows from an earlier crashed run; they hold only temp copies
            gui_close(leftovers)
        foreign = [d for d in open_documents()
                   if not d.startswith("hwpkit-") and not UNTITLED.match(d)]
        if foreign:
            raise HwpkitError(
                "hancom_unavailable",
                f"Hancom has {len(foreign)} open document(s) this tool did not open; "
                "save and close them first.",
            )
        stem = f"hwpkit-{secrets.token_hex(4)}"
        work = Path(tmp) / f"{stem}{src.suffix.lower()}"
        shutil.copy2(src, work)
        produced = work.with_suffix(f".{fmt}")
        # Save As retitles the window to the new name, so both titles are ours to close.
        opened = [work.name] if fmt == "pdf" else [work.name, produced.name]
        try:
            gui_open(work, work.name)
            lock.beat()
            gui_export(work.name, fmt)
            wait_for_file(produced)
            lock.beat()
        finally:
            try:
                gui_close(opened)
            except HwpkitError as e:
                raise HwpkitError("cleanup_failed", f"could not close {opened}: {e}") from e
        out.parent.mkdir(parents=True, exist_ok=True)
        os.replace(produced, out) if produced.stat().st_dev == out.parent.stat().st_dev \
            else shutil.move(str(produced), out)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("input", type=Path)
    p.add_argument("--to", required=True, choices=sorted(FORMATS | {"pdf"}))
    p.add_argument("-o", "--output", type=Path)
    p.add_argument("--force", action="store_true", help="overwrite an existing output")
    p.add_argument("--engine", choices=["auto", "java", "hancom"], default="auto",
                   help="auto: headless Java for .hwp->.hwpx, Hancom GUI for the rest or on failure")
    a = p.parse_args(argv)
    dest = a.output or a.input.with_suffix(f".{a.to}")
    if a.to in LOSSY:
        print(f"hwpkit: warning: .{a.to} cannot hold every HWP feature; layout may differ "
              "(Hancom's 호환 문서 prompt was accepted).", file=sys.stderr)
    try:
        out = export(a.input.resolve(), a.to, dest.resolve(), a.force, a.engine)
    except HwpkitError as e:
        print(f"hwpkit: {e.code.upper()}: {a.input}: {e}", file=sys.stderr)
        return EXIT[e.code]
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
