"""Cross-platform resolution of the Java 17 + tla2tools.jar toolchain.

Centralizes how TraceFix finds the external verification toolchain so the CLI,
the PlusCal compiler, the TLC runner, and `tla-verify-pluscal doctor` all agree.

Java resolution priority (first hit wins):
  1. explicit argument (e.g. ``--java-path``)
  2. ``TLA_VERIFY_JAVA`` environment variable
  3. ``JAVA_EXE`` environment variable
  4. ``$JAVA_HOME/bin/java`` or ``%JAVA_HOME%\bin\java.exe``
  5. ``java`` on ``PATH``

JAR resolution priority: explicit arg → ``TLA_VERIFY_JAR`` env → bundled
``<repo>/lib/tla2tools.jar``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists())


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _configured_java_env() -> str | None:
    env = os.environ.get("TLA_VERIFY_JAVA")
    if env:
        return env
    return os.environ.get("JAVA_EXE")


def resolve_java(explicit: str | None = None) -> str:
    """Resolve a Java binary path (see module docstring for priority)."""
    configured = _configured_java_env()
    if explicit:
        if configured and not _same_path(explicit, configured):
            raise RuntimeError(
                "Java selection conflict: --java-path resolved to "
                f"{explicit!r}, but TLA_VERIFY_JAVA/JAVA_EXE is configured as "
                f"{configured!r}. Refusing to silently use the wrong Java."
            )
        return explicit
    if configured:
        return configured
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        bin_dir = Path(java_home) / "bin"
        candidates = [bin_dir / "java.exe", bin_dir / "java"] if sys.platform == "win32" else [bin_dir / "java", bin_dir / "java.exe"]
        for cand in candidates:
            if cand.exists():
                return str(cand)
    on_path = shutil.which("java")
    if on_path:
        return on_path
    return "java"


def resolve_jar(explicit: str | None = None) -> str:
    """Resolve the tla2tools.jar path (explicit → env → bundled lib/)."""
    if explicit:
        return explicit
    env = os.environ.get("TLA_VERIFY_JAR")
    if env:
        return env
    return str(_repo_root() / "lib" / "tla2tools.jar")


def java_major_version(java_path: str) -> str | None:
    """Return the Java major version (e.g. ``"17"``) or None if java can't run.

    Handles both the modern scheme (``17.0.x`` → ``"17"``) and the legacy
    ``1.x`` scheme (``1.8.0`` → ``"8"``).
    """
    try:
        proc = subprocess.run(
            [java_path, "-version"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stderr or "") + (proc.stdout or "")
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
    if not m:
        return None
    major, minor = m.group(1), m.group(2)
    if major == "1" and minor:  # legacy 1.8 → 8
        return minor
    return major


def java_version_output(java_path: str) -> str:
    """Return combined ``java -version`` output, or a clear error string."""
    try:
        proc = subprocess.run(
            [java_path, "-version"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return f"<unable to run {java_path!r} -version: {exc}>"
    return ((proc.stderr or "") + (proc.stdout or "")).strip()


def command_display(command: list[str]) -> str:
    """Render a subprocess command for logs without invoking a shell."""
    try:
        return subprocess.list2cmdline(command) if sys.platform == "win32" else " ".join(command)
    except Exception:
        return repr(command)


def tla_tool_log(phase: str, java_path: str, tla2tools_jar: str, command: list[str]) -> str:
    """Build a diagnostic block for PlusCal/TLC invocations."""
    version = java_version_output(java_path)
    return "\n".join([
        f"[tracefix toolchain] phase: {phase}",
        f"[tracefix toolchain] Java executable selected: {java_path}",
        "[tracefix toolchain] java -version:",
        version,
        f"[tracefix toolchain] tla2tools.jar: {tla2tools_jar}",
        f"[tracefix toolchain] command: {command_display(command)}",
    ])


JAVA_MISSING_HINT = (
    "Install Java 17, then set TLA_VERIFY_JAVA=/path/to/java, JAVA_EXE=/path/to/java, "
    "JAVA_HOME to the JDK root, or pass --java-path."
)
JAR_MISSING_HINT = (
    "Run `bash scripts/download_tla2tools.sh` to fetch tla2tools.jar v1.8.0, "
    "or set TLA_VERIFY_JAR=/path/to/tla2tools.jar or pass --jar-path."
)
