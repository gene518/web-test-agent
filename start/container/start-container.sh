#!/usr/bin/env bash
set -euo pipefail

# 这个脚本由 VPS 运维人员从仓库任意目录调用，统一转到 deploy/ 下执行
# Docker Compose，并确保桌面与容器共用 web-agent/.env 中的应用配置。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_DIR="$PROJECT_ROOT/deploy"
MODEL_ENV_FILE="${WEB_TEST_AGENT_MODEL_ENV_FILE:-$PROJECT_ROOT/web-agent/.env}"
DEPLOY_ENV_FILE="${WEB_TEST_AGENT_CONTAINER_ENV_FILE:-$DEPLOY_DIR/.env}"
MODE="${1:-help}"
SOURCE_SHA="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || printf '0000000000000000000000000000000000000000')"

if [ -f "$MODEL_ENV_FILE" ]; then
  MODEL_ENV_FILE="$(cd "$(dirname "$MODEL_ENV_FILE")" && pwd)/$(basename "$MODEL_ENV_FILE")"
fi

if [ -f "$DEPLOY_ENV_FILE" ]; then
  DEPLOY_ENV_FILE="$(cd "$(dirname "$DEPLOY_ENV_FILE")" && pwd)/$(basename "$DEPLOY_ENV_FILE")"
fi

fail() {
  printf '容器启动失败：%s\n' "$*" >&2
  exit 1
}

require_compose() {
  command -v docker >/dev/null 2>&1 || fail "未找到 Docker Engine。"
  docker compose version >/dev/null 2>&1 || fail "未找到 Docker Compose v2。"
}

require_env_files() {
  [ -f "$MODEL_ENV_FILE" ] || fail "未找到模型配置 $MODEL_ENV_FILE。请先创建 web-agent/.env。"
  [ -f "$DEPLOY_ENV_FILE" ] || fail "未找到部署配置 $DEPLOY_ENV_FILE。请先按 start/container/README.md 创建部署配置。"
}

run_compose() {
  (
    cd "$DEPLOY_DIR"
    DEPLOYED_SHA="$SOURCE_SHA" docker compose \
        --env-file "$MODEL_ENV_FILE" \
        --env-file "$DEPLOY_ENV_FILE" \
        "$@"
  )
}

print_service_group() {
  local title="$1"
  shift
  printf '\n%s\n' "$title"
  run_compose ps "$@"
}

print_help() {
  cat <<'EOF'
用法：bash start/container/start-container.sh <操作> [参数]

操作：
  bootstrap       首次从当前源码构建并启动容器。
  up              使用模型配置与部署配置启动或恢复服务。
  down            停止服务，保留命名卷和宿主机数据。
  logs [服务...]  持续查看所有服务或指定服务的日志。
  status          查看服务状态。
  config          校验并输出 Docker Compose 解析后的配置。

版本升级由部署人员更新源码后重新执行 bootstrap，不提供 H5 在线更新入口。
完整的初始化、权限和手工升级说明见 deploy/README.md。
EOF
}

case "$MODE" in
  bootstrap)
    require_compose
    require_env_files
    run_compose config --quiet
    run_compose build
    run_compose up -d --wait
    ;;
  up)
    require_compose
    require_env_files
    run_compose config --quiet
    run_compose up -d --wait
    ;;
  down)
    require_compose
    require_env_files
    run_compose down
    ;;
  logs)
    require_compose
    require_env_files
    shift || true
    run_compose logs -f "$@"
    ;;
  status)
    require_compose
    require_env_files
    print_service_group "核心运行组（Agent / Scheduler）" agent scheduler
    print_service_group "访问入口组（Web）" web
    ;;
  config)
    require_compose
    require_env_files
    run_compose config
    ;;
  help|-h|--help)
    print_help
    ;;
  *)
    print_help
    fail "不支持的操作：$MODE"
    ;;
esac
