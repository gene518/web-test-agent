from __future__ import annotations

from pathlib import Path

import pytest
from starlette.exceptions import HTTPException
from starlette.testclient import TestClient

from deep_agent.http_artifacts import ArtifactResolver, build_artifact_http_app


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "test_case" / "demo").mkdir(parents=True)
    return project


def test_text_preview_is_escaped_and_download_is_attachment(tmp_path: Path) -> None:
    project = _project(tmp_path)
    artifact = project / "test_case" / "demo" / "aaa_demo.md"
    artifact.write_text("<script>alert('no')</script>\n测试完成", encoding="utf-8")
    client = TestClient(build_artifact_http_app(tmp_path))

    preview = client.get(
        "/artifacts/preview", params={"path": "project/test_case/demo/aaa_demo.md"}
    )
    relative_preview = client.get(
        "/artifacts/preview",
        params={"path": "test_case/demo/aaa_demo.md", "base_dir": str(project)},
    )
    download = client.get(
        "/artifacts/download", params={"path": "project/test_case/demo/aaa_demo.md"}
    )

    assert preview.status_code == 200
    assert relative_preview.status_code == 200
    assert "<script>alert" not in preview.text
    assert "&lt;script&gt;alert" in preview.text
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert download.status_code == 200
    assert download.content == artifact.read_bytes()
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["content-type"].startswith("application/octet-stream")


def test_directory_preview_exposes_only_public_artifact_trees(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "test-results" / "run" / "artifacts").mkdir(parents=True)
    (project / "test_case" / "demo" / "a_login.spec.ts").write_text(
        "test('login', () => {})", encoding="utf-8"
    )
    (project / "test-results" / "run" / "artifacts" / "test-failed-1.png").write_bytes(
        b"image"
    )
    (project / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (project / "package.json").write_text('{"private": true}', encoding="utf-8")
    (project / "service-account.json").write_text("{}", encoding="utf-8")
    client = TestClient(build_artifact_http_app(tmp_path))

    response = client.get("/artifacts/preview", params={"path": "project"})

    assert response.status_code == 200
    assert "test_case" in response.text
    assert "test-results" in response.text
    assert ".env" not in response.text
    assert "package.json" not in response.text
    assert "service-account.json" not in response.text


def test_positive_policy_rejects_project_secrets_and_arbitrary_files(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "id_rsa").write_text("private key", encoding="utf-8")
    (project / "service-account.json").write_text("{}", encoding="utf-8")
    (project / "access-token.txt").write_text("secret", encoding="utf-8")
    (project / "task-healer.md").write_text("policy", encoding="utf-8")
    (project / "test_case" / "demo" / "token.txt").write_text(
        "secret", encoding="utf-8"
    )
    output_dir = project / "test-results" / "run" / "artifacts"
    output_dir.mkdir(parents=True)
    (output_dir / "id_rsa").write_text("private key", encoding="utf-8")
    (output_dir / "service-account.json").write_text("{}", encoding="utf-8")
    (output_dir / "access-token.txt").write_text("secret", encoding="utf-8")
    (project / "test_case" / "demo" / "a_login.spec.ts").write_text(
        "test('login', () => {})", encoding="utf-8"
    )
    (project / "test_case" / "demo" / "aaa_demo.md").write_text(
        "# plan", encoding="utf-8"
    )
    client = TestClient(build_artifact_http_app(tmp_path))

    for path in (
        "project/id_rsa",
        "project/service-account.json",
        "project/access-token.txt",
        "project/task-healer.md",
        "project/test_case/demo/token.txt",
        "project/test-results/run/artifacts/id_rsa",
        "project/test-results/run/artifacts/service-account.json",
        "project/test-results/run/artifacts/access-token.txt",
    ):
        assert client.get("/artifacts/preview", params={"path": path}).status_code == 404
        assert client.get("/artifacts/download", params={"path": path}).status_code == 404

    assert client.get(
        "/artifacts/preview", params={"path": "project/test_case/demo/a_login.spec.ts"}
    ).status_code == 200
    assert client.get(
        "/artifacts/preview", params={"path": "project/test_case/demo/aaa_demo.md"}
    ).status_code == 200


def test_scheduler_reports_and_playwright_artifacts_remain_available(tmp_path: Path) -> None:
    project = _project(tmp_path)
    scheduler_reports = project / "test_case" / "scheduler-reports" / "daily-1234"
    scheduler_reports.mkdir(parents=True)
    report = scheduler_reports / "latest.json"
    report.write_text('{"status": "passed"}', encoding="utf-8")
    log = project / "test_case" / "scheduler-service.log"
    log.write_text("completed", encoding="utf-8")
    image = project / "test-results" / "run" / "artifacts" / "test-failed-1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"not-a-real-image")
    client = TestClient(build_artifact_http_app(tmp_path))

    assert client.get(
        "/artifacts/preview",
        params={"path": "project/test_case/scheduler-reports/daily-1234/latest.json"},
    ).status_code == 200
    assert client.get(
        "/artifacts/download",
        params={"path": "project/test_case/scheduler-service.log"},
    ).status_code == 200
    raw = client.get(
        "/artifacts/raw",
        params={"path": "project/test-results/run/artifacts/test-failed-1.png"},
    )
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/png")


def test_traversal_and_symlink_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project = root / "project"
    safe_dir = project / "test_case" / "demo"
    safe_dir.mkdir(parents=True)
    outside = tmp_path / "outside.spec.ts"
    outside.write_text("secret", encoding="utf-8")
    (safe_dir / "escape.spec.ts").symlink_to(outside)
    client = TestClient(build_artifact_http_app(root))

    for path in ("../outside.spec.ts", "project/test_case/demo/escape.spec.ts"):
        response = client.get("/artifacts/preview", params={"path": path})
        assert response.status_code == 404


def test_file_response_revalidates_and_refuses_a_path_replaced_by_symlink(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    artifact = project / "test_case" / "demo" / "a_login.spec.ts"
    artifact.write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.spec.ts"
    outside.write_text("secret", encoding="utf-8")
    resolver = ArtifactResolver(tmp_path)

    resolved = resolver.resolve("project/test_case/demo/a_login.spec.ts")
    artifact.unlink()
    artifact.symlink_to(outside)

    with pytest.raises(HTTPException) as error:
        resolver.open_public_file(resolved)
    assert error.value.status_code == 404


def test_raw_route_only_serves_small_raster_images(tmp_path: Path) -> None:
    project = _project(tmp_path)
    image = project / "test-results" / "run" / "artifacts" / "test-failed-1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"not-a-real-image")
    html = project / "test-results" / "run" / "html-report" / "index.html"
    html.parent.mkdir(parents=True)
    html.write_text("<h1>active</h1>", encoding="utf-8")
    client = TestClient(build_artifact_http_app(tmp_path))

    image_response = client.get(
        "/artifacts/raw",
        params={"path": "project/test-results/run/artifacts/test-failed-1.png"},
    )
    html_response = client.get(
        "/artifacts/raw",
        params={"path": "project/test-results/run/html-report/index.html"},
    )

    assert image_response.status_code == 200
    assert image_response.headers["content-type"].startswith("image/png")
    assert html_response.status_code == 404
