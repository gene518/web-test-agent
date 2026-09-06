from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_compose_has_three_services_and_scoped_runtime_mounts() -> None:
    compose = (REPOSITORY_ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    agent_section, scheduler_and_rest = compose.split("\n  scheduler:\n", 1)
    scheduler_section, web_section = scheduler_and_rest.split("\n  web:\n", 1)

    required_config_mount = (
        "${CONFIG_HOST_PATH:?请设置配置目录的宿主机绝对路径}:/data/config"
    )
    assert f"- {required_config_mount}\n" in agent_section
    assert f"- {required_config_mount}:ro\n" in scheduler_section
    assert 'BG_JOB_ISOLATED_LOOPS: "true"' in compose
    assert "name: web-test-agent-internal" in compose
    assert "name: web-test-agent-langgraph-state" in compose
    assert "MASTER_LLM__API_KEY" not in scheduler_section
    assert "SPECIALIST_LLM__API_KEY" not in scheduler_section
    assert "com.web-test-agent.group: core-runtime" in agent_section
    assert "com.web-test-agent.group: core-runtime" in scheduler_section
    assert "com.web-test-agent.group: access-entry" in web_section
    assert "\n  updater:\n" not in compose
    assert "UPDATE_" not in compose
    assert "docker.sock" not in compose
    assert "update_state" not in compose
    assert "scheduler_state" not in compose
    assert "env_file:" not in compose
    assert "H5_BASIC_AUTH" not in compose


def test_container_runtime_is_pinned_non_root_and_has_no_update_control_plane() -> None:
    agent_dockerfile = (REPOSITORY_ROOT / "deploy" / "Dockerfile.agent").read_text(
        encoding="utf-8"
    )
    web_dockerfile = (REPOSITORY_ROOT / "deploy" / "Dockerfile.web").read_text(
        encoding="utf-8"
    )
    caddyfile = (REPOSITORY_ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")

    assert "mcr.microsoft.com/playwright:v1.61.1-noble@sha256:" in agent_dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.7@sha256:" in agent_dockerfile
    assert "@playwright/test@1.61.1" in agent_dockerfile
    assert "--mount=type=cache,target=/root/.cache/uv" in agent_dockerfile
    assert (
        "UV_CACHE_DIR=/root/.cache/uv UV_HTTP_RETRIES=5 UV_HTTP_TIMEOUT=300"
        in agent_dockerfile
    )
    assert "uv sync --frozen --extra dev" in agent_dockerfile
    assert "USER pwuser" in agent_dockerfile
    assert (
        "mkdir -p /app/.langgraph_api /data/projects /data/config" in agent_dockerfile
    )
    assert "/scheduler-state" not in agent_dockerfile
    assert "--no-reload" in agent_dockerfile
    assert "node:24-alpine@sha256:" in web_dockerfile
    assert "caddy:2.10.2-alpine@sha256:" in web_dockerfile
    assert "VITE_DEPLOYMENT_MODE" not in web_dockerfile
    assert "setcap -r /usr/bin/caddy" in web_dockerfile
    assert "USER 1000:1000" in web_dockerfile
    assert "basic_auth" not in caddyfile
    assert "handle_path /api/langgraph/*" in caddyfile
    assert "handle /api/artifacts/*" in caddyfile
    assert "route /health/agent" in caddyfile
    assert "/api/update" not in caddyfile
    assert "forward_auth" not in caddyfile
    assert not (REPOSITORY_ROOT / "deploy" / "updater").exists()


def test_container_publish_builds_and_signs_only_agent_and_web_images() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "container-publish:" in workflow
    assert (
        "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    )
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
    assert "构建 Agent 镜像" in workflow
    assert "构建 Web 镜像" in workflow
    assert 'cosign sign --yes "$PREFIX-agent@$AGENT_DIGEST"' in workflow
    assert 'cosign sign --yes "$PREFIX-web@$WEB_DIGEST"' in workflow
    assert "Updater 镜像" not in workflow
    assert "UPDATER_DIGEST" not in workflow
    assert "workflow_run:" not in workflow
    assert not (REPOSITORY_ROOT / ".github" / "workflows" / "container.yml").exists()


def test_container_setup_contains_only_required_h5_and_data_configuration() -> None:
    env_example = (REPOSITORY_ROOT / "deploy" / ".env.example").read_text(
        encoding="utf-8"
    )
    readme = (REPOSITORY_ROOT / "deploy" / "README.md").read_text(encoding="utf-8")

    assert "H5_BASIC_AUTH" not in env_example
    assert "PROJECTS_HOST_PATH=" in env_example
    assert "CONFIG_HOST_PATH=" in env_example
    assert "UPDATE_" not in env_example
    assert "GITHUB_TOKEN" not in env_example
    assert "GHCR_TOKEN" not in env_example
    assert "DEPLOY_HOST_PATH" not in env_example
    assert "MASTER_LLM__API_KEY" not in env_example
    assert "SPECIALIST_LLM__API_KEY" not in env_example
    assert "umask 077" in readme
    assert "chmod 600 web-agent/.env deploy/.env" in readme
    assert (
        "sudo install -d -o 1001 -g 1001 -m 0750 data data/projects data/config"
        in readme
    )
    assert "scheduler_tasks.example.json data/config/scheduler_tasks.json" in readme
    assert "三个常驻容器" in readme
    assert "两个镜像" in readme
    assert "/var/run/docker.sock" not in readme
    assert "UPDATE_INTERNAL_TOKEN" not in readme
    assert "浏览器内版本检查或自动重启流程" in readme


def test_removed_update_feature_has_no_client_or_server_runtime_surface() -> None:
    client_root = REPOSITORY_ROOT / "web-agent-client"
    app = (client_root / "src" / "App.tsx").read_text(encoding="utf-8")
    vite_config = (client_root / "vite.config.ts").read_text(encoding="utf-8")

    assert "UpdateBadge" not in app
    assert "/api/update" not in vite_config
    assert not (client_root / "src" / "components" / "UpdateBadge.tsx").exists()
    assert not (client_root / "src" / "lib" / "update.ts").exists()
    assert not (client_root / "tests" / "e2e" / "h5-update.spec.ts").exists()
    assert not (client_root / "tests" / "e2e" / "h5-maintenance.spec.ts").exists()
    assert not (REPOSITORY_ROOT / "deploy" / "updater").exists()
