import json

from tracefix.runtime.cityos_docker_harness import run_cityos_docker_builds


def test_cityos_docker_harness_builds_from_cityos_root_in_dry_run(tmp_path):
    cityos = tmp_path / "cityos"
    app_dir = cityos / "apps" / "demo-app"
    app_dir.mkdir(parents=True)
    (app_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    manifest = tmp_path / "demo-synthesis.json"
    manifest.write_text(json.dumps({
        "apps": [{
            "name": "demo-app",
            "kind": "agent",
            "agent": "WRITER",
            "path": str(app_dir),
        }],
    }), encoding="utf-8")

    result = run_cityos_docker_builds(
        manifest_path=manifest,
        cityos_root=cityos,
        dry_run=True,
    )

    assert result["ok"] is True
    run = result["runs"][0]
    assert run["cwd"] == str(cityos.resolve())
    assert run["command"] == [
        "docker",
        "build",
        "-f",
        "apps/demo-app/Dockerfile",
        "-t",
        "cityos-demo-app:latest",
        ".",
    ]
    assert run["status"] == "dry_run"