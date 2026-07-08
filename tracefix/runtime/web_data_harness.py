"""Host-side runner for synthesized TraceFix apps against a web data source.

This keeps the CityOS synthesis path intact while letting the same generated
app bundles consume data from a normal HTTP server when CityOS is not the
runtime environment. When the source is the smartroom-control LAN API, the
runner collects a structured snapshot from the API rather than just fetching
one raw URL.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import shlex
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse, urlunparse

from tracefix.runtime.cityos_agent_harness import CityOSAgentHarness, CityOSHarnessConfig
from tracefix.runtime.cityos_docker_harness import CityOSDockerApp, load_manifest, manifest_apps

_DEFAULT_SOURCE_URL = "http://feruzgay.local:4000/api/v1"
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_SMARTROOM_MODELS = ("action-hmdb", "action", "yolo26l", "yolo26n-pose")


def default_web_data_url() -> str:
    return _DEFAULT_SOURCE_URL


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value) or "item"


def _payload_extension(content_type: str, body: bytes) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in {"application/json", "application/ld+json"}:
        return ".json"
    if media_type.startswith("text/"):
        return ".txt"
    guessed = mimetypes.guess_extension(media_type) if media_type else None
    if guessed:
        return guessed
    stripped = body.lstrip()[:1]
    if stripped in {b"{", b"["}:
        return ".json"
    return ".bin"


def _read_url(
    url: str,
    *,
    timeout_seconds: int,
    max_bytes: int,
    accept: str = "application/json, text/plain, */*",
) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={
        "User-Agent": "TraceFix-WebDataHarness/0.2",
        "Accept": accept,
    })
    fetched_at = _utc_now().isoformat()
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - local user-provided data source
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"Web data response exceeded {max_bytes} bytes: {url}")
        return {
            "url": url,
            "status": getattr(response, "status", None),
            "reason": getattr(response, "reason", ""),
            "contentType": response.headers.get("content-type", ""),
            "headers": dict(response.headers.items()),
            "body": body,
            "fetchedAt": fetched_at,
        }


def _read_json_url(url: str, *, timeout_seconds: int, max_bytes: int) -> tuple[dict[str, Any], dict[str, Any]]:
    response = _read_url(
        url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        accept="application/json",
    )
    try:
        data = json.loads(response["body"].decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON from {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return data, response


def fetch_web_payload(
    source_url: str,
    *,
    timeout_seconds: int = 30,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    url = str(source_url or "").strip()
    if not url:
        raise ValueError("Web data URL is required.")
    payload = _read_url(url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
    payload["sourceKind"] = "http"
    return payload


def _looks_like_smartroom_url(source_url: str) -> bool:
    parsed = urlparse(str(source_url or ""))
    if "/api/v1" in parsed.path:
        return True
    try:
        return parsed.port == 4000
    except ValueError:
        return False


def _smartroom_base_url(source_url: str) -> str:
    url = str(source_url or "").strip() or _DEFAULT_SOURCE_URL
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid smartroom API URL: {source_url}")
    path = parsed.path.rstrip("/")
    marker = "/api/v1"
    if marker in path:
        path = path[:path.index(marker) + len(marker)]
    else:
        path = marker
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _api_url(base_url: str, *parts: str) -> str:
    quoted = "/".join(quote(str(part).strip("/"), safe="") for part in parts)
    return f"{base_url.rstrip('/')}/{quoted}"


def _frame_time(camera_info: dict[str, Any]) -> float:
    try:
        duration = float(camera_info.get("durationSec") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 1:
        return 0.0
    return min(5.0, max(0.0, duration / 2.0))


def _download_smartroom_frame(
    *,
    base_url: str,
    day: str,
    rec: str,
    camera: str,
    camera_info: dict[str, Any],
    frames_dir: Path,
    timeout_seconds: int,
    max_bytes: int,
) -> dict[str, Any]:
    t = _frame_time(camera_info)
    params = urlencode({"t": f"{t:.3f}", "w": 320, "q": 50, "video": "raw"})
    url = _api_url(base_url, "recordings", day, rec, camera, "frame") + f"?{params}"
    response = _read_url(
        url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        accept="image/jpeg,*/*",
    )
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_name = _safe_name(f"{day}_{rec}_{camera}_t{t:.1f}") + ".jpg"
    frame_path = frames_dir / frame_name
    frame_path.write_bytes(response["body"])
    return {
        "url": url,
        "localPath": str(frame_path),
        "contentType": response.get("contentType") or "image/jpeg",
        "sizeBytes": len(response["body"]),
        "t": t,
        "width": 320,
        "quality": 50,
        "fetchedAt": response.get("fetchedAt"),
    }


def fetch_smartroom_payload(
    source_url: str,
    *,
    output_root: Path,
    timeout_seconds: int = 30,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    base_url = _smartroom_base_url(source_url)
    errors: list[dict[str, str]] = []
    recordings_doc, recordings_response = _read_json_url(
        _api_url(base_url, "recordings"),
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    recordings = recordings_doc.get("recordings") if isinstance(recordings_doc.get("recordings"), list) else []
    selected = recordings[0] if recordings and isinstance(recordings[0], dict) else None
    snapshot: dict[str, Any] = {
        "kind": "smartroom-control.snapshot.v1",
        "sourceApi": base_url,
        "fetchedAt": _utc_now().isoformat(),
        "recordingCount": len(recordings),
        "recordings": recordings,
        "selected": None,
        "errors": errors,
    }

    if selected is not None:
        day = str(selected.get("day") or "")
        rec = str(selected.get("rec") or "")
        selected_payload: dict[str, Any] = {
            "day": day,
            "rec": rec,
            "mtime": selected.get("mtime"),
            "cameras": {},
        }
        cameras = selected.get("cameras") if isinstance(selected.get("cameras"), dict) else {}
        for camera, raw_camera_info in cameras.items():
            camera_name = str(camera)
            camera_info = raw_camera_info if isinstance(raw_camera_info, dict) else {}
            camera_payload: dict[str, Any] = {
                "metadata": camera_info,
                "inference": {},
                "frame": None,
            }
            models = camera_info.get("models") if isinstance(camera_info.get("models"), dict) else {}
            for model in _SMARTROOM_MODELS:
                if str(models.get(model) or "").lower() != "done":
                    continue
                inference_url = _api_url(base_url, "recordings", day, rec, camera_name, "inference", model)
                try:
                    inference, _response = _read_json_url(
                        inference_url,
                        timeout_seconds=timeout_seconds,
                        max_bytes=max_bytes,
                    )
                    camera_payload["inference"][model] = {
                        "url": inference_url,
                        "data": inference,
                    }
                except Exception as exc:  # noqa: BLE001 - keep partial snapshot useful
                    errors.append({
                        "url": inference_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
            try:
                camera_payload["frame"] = _download_smartroom_frame(
                    base_url=base_url,
                    day=day,
                    rec=rec,
                    camera=camera_name,
                    camera_info=camera_info,
                    frames_dir=output_root / "source_data" / "frames",
                    timeout_seconds=timeout_seconds,
                    max_bytes=max_bytes,
                )
            except Exception as exc:  # noqa: BLE001 - frames are helpful but not required
                frame_url = _api_url(base_url, "recordings", day, rec, camera_name, "frame")
                errors.append({
                    "url": frame_url,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            selected_payload["cameras"][camera_name] = camera_payload
        snapshot["selected"] = selected_payload

    answer = build_smartroom_answer(snapshot)
    snapshot["answer"] = answer
    body = json.dumps(snapshot, indent=2, default=str).encode("utf-8")
    if len(body) > max_bytes:
        raise ValueError(f"Smartroom snapshot exceeded {max_bytes} bytes.")
    return {
        "url": base_url,
        "status": recordings_response.get("status"),
        "reason": recordings_response.get("reason"),
        "contentType": "application/json",
        "headers": recordings_response.get("headers") or {},
        "body": body,
        "fetchedAt": snapshot["fetchedAt"],
        "sourceKind": "smartroom-control",
        "answer": answer,
        "snapshotSummary": {
            "recordingCount": len(recordings),
            "selectedDay": selected.get("day") if isinstance(selected, dict) else None,
            "selectedRecording": selected.get("rec") if isinstance(selected, dict) else None,
            "cameras": list((snapshot.get("selected") or {}).get("cameras", {}).keys()) if snapshot.get("selected") else [],
            "errors": len(errors),
        },
    }


def _extract_detection_summary(inference: dict[str, Any]) -> dict[str, Any]:
    detections = inference.get("detections") if isinstance(inference.get("detections"), dict) else {}
    timeline = detections.get("timeline") if isinstance(detections.get("timeline"), list) else []
    counts = []
    for point in timeline:
        if not isinstance(point, dict):
            continue
        try:
            counts.append(int(point.get("count") or 0))
        except (TypeError, ValueError):
            continue
    actions = detections.get("actions") if isinstance(detections.get("actions"), list) else []
    track_actions = detections.get("trackActions") if isinstance(detections.get("trackActions"), dict) else {}
    return {
        "status": detections.get("status"),
        "durationSec": detections.get("durationSec"),
        "peakPeople": max(counts) if counts else None,
        "lastPeople": counts[-1] if counts else None,
        "samples": len(counts),
        "tracks": detections.get("tracks"),
        "actions": [str(item) for item in actions if str(item).strip()],
        "trackActions": {str(key): str(value) for key, value in track_actions.items()},
        "jumps": detections.get("jumps"),
    }


def build_smartroom_answer(snapshot: dict[str, Any]) -> dict[str, Any]:
    selected = snapshot.get("selected") if isinstance(snapshot.get("selected"), dict) else None
    if selected is None:
        text = "No smartroom recordings were available from the API."
        return {
            "question": "What does the latest smartroom API data show?",
            "text": text,
            "recording": None,
            "cameras": [],
        }

    day = str(selected.get("day") or "unknown day")
    rec = str(selected.get("rec") or "unknown recording")
    camera_answers: list[dict[str, Any]] = []
    line_parts = [f"Latest recording {rec} from {day}."]
    cameras = selected.get("cameras") if isinstance(selected.get("cameras"), dict) else {}
    for camera_name, raw_camera in cameras.items():
        camera = raw_camera if isinstance(raw_camera, dict) else {}
        metadata = camera.get("metadata") if isinstance(camera.get("metadata"), dict) else {}
        inference = camera.get("inference") if isinstance(camera.get("inference"), dict) else {}
        model_summaries: dict[str, Any] = {}
        action_labels: set[str] = set()
        peak_people: int | None = None
        last_people: int | None = None
        track_count: int | None = None
        for model, wrapper in inference.items():
            data = wrapper.get("data") if isinstance(wrapper, dict) and isinstance(wrapper.get("data"), dict) else {}
            summary = _extract_detection_summary(data)
            model_summaries[str(model)] = summary
            if summary["peakPeople"] is not None:
                peak_people = max(peak_people or 0, int(summary["peakPeople"]))
            if summary["lastPeople"] is not None:
                last_people = int(summary["lastPeople"])
            if summary["tracks"] is not None:
                try:
                    track_count = max(track_count or 0, int(summary["tracks"]))
                except (TypeError, ValueError):
                    pass
            action_labels.update(summary["actions"])
            action_labels.update(value for value in summary["trackActions"].values() if value)
        frame = camera.get("frame") if isinstance(camera.get("frame"), dict) else None
        camera_answer = {
            "camera": str(camera_name),
            "node": metadata.get("node"),
            "durationSec": metadata.get("durationSec"),
            "peakPeople": peak_people,
            "lastPeople": last_people,
            "trackCount": track_count,
            "actions": sorted(action_labels),
            "models": model_summaries,
            "framePath": frame.get("localPath") if frame else None,
        }
        camera_answers.append(camera_answer)
        facts = [str(camera_name)]
        if peak_people is not None:
            facts.append(f"peak occupancy {peak_people}")
        if last_people is not None:
            facts.append(f"last observed occupancy {last_people}")
        if action_labels:
            facts.append("actions " + ", ".join(sorted(action_labels)))
        if track_count is not None:
            facts.append(f"tracks {track_count}")
        if frame and frame.get("localPath"):
            facts.append(f"sample frame {frame['localPath']}")
        line_parts.append("; ".join(facts) + ".")

    if not camera_answers:
        line_parts.append("No cameras were listed for the selected recording.")
    errors = snapshot.get("errors") if isinstance(snapshot.get("errors"), list) else []
    if errors:
        line_parts.append(f"Partial data: {len(errors)} API request(s) failed; see snapshot errors.")
    chat_answer = _build_smartroom_chat_answer(day=day, rec=rec, cameras=camera_answers, errors=errors)
    return {
        "question": "What does the latest smartroom API data show?",
        "text": " ".join(line_parts),
        "chatAnswer": chat_answer,
        "chat_answer": chat_answer,
        "recording": {"day": day, "rec": rec},
        "cameras": camera_answers,
        "errors": errors,
    }


def _build_smartroom_chat_answer(
    *,
    day: str | None,
    rec: str | None,
    cameras: list[dict[str, Any]],
    errors: list[Any],
) -> str:
    if not cameras:
        return "I could not find any camera results in the latest smartroom recording yet."
    camera_parts: list[str] = []
    latest_parts: list[str] = []
    peak_values: list[int] = []
    for camera in cameras:
        name = _format_smartroom_camera_label(camera.get("camera") or "camera")
        peak = camera.get("peakPeople")
        latest = camera.get("lastPeople")
        if peak is not None:
            try:
                peak_int = int(peak)
            except (TypeError, ValueError):
                peak_int = None
            if peak_int is not None:
                peak_values.append(peak_int)
                camera_parts.append(f"{name} peaked at {_person_count_label(peak_int)}")
        if latest is not None:
            latest_parts.append(f"{name} most recently showed {_person_count_label(latest)}")
    if not camera_parts:
        return "I found the latest smartroom recording, but it did not include enough occupancy data to summarize yet."

    recording = " / ".join(part for part in [day, rec] if part)
    prefix = f"For the latest recording ({recording}), " if recording else "For the latest recording, "
    overall_peak = max(peak_values) if peak_values else None
    summary = prefix + ", and ".join(camera_parts) + "."
    if overall_peak is not None:
        summary += f" Across the observed time window, the peak occupancy was {_person_count_label(overall_peak)} overall."
    if latest_parts:
        summary += " At the latest observed moment, " + ", and ".join(latest_parts) + "."
    if errors:
        summary += f" This answer is based on partial data because {len(errors)} API request(s) failed."
    return summary


def _person_count_label(value: Any) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return f"{value} people"
    noun = "person" if count == 1 else "people"
    return f"{count} {noun}"


def _format_smartroom_camera_label(value: Any) -> str:
    name = str(value or "camera")
    lowered = name.lower()
    suffix = name[3:]
    if lowered.startswith("cam") and suffix.isdigit():
        return f"cam {suffix}"
    return name


def _answer_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("sourceKind") != "smartroom-control":
        return None
    body = payload.get("body") or b""
    if not isinstance(body, bytes):
        return None
    try:
        snapshot = json.loads(body.decode("utf-8-sig"))
    except json.JSONDecodeError:
        return None
    if not isinstance(snapshot, dict):
        return None
    answer = snapshot.get("answer") if isinstance(snapshot.get("answer"), dict) else None
    return answer or build_smartroom_answer(snapshot)


def _write_answer_artifacts(output_root: Path, runs: list[dict[str, Any]], answer: dict[str, Any] | None) -> str | None:
    if not answer:
        return None
    answer_path = output_root / "smartroom-answer.json"
    answer_path.write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        if run.get("status") != "completed" or not run.get("outputDir"):
            continue
        app_answer_path = Path(str(run["outputDir"])) / "smartroom-answer.json"
        app_answer_path.write_text(json.dumps({
            "app": run.get("app"),
            "answer": answer,
        }, indent=2) + "\n", encoding="utf-8")
        run["answerPath"] = str(app_answer_path)
    return str(answer_path)
def fetch_source_payload(
    source_url: str,
    *,
    output_root: Path,
    source_mode: str = "auto",
    timeout_seconds: int = 30,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    mode = str(source_mode or "auto").strip().lower()
    if mode in {"smartroom", "smartroom-control", "smartroom_control"} or (
        mode == "auto" and _looks_like_smartroom_url(source_url)
    ):
        return fetch_smartroom_payload(
            source_url,
            output_root=output_root,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
    return fetch_web_payload(
        source_url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )


def write_web_payload(payload: dict[str, Any], output_root: Path) -> dict[str, Any]:
    source_dir = output_root / "source_data"
    source_dir.mkdir(parents=True, exist_ok=True)
    body = payload.get("body") or b""
    if not isinstance(body, bytes):
        raise TypeError("Fetched web payload body must be bytes.")
    stamp = _utc_now().strftime("%Y%m%d-%H%M%S-%f")
    extension = _payload_extension(str(payload.get("contentType") or ""), body)
    payload_path = source_dir / f"{stamp}_web_payload{extension}"
    metadata_path = source_dir / f"{stamp}_web_payload_metadata.json"
    payload_path.write_bytes(body)
    metadata = {
        "url": payload.get("url"),
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "contentType": payload.get("contentType"),
        "headers": payload.get("headers") or {},
        "sourceKind": payload.get("sourceKind") or "http",
        "snapshotSummary": payload.get("snapshotSummary"),
        "answer": payload.get("answer"),
        "payloadPath": str(payload_path),
        "sizeBytes": len(body),
        "fetchedAt": payload.get("fetchedAt"),
        "writtenAt": _utc_now().isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    metadata["metadataPath"] = str(metadata_path)
    return metadata


def _resolve_app_path(app: CityOSDockerApp, manifest_path: Path) -> Path:
    path = app.path.expanduser()
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def _handler_command(command: str | list[str] | None) -> list[str]:
    if command is None:
        return []
    if isinstance(command, list):
        return [str(item) for item in command if str(item).strip()]
    raw = str(command).strip()
    return shlex.split(raw) if raw else []


async def _run_app_against_payload(
    *,
    app: CityOSDockerApp,
    manifest_path: Path,
    payload_path: Path,
    output_root: Path,
    handler_command: str | list[str] | None,
    handler_timeout_seconds: int,
) -> dict[str, Any]:
    app_path = _resolve_app_path(app, manifest_path)
    bundle_dir = app_path / "tracefix_bundle"
    output_dir = output_root / "apps" / app.name
    frames_dir = output_dir / "frames"
    try:
        if not bundle_dir.is_dir():
            raise FileNotFoundError(f"Generated app bundle does not exist: {bundle_dir}")
        config = CityOSHarnessConfig(
            app_kind=app.kind or "app",
            agent_id=app.agent or app.name,
            bundle_dir=bundle_dir,
            runtime_mode="web_data",
            autorun=False,
            output_dir=output_dir,
            ready_dir=output_root / "ready",
            startup_cmd=[],
            handler_cmd=_handler_command(handler_command),
            handler_timeout=float(handler_timeout_seconds),
            verbose=False,
            task_id="",
        )
        harness = CityOSAgentHarness(config)
        existing_records = set()
        if frames_dir.exists():
            existing_records = {path.resolve() for path in frames_dir.glob("*.json")}
        ready_path = await harness.write_readiness()
        await harness.receive_frame("web_data", payload_path, _utc_now())
        frame_records = [
            str(path)
            for path in sorted(frames_dir.glob("*.json"))
            if path.resolve() not in existing_records and not path.name.endswith("_handler.json")
        ]
        handler_records = [
            str(path)
            for path in sorted(frames_dir.glob("*_handler.json"))
            if path.resolve() not in existing_records
        ]
        return {
            "app": {
                "name": app.name,
                "kind": app.kind,
                "agent": app.agent,
                "path": str(app_path),
            },
            "status": "completed",
            "outputDir": str(output_dir),
            "readyPath": str(ready_path),
            "framesDir": str(frames_dir),
            "frameRecords": frame_records,
            "handlerRecords": handler_records,
            "handlerConfigured": bool(config.handler_cmd),
        }
    except Exception as exc:  # noqa: BLE001 - result JSON should report each app failure
        return {
            "app": {
                "name": app.name,
                "kind": app.kind,
                "agent": app.agent,
                "path": str(app_path),
            },
            "status": "failed",
            "outputDir": str(output_dir),
            "framesDir": str(frames_dir),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _run_apps(
    *,
    apps: list[CityOSDockerApp],
    manifest_path: Path,
    payload_path: Path,
    output_root: Path,
    handler_command: str | list[str] | None,
    handler_timeout_seconds: int,
) -> list[dict[str, Any]]:
    return await asyncio.gather(*[
        _run_app_against_payload(
            app=app,
            manifest_path=manifest_path,
            payload_path=payload_path,
            output_root=output_root,
            handler_command=handler_command,
            handler_timeout_seconds=handler_timeout_seconds,
        )
        for app in apps
    ])


def run_web_data_apps(
    *,
    manifest_path: Path,
    source_url: str = _DEFAULT_SOURCE_URL,
    output_root: Path | None = None,
    source_mode: str = "auto",
    timeout_seconds: int = 30,
    handler_command: str | list[str] | None = None,
    handler_timeout_seconds: int = 60,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"Synthesis manifest does not exist: {manifest_path}")
    manifest = load_manifest(manifest_path)
    apps = manifest_apps(manifest)
    if not apps:
        raise ValueError(f"No apps found in synthesis manifest: {manifest_path}")
    output_root = (output_root or manifest_path.with_name(manifest_path.stem + "-web-data")).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    started_at = _utc_now().isoformat()
    payload = fetch_source_payload(
        source_url,
        output_root=output_root,
        source_mode=source_mode,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    payload_metadata = write_web_payload(payload, output_root)
    payload_path = Path(str(payload_metadata["payloadPath"]))
    runs = asyncio.run(_run_apps(
        apps=apps,
        manifest_path=manifest_path,
        payload_path=payload_path,
        output_root=output_root,
        handler_command=handler_command,
        handler_timeout_seconds=handler_timeout_seconds,
    ))
    answer = _answer_from_payload(payload)
    answer_path = _write_answer_artifacts(output_root, runs, answer)
    finished_at = _utc_now().isoformat()
    result = {
        "ok": bool(runs) and all(run.get("status") == "completed" for run in runs),
        "sourceUrl": source_url,
        "sourceKind": payload.get("sourceKind") or "http",
        "sourceMode": source_mode,
        "manifestPath": str(manifest_path),
        "outputRoot": str(output_root),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "payload": payload_metadata,
        "answer": answer,
        "answerPath": answer_path,
        "runs": runs,
    }
    result_path = output_root / "web-data-run.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["resultPath"] = str(result_path)
    return result