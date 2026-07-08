import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tracefix.runtime.web_data_harness import run_web_data_apps


class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"occupied": true, "room": "smart_room_1"}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _SmartroomHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/v1/recordings":
            payload = {
                "recordings": [{
                    "day": "day_08_2026-07-07",
                    "rec": "rec_20260707_001",
                    "mtime": 1783448292039.5,
                    "cameras": {
                        "cam2": {
                            "node": "smartroom2",
                            "durationSec": 30,
                            "models": {
                                "yolo26l": "done",
                                "action-hmdb": "done",
                                "yolo26n-pose": "analyzing",
                            },
                        },
                    },
                }],
            }
            self._json(payload)
            return
        if self.path == "/api/v1/recordings/day_08_2026-07-07/rec_20260707_001/cam2/inference/yolo26l":
            self._json({"detections": {"status": "done", "timeline": [{"t": 0.0, "count": 2}]}})
            return
        if self.path == "/api/v1/recordings/day_08_2026-07-07/rec_20260707_001/cam2/inference/action-hmdb":
            self._json({"detections": {"status": "done", "actions": ["pour"]}, "actions": {"tracks": {}}})
            return
        if self.path.startswith("/api/v1/recordings/day_08_2026-07-07/rec_20260707_001/cam2/frame?"):
            body = b"fake-jpeg"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"not found"}')

    def _json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _start_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/"


def _make_app(tmp_path, name, agent):
    app_dir = tmp_path / name
    bundle = app_dir / "tracefix_bundle"
    workspace = bundle / "workspace"
    workspace.mkdir(parents=True)
    (bundle / "plan.json").write_text(json.dumps({
        "version": "0.1",
        "application": {"name": "web_data_demo"},
    }), encoding="utf-8")
    (bundle / "agent.json").write_text(json.dumps({
        "name": agent,
    }), encoding="utf-8")
    return app_dir


def _make_manifest(tmp_path):
    writer_dir = _make_app(tmp_path, "tracefix-writer", "WRITER")
    monitor_dir = _make_app(tmp_path, "tracefix-monitor", "monitor")
    manifest = tmp_path / "demo-synthesis.json"
    manifest.write_text(json.dumps({
        "apps": [
            {"name": "tracefix-writer", "kind": "agent", "agent": "WRITER", "path": str(writer_dir)},
            {"name": "tracefix-monitor", "kind": "monitor", "agent": None, "path": str(monitor_dir)},
        ],
    }), encoding="utf-8")
    return manifest


def test_web_data_harness_feeds_synthesized_apps_from_http_source(tmp_path):
    manifest = _make_manifest(tmp_path)
    server, url = _start_server(_JsonHandler)
    try:
        result = run_web_data_apps(
            manifest_path=manifest,
            source_url=url,
            source_mode="raw",
            output_root=tmp_path / "web-data-run",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["ok"] is True
    assert result["sourceUrl"] == url
    assert result["sourceKind"] == "http"
    assert result["payload"]["contentType"] == "application/json"
    assert result["payload"]["sizeBytes"] > 0
    assert len(result["runs"]) == 2

    for run in result["runs"]:
        assert run["status"] == "completed"
        assert run["handlerConfigured"] is False
        ready = json.loads(open(run["readyPath"], encoding="utf-8").read())
        assert ready["runtime_mode"] == "web_data"
        assert ready["handler_configured"] is False
        assert run["frameRecords"]
        record = json.loads(open(run["frameRecords"][0], encoding="utf-8").read())
        assert record["stream"] == "web_data"
        assert record["input_exists"] is True
        assert record["input_size_bytes"] == result["payload"]["sizeBytes"]


def test_web_data_harness_collects_smartroom_control_snapshot(tmp_path):
    manifest = _make_manifest(tmp_path)
    server, url = _start_server(_SmartroomHandler)
    try:
        result = run_web_data_apps(
            manifest_path=manifest,
            source_url=url,
            source_mode="auto",
            output_root=tmp_path / "smartroom-run",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["ok"] is True
    assert result["sourceKind"] == "smartroom-control"
    assert result["payload"]["snapshotSummary"]["selectedRecording"] == "rec_20260707_001"
    assert result["payload"]["snapshotSummary"]["cameras"] == ["cam2"]
    assert result["answer"] is not None
    assert "actions pour" in result["answer"]["text"]
    assert result["answer"]["chatAnswer"] == result["answer"]["chat_answer"]
    assert "cam 2 peaked at 2 people" in result["answer"]["chatAnswer"]
    assert "peak occupancy was 2 people overall" in result["answer"]["chatAnswer"]
    assert result["answerPath"]

    snapshot = json.loads(open(result["payload"]["payloadPath"], encoding="utf-8").read())
    cam2 = snapshot["selected"]["cameras"]["cam2"]
    assert set(cam2["inference"].keys()) == {"action-hmdb", "yolo26l"}
    assert cam2["inference"]["action-hmdb"]["data"]["detections"]["actions"] == ["pour"]
    assert cam2["frame"]["localPath"].endswith(".jpg")

    for run in result["runs"]:
        assert run["status"] == "completed"
        assert run["frameRecords"]
        assert run["answerPath"]