"""Host-side harness for building synthesized TraceFix apps inside CityOS.

TraceFix verification runs outside CityOS. After synthesis places generated apps
under ``<cityos_root>/apps``, this harness runs Docker build commands with the
working directory set to the CityOS repository root.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_APP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")


@dataclass(frozen=True)
class CityOSDockerApp:
    name: str
    kind: str
    agent: str | None
    path: Path


@dataclass(frozen=True)
class CityOSDockerRun:
    app: CityOSDockerApp
    command: list[str]
    cwd: Path
    returncode: int | None
    status: str
    stdout: str
    stderr: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": {
                "name": self.app.name,
                "kind": self.app.kind,
                "agent": self.app.agent,
                "path": str(self.app.path),
            },
            "command": self.command,
            "commandText": command_text(self.command),
            "cwd": str(self.cwd),
            "returncode": self.returncode,
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
        }


def default_cityos_root() -> Path | None:
    configured = os.environ.get("CITYOS_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    for candidate in (
        Path.home() / "TraceFix-CityOS" / "GitHub" / "cityos",
        Path.home() / "cityos",
    ):
        if candidate.exists():
            return candidate.resolve()
    return None


def command_text(command: list[str]) -> str:
    parts = []
    for item in command:
        text = str(item)
        if re.match(r"^[A-Za-z0-9_./:\\=-]+$", text):
            parts.append(text)
        else:
            parts.append('"' + text.replace('"', '\\"') + '"')
    return " ".join(parts)


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid synthesis manifest: {manifest_path}")
    return data


def manifest_apps(manifest: dict[str, Any]) -> list[CityOSDockerApp]:
    apps = []
    for item in manifest.get("apps") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        apps.append(CityOSDockerApp(
            name=name,
            kind=str(item.get("kind") or "app"),
            agent=str(item.get("agent") or "") or None,
            path=Path(str(item.get("path") or "")),
        ))
    return apps


def validate_cityos_root(cityos_root: Path) -> Path:
    root = cityos_root.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"CityOS root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"CityOS root is not a directory: {root}")
    apps_dir = root / "apps"
    if not apps_dir.is_dir():
        raise FileNotFoundError(f"CityOS root is missing an apps/ directory: {apps_dir}")
    return root


def docker_build_command(app_name: str) -> list[str]:
    if not _APP_NAME_RE.match(app_name):
        raise ValueError(f"unsafe CityOS app name: {app_name!r}")
    return [
        "docker",
        "build",
        "-f",
        f"apps/{app_name}/Dockerfile",
        "-t",
        f"cityos-{app_name}:latest",
        ".",
    ]


def run_cityos_docker_builds(
    *,
    manifest_path: Path,
    cityos_root: Path | None = None,
    timeout_seconds: int = 1800,
    dry_run: bool = False,
) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"Synthesis manifest does not exist: {manifest_path}")
    resolved_root = cityos_root or default_cityos_root()
    if resolved_root is None:
        raise FileNotFoundError("CityOS root was not found. Set CITYOS_ROOT or pass cityos_root.")
    root = validate_cityos_root(resolved_root)
    manifest = load_manifest(manifest_path)
    apps = manifest_apps(manifest)
    if not apps:
        raise ValueError(f"No apps found in synthesis manifest: {manifest_path}")

    started_at = datetime.now(timezone.utc).isoformat()
    runs: list[CityOSDockerRun] = []
    for app in apps:
        app_dir = root / "apps" / app.name
        dockerfile = app_dir / "Dockerfile"
        command = docker_build_command(app.name)
        if not dockerfile.exists():
            runs.append(CityOSDockerRun(
                app=app,
                command=command,
                cwd=root,
                returncode=None,
                status="missing_dockerfile",
                stdout="",
                stderr="",
                error=f"Expected Dockerfile at {dockerfile}. Regenerate artifacts into {root / 'apps'}.",
            ))
            continue
        if dry_run:
            runs.append(CityOSDockerRun(
                app=app,
                command=command,
                cwd=root,
                returncode=0,
                status="dry_run",
                stdout="",
                stderr="",
            ))
            continue
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            runs.append(CityOSDockerRun(
                app=app,
                command=command,
                cwd=root,
                returncode=completed.returncode,
                status="completed" if completed.returncode == 0 else "failed",
                stdout=completed.stdout,
                stderr=completed.stderr,
            ))
        except subprocess.TimeoutExpired as exc:
            runs.append(CityOSDockerRun(
                app=app,
                command=command,
                cwd=root,
                returncode=None,
                status="timeout",
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                error=f"Timed out after {timeout_seconds} seconds",
            ))
        except OSError as exc:
            runs.append(CityOSDockerRun(
                app=app,
                command=command,
                cwd=root,
                returncode=None,
                status="error",
                stdout="",
                stderr="",
                error=f"{type(exc).__name__}: {exc}",
            ))

    finished_at = datetime.now(timezone.utc).isoformat()
    result = {
        "ok": bool(runs) and all(run.status in {"completed", "dry_run"} for run in runs),
        "cityosRoot": str(root),
        "manifestPath": str(manifest_path),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "dryRun": dry_run,
        "runs": [run.to_dict() for run in runs],
    }
    out_path = manifest_path.with_name(manifest_path.stem + "-cityos-docker-build.json")
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["resultPath"] = str(out_path)
    return result