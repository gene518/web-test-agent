#!/usr/bin/env bash
# 双击此文件，将项目源码压缩为可迁移到 Windows 的 ZIP 包。
# 黑名单模式：仅排除可重新生成的依赖、缓存、日志、运行历史和 Git 历史。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_ROOT")"
OUTPUT_DIR="$(dirname "$PROJECT_ROOT")"
TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
ARCHIVE_PATH="$OUTPUT_DIR/${PROJECT_NAME}-windows-${TIMESTAMP}.zip"

# 注意：web-agent/.env 会被保留，方便迁移配置；压缩包中可能包含密钥，请妥善保存。
EXCLUDES=(
  "${PROJECT_NAME}/.git/*"
  "${PROJECT_NAME}/.idea/*"
  "${PROJECT_NAME}/.vscode/*"
  "${PROJECT_NAME}/.DS_Store"
  "${PROJECT_NAME}/*/.DS_Store"
  "${PROJECT_NAME}/.pytest_cache/*"
  "${PROJECT_NAME}/.uv-cache/*"
  "${PROJECT_NAME}/.playwright-cli/*"
  "${PROJECT_NAME}/.playwright-mcp/*"
  "${PROJECT_NAME}/web-agent/.venv/*"
  "${PROJECT_NAME}/web-agent/.langgraph_api/*"
  "${PROJECT_NAME}/web-agent/.pytest_cache/*"
  "${PROJECT_NAME}/web-agent/runtime/*"
  "${PROJECT_NAME}/web-agent/web_autotest_agent.egg-info/*"
  "${PROJECT_NAME}/web-agent/scheduler_tasks.json"
  "${PROJECT_NAME}/web-agent/tests/debug/*.log"
  "${PROJECT_NAME}/web-portal/node_modules/*"
  "${PROJECT_NAME}/web-portal/.next/*"
  "${PROJECT_NAME}/web-portal/dist/*"
  "${PROJECT_NAME}/web-portal/*.tsbuildinfo"
  "${PROJECT_NAME}/start/.cache/*"
  "${PROJECT_NAME}/start/backend.log"
  "${PROJECT_NAME}/start/frontend.log"
  "${PROJECT_NAME}/**/__pycache__/*"
  "${PROJECT_NAME}/**/*.pyc"
  "${PROJECT_NAME}/**/*.pyo"
)

printf '正在创建 Windows 迁移压缩包…\n'
printf '项目：%s\n输出：%s\n\n' "$PROJECT_ROOT" "$ARCHIVE_PATH"

cd "$OUTPUT_DIR"
zip -r -q "$ARCHIVE_PATH" "$PROJECT_NAME" -x "${EXCLUDES[@]}"

printf '完成。压缩包大小：%s\n' "$(du -h "$ARCHIVE_PATH" | awk '{print $1}')"
printf '压缩包位置：%s\n' "$ARCHIVE_PATH"
printf '\n注意：压缩包包含 web-agent/.env，可能含有 API 密钥，请勿随意分享。\n'
read -r -p '按回车键关闭窗口…' _
