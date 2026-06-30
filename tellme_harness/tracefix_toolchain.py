"""Deterministic detection of the TraceFix verification toolchain.

Distinguishes *verification* availability (Java 11+ and ``tla2tools.jar`` — enough
to run generate → translate → TLC) from *state-extraction* availability (which
additionally needs ``tree-sitter`` + ``tree-sitter-tlaplus``). No shell injection
(argument lists only), bounded subprocess timeouts, sanitized output, and never a
false positive from a file merely existing — Java is actually invoked to read its
version.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from .schemas import TraceFixToolchainStatus

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Same precedence TraceFix's own toolchain uses; kept here so detection does not
# require importing the (optionally-absent) tracefix package.
_HOMEBREW_JAVA = Path("/opt/homebrew/opt/openjdk@17/bin/java")
_MIN_JAVA_MAJOR = 11


def _find_tracefix_root() -> Optional[Path]:
    for name in ("TraceFixCityOS-main", "TraceFix-main"):
        candidate = _PROJECT_ROOT / name
        if (candidate / "tracefix").is_dir():
            return candidate
    return None


def _resolve_java() -> Optional[str]:
    import os
    import shutil

    env = os.environ.get("TLA_VERIFY_JAVA")
    if env and Path(env).exists():
        return env
    if _HOMEBREW_JAVA.exists():
        return str(_HOMEBREW_JAVA)
    java_home = os.environ.get("JAVA_HOME")
    if java_home and (Path(java_home) / "bin" / "java").exists():
        return str(Path(java_home) / "bin" / "java")
    on_path = shutil.which("java")
    return on_path


def _resolve_jar() -> Optional[str]:
    import os

    env = os.environ.get("TLA_VERIFY_JAR")
    if env and Path(env).exists():
        return env
    root = _find_tracefix_root()
    if root is not None:
        candidate = root / "lib" / "tla2tools.jar"
        if candidate.exists() and candidate.stat().st_size > 0:
            return str(candidate)
    return None


def _java_major_version(java_path: str) -> Tuple[Optional[str], Optional[int]]:
    """Return (raw_version, major) by actually invoking ``java -version``."""
    try:
        proc = subprocess.run(
            [java_path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    # `java -version` prints to stderr, e.g. `openjdk version "17.0.19" ...`.
    text = (proc.stderr or "") + (proc.stdout or "")
    match = re.search(r'version "([0-9][0-9_.]*)"', text)
    if not match:
        return None, None
    raw = match.group(1)
    parts = raw.split(".")
    try:
        major = int(parts[0])
        if major == 1 and len(parts) > 1:  # legacy "1.8.0" form
            major = int(parts[1])
    except ValueError:
        return raw, None
    return raw, major


def _module_available(module_name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def detect_tracefix_toolchain() -> TraceFixToolchainStatus:
    blockers: list[str] = []

    java_path = _resolve_java()
    java_found = java_path is not None
    java_version: Optional[str] = None
    java_major: Optional[int] = None
    if java_found:
        java_version, java_major = _java_major_version(java_path)  # type: ignore[arg-type]
        if java_version is None:
            blockers.append("java binary present but could not report its version")
    else:
        blockers.append("no java binary found (set TLA_VERIFY_JAVA or install openjdk@17)")

    java_compatible = bool(java_major is not None and java_major >= _MIN_JAVA_MAJOR)
    if java_found and java_major is not None and not java_compatible:
        blockers.append(f"java {java_major} is too old; TLC v1.8.0 needs Java {_MIN_JAVA_MAJOR}+")

    jar_path = _resolve_jar()
    jar_found = jar_path is not None
    if not jar_found:
        blockers.append("tla2tools.jar not found (run scripts/download_tla2tools.sh)")

    tree_sitter = _module_available("tree_sitter")
    tree_sitter_tlaplus = _module_available("tree_sitter_tlaplus")

    verification_available = java_compatible and jar_found
    state_extraction_available = verification_available and tree_sitter and tree_sitter_tlaplus
    if verification_available and not state_extraction_available:
        blockers.append("state extraction unavailable (tree-sitter / tree-sitter-tlaplus missing) — verification still works")

    return TraceFixToolchainStatus(
        java_found=java_found,
        java_path=java_path,
        java_version=java_version,
        java_compatible=java_compatible,
        tla2tools_found=jar_found,
        tla2tools_path=jar_path,
        tree_sitter_found=tree_sitter,
        tree_sitter_tlaplus_found=tree_sitter_tlaplus,
        verification_available=verification_available,
        state_extraction_available=state_extraction_available,
        blockers=blockers,
    )
