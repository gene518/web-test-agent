from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_compose_keeps_agent_config_writable_and_scheduler_config_read_only() -> None:
    compose = (REPOSITORY_ROOT / "deploy" / "compose.yaml").read_text(
        encoding="utf-8"
    )
    agent_section, scheduler_and_rest = compose.split("\n  scheduler:\n", 1)
    scheduler_section = scheduler_and_rest.split("\n  updater:\n", 1)[0]

    required_config_mount = (
        "${CONFIG_HOST_PATH:?请设置配置目录的宿主机绝对路径}:/data/config"
    )
    assert f"- {required_config_mount}\n" in agent_section
    assert f"- {required_config_mount}:ro\n" in scheduler_section
    assert "BG_JOB_ISOLATED_LOOPS: \"true\"" in compose
    assert "SCHEDULER_MAINTENANCE_FILE: /update-control/maintenance.json" in compose
    assert "SCHEDULER_STATUS_FILE: /scheduler-state/scheduler-status.json" in compose
    assert "name: web-test-agent-internal" in compose
    assert "name: web-test-agent-update-state" in compose
    assert "name: web-test-agent-scheduler-state" in compose
    assert "UPDATER_STATE_VOLUME" not in compose
    assert "- update_state:/update-control:ro" in scheduler_section
    assert "- scheduler_state:/scheduler-state" in scheduler_section
    assert "MASTER_LLM__API_KEY" not in scheduler_section
    assert "SPECIALIST_LLM__API_KEY" not in scheduler_section
    updater_section = compose.split("\n  updater:\n", 1)[1].split("\n  web:\n", 1)[0]
    assert "read_only: true" in updater_section
    assert 'tmpfs: ["/tmp:size=64m,mode=1777"]' in updater_section
    assert "cap_drop: [ALL]" in updater_section
    assert "env_file:" not in compose


def test_container_runtime_is_pinned_non_root_and_maintenance_gated() -> None:
    agent_dockerfile = (
        REPOSITORY_ROOT / "deploy" / "Dockerfile.agent"
    ).read_text(encoding="utf-8")
    web_dockerfile = (REPOSITORY_ROOT / "deploy" / "Dockerfile.web").read_text(
        encoding="utf-8"
    )
    caddyfile = (REPOSITORY_ROOT / "deploy" / "Caddyfile").read_text(
        encoding="utf-8"
    )

    assert "mcr.microsoft.com/playwright:v1.61.1-noble@sha256:" in agent_dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.7@sha256:" in agent_dockerfile
    assert "@playwright/test@1.61.1" in agent_dockerfile
    assert "--mount=type=cache,target=/root/.cache/uv" in agent_dockerfile
    assert "UV_CACHE_DIR=/root/.cache/uv UV_HTTP_RETRIES=5 UV_HTTP_TIMEOUT=300" in agent_dockerfile
    assert "uv sync --frozen --extra dev" in agent_dockerfile
    assert "USER pwuser" in agent_dockerfile
    assert "mkdir -p /app/.langgraph_api /data/projects /data/config /scheduler-state" in agent_dockerfile
    assert "/home/pwuser /scheduler-state" in agent_dockerfile
    assert "--no-reload" in agent_dockerfile
    assert "node:24-alpine@sha256:" in web_dockerfile
    assert "caddy:2.10.2-alpine@sha256:" in web_dockerfile
    assert "VITE_DEPLOYMENT_MODE=container" in web_dockerfile
    assert "setcap -r /usr/bin/caddy" in web_dockerfile
    assert "USER 1000:1000" in web_dockerfile
    assert "method POST PUT PATCH DELETE" in caddyfile
    assert "forward_auth updater:8090" in caddyfile
    assert "route /health/agent" in caddyfile
    assert "route /health/update" in caddyfile


def test_container_publish_is_a_main_push_ci_job_for_the_current_revision() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "container-publish:" in workflow
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    assert "- backend-test" in workflow
    assert "- client-frontend" in workflow
    assert "- windows-startup-check" in workflow
    assert "- web-agent-client-windows" in workflow
    assert "packages: write" in workflow
    assert "id-token: write" in workflow
    assert "SOURCE_SHA: ${{ github.sha }}" in workflow
    assert "ref: ${{ steps.revision.outputs.sha }}" in workflow
    assert "fetch-depth: 1" in workflow
    assert "persist-credentials: false" in workflow
    assert 'test "$(git rev-parse HEAD)" = "$REVISION"' in workflow
    assert "workflow_run:" not in workflow
    assert not (REPOSITORY_ROOT / ".github" / "workflows" / "container.yml").exists()


def test_container_setup_restricts_secret_and_runtime_directory_permissions() -> None:
    env_example = (REPOSITORY_ROOT / "deploy" / ".env.example").read_text(
        encoding="utf-8"
    )
    readme = (REPOSITORY_ROOT / "deploy" / "README.md").read_text(
        encoding="utf-8"
    )

    assert "UPDATE_INTERNAL_TOKEN=" in env_example
    assert "UPDATE_CSRF_SECRET=" in env_example
    assert "复制后立即执行 `chmod 600 .env`" in env_example
    assert "umask 077" in readme
    assert "chmod 600 .env" in readme
    assert "sudo install -d -o 1001 -g 1001 -m 0750 data data/projects data/config" in readme
    assert "scheduler_tasks.example.json data/config/scheduler_tasks.json" in readme
    assert "`.env` 必须保持 `0600`" in readme
    assert "`/var/run/docker.sock`" in readme
    assert "宿主机级 Docker 控制权" in readme
    assert "不能把它们当作隔离边界" in readme
    assert "socket proxy" in readme
    assert ".github/workflows/ci.yml@refs/heads/main" in readme
