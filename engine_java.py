"""Headless HWP -> HWPX conversion through the bundled Java engine.

Runs ``hwp2hwpx-all.jar`` (neolord0/hwp2hwpx + hwplib + hwpxlib, built by
``make -C hwpkit install``) with the Homebrew JDK. No GUI, no system ``java``
symlink required. Stdlib only.

    convert(src, dest)   -> None, raises JavaEngineError
    available()          -> bool
    version()            -> upstream hwp2hwpx commit SHA baked into the JAR
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import zipfile
from pathlib import Path

INSTALL_HINT = "run `make -C hwpkit install`"
DEFAULT_JAR = Path.home() / ".local/share/hwpkit/hwp2hwpx-all.jar"
_BREW_JDK_SUFFIX = Path("libexec/openjdk.jdk/Contents/Home")
_BREW_PREFIXES = (Path("/opt/homebrew/opt/openjdk"), Path("/usr/local/opt/openjdk"))
# Startup-friendly flags: conversions are short-lived, single-document runs.
_JVM_FLAGS = ("-Djava.awt.headless=true", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1", "-Xss16m")

# Exit codes of kr.hwpkit.Convert -> JavaEngineError.code
_EXIT_CODES = {10: "encrypted", 11: "bad_input", 12: "bad_input"}


class JavaEngineError(Exception):
    """code is one of: "env", "encrypted", "bad_input", "failed"."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def jar_path() -> Path:
    return Path(os.environ.get("HWPKIT_JAR") or DEFAULT_JAR).expanduser()


def _java_ok(home: Path) -> bool:
    return (home / "bin/java").is_file() and os.access(home / "bin/java", os.X_OK)


def java_home() -> Path | None:
    """JAVA_HOME if set and usable, else the Homebrew openjdk keg. Never /usr/bin/java."""
    env = os.environ.get("JAVA_HOME")
    if env and _java_ok(Path(env)):
        return Path(env)
    for prefix in _BREW_PREFIXES:
        if _java_ok(prefix / _BREW_JDK_SUFFIX):
            return prefix / _BREW_JDK_SUFFIX
    brew = shutil.which("brew")
    if brew:
        try:
            out = subprocess.run([brew, "--prefix", "openjdk"], capture_output=True, text=True,
                                 timeout=30, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        home = Path(out) / _BREW_JDK_SUFFIX
        if out and _java_ok(home):
            return home
    return None


def _require_env() -> tuple[Path, Path]:
    jar = jar_path()
    if not jar.is_file():
        raise JavaEngineError("env", f"converter JAR not found at {jar}; {INSTALL_HINT}")
    home = java_home()
    if home is None:
        raise JavaEngineError("env", f"no JDK found (JAVA_HOME or Homebrew openjdk); {INSTALL_HINT}")
    return home / "bin/java", jar


def available() -> bool:
    try:
        _require_env()
    except JavaEngineError:
        return False
    return True


def version() -> str:
    """Upstream hwp2hwpx SHA from the JAR manifest (no JVM start)."""
    jar = jar_path()
    try:
        with zipfile.ZipFile(jar) as zf:
            manifest = zf.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile) as e:
        raise JavaEngineError("env", f"cannot read {jar}: {e}; {INSTALL_HINT}") from e
    for line in manifest.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "Hwpkit-Upstream-Sha":
            return value.strip()
    raise JavaEngineError("env", f"{jar} has no Hwpkit-Upstream-Sha; {INSTALL_HINT}")


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def convert(src: Path, dest: Path, timeout: float = 120) -> None:
    """Convert src (.hwp) to dest (.hwpx). dest is replaced atomically or left untouched."""
    java, jar = _require_env()
    src, dest = Path(src).resolve(), Path(dest).absolute()
    if not src.is_file():
        raise JavaEngineError("bad_input", f"no such file: {src}")
    if not dest.parent.is_dir():
        raise JavaEngineError("failed", f"output directory does not exist: {dest.parent}")

    tmp = dest.with_name(f".{dest.stem}.hwpkit-{secrets.token_hex(4)}.hwpx")
    cmd = [str(java), *_JVM_FLAGS, "-jar", str(jar), str(src), str(tmp)]
    try:
        try:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise JavaEngineError("failed", f"java engine timed out after {timeout:g}s") from e
        except OSError as e:
            raise JavaEngineError("env", f"cannot run {java}: {e}; {INSTALL_HINT}") from e

        if proc.returncode != 0:
            raise JavaEngineError(_EXIT_CODES.get(proc.returncode, "failed"), _message(proc))
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise JavaEngineError("failed", "java engine exited 0 but wrote no output")
        os.replace(tmp, dest)
    finally:
        _remove(tmp)
        # Convert.java writes <tmp>.tmp-<pid> first; a killed JVM can leave it behind.
        for leftover in tmp.parent.glob(tmp.name + ".tmp-*"):
            _remove(leftover)


def _message(proc: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("hwpkit-java: "):
            return ln.removeprefix("hwpkit-java: ")
    tail = lines[-1] if lines else "no stderr"
    return f"{proc.returncode}: {tail}"
