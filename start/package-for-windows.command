#!/usr/bin/env bash
# 双击此文件，将项目源码压缩为可迁移到 Windows 的 ZIP 包。
# 黑名单模式：保留全部源码和配置，仅排除可重新生成的依赖、缓存与运行产物。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_ROOT")"
OUTPUT_DIR="${PACKAGE_OUTPUT_DIR:-$(dirname "$PROJECT_ROOT")}"
TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
ARCHIVE_PATH="$OUTPUT_DIR/${PROJECT_NAME}-windows-${TIMESTAMP}.zip"

fail() {
  printf '打包失败：%s\n' "$1" >&2
  rm -f "$ARCHIVE_PATH"
  exit 1
}

for command_name in git python3 unzip; do
  command -v "$command_name" >/dev/null 2>&1 || fail "未找到 ${command_name} 命令。"
done

REQUIRED_FILES=(
  "README.md"
  "start/README.md"
  "start/macos-start.command"
  "start/windows-start.ps1"
  "start/package-for-windows.command"
  "start/package-for-windows.py"
  "web-agent/.env"
  "web-agent/.env.example"
  "web-agent/langgraph.json"
  "web-agent/pyproject.toml"
  "web-agent/uv.lock"
  "web-agent/deep_agent/app.py"
  "web-agent/deep_agent/assets/demo/package.json"
  "web-agent-client/package.json"
  "web-agent-client/pnpm-lock.yaml"
  "web-agent-client/src/App.tsx"
  "web-agent-client/src-tauri/Cargo.toml"
  "web-agent-client/src-tauri/Cargo.lock"
  "web-agent-client/src-tauri/tauri.conf.json"
  "web-agent-client/src-tauri/src/main.rs"
  "web-agent-client/src-tauri/icons/icon.ico"
)

for required_file in "${REQUIRED_FILES[@]}"; do
  [ -f "$PROJECT_ROOT/$required_file" ] || fail "缺少必需文件：$required_file"
done

git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "项目目录不是 Git 工作区，无法校验源码清单。"
mkdir -p "$OUTPUT_DIR"

printf '正在创建 Windows 迁移压缩包…\n'
printf '项目：%s\n输出：%s\n\n' "$PROJECT_ROOT" "$ARCHIVE_PATH"

# Python zipfile 会为非 ASCII 文件名设置 UTF-8 标志，避免 Windows 解压中文路径乱码。
python3 "$SCRIPT_DIR/package-for-windows.py" "$PROJECT_ROOT" "$ARCHIVE_PATH" || fail "创建 ZIP 失败。"

unzip -tq "$ARCHIVE_PATH" >/dev/null || fail "ZIP 完整性检查失败。"

archive_has_file() {
  unzip -Z1 "$ARCHIVE_PATH" "$1" >/dev/null 2>&1
}

for required_file in "${REQUIRED_FILES[@]}"; do
  archive_entry="$PROJECT_NAME/$required_file"
  archive_has_file "$archive_entry" || fail "压缩包缺少必需文件：$required_file"
done

tracked_count=0
while IFS= read -r -d '' tracked_file; do
  tracked_count=$((tracked_count + 1))
  archive_entry="$PROJECT_NAME/$tracked_file"
  archive_has_file "$archive_entry" || fail "压缩包遗漏 Git 跟踪文件：$tracked_file"
done < <(git -C "$PROJECT_ROOT" ls-files -z)

archive_list="$(mktemp)"
trap 'rm -f "$archive_list"' EXIT
unzip -Z1 "$ARCHIVE_PATH" > "$archive_list"
while IFS= read -r archive_entry; do
  case "$archive_entry" in
    "$PROJECT_NAME/.git/"*|\
    "$PROJECT_NAME/.langgraph_api/"*|\
    "$PROJECT_NAME/.idea/"*|\
    "$PROJECT_NAME/"*/.idea/*|\
    "$PROJECT_NAME/.vscode/"*|\
    "$PROJECT_NAME/"*/.vscode/*|\
    "$PROJECT_NAME/node_modules/"*|\
    "$PROJECT_NAME/"*/node_modules/*|\
    "$PROJECT_NAME/"*/__pycache__/*|\
    "$PROJECT_NAME/web-agent/.venv/"*|\
    "$PROJECT_NAME/web-agent/.langgraph_api/"*|\
    "$PROJECT_NAME/web-agent/runtime/"*|\
    "$PROJECT_NAME/web-agent-client/dist/"*|\
    "$PROJECT_NAME/web-agent-client/test-results/"*|\
    "$PROJECT_NAME/web-agent-client/playwright-report/"*|\
    "$PROJECT_NAME/web-agent-client/src-tauri/target/"*|\
    "$PROJECT_NAME/output/"*|\
    "$PROJECT_NAME/start/backend.log")
      fail "压缩包包含不应迁移的运行产物：$archive_entry"
      ;;
  esac
done < "$archive_list"

printf '校验通过：包含 %s 个 Git 跟踪文件及 Windows 运行所需配置。\n' "$tracked_count"
printf '完成。压缩包大小：%s\n' "$(du -h "$ARCHIVE_PATH" | awk '{print $1}')"
printf '压缩包位置：%s\n' "$ARCHIVE_PATH"
printf 'SHA-256：%s\n' "$(shasum -a 256 "$ARCHIVE_PATH" | awk '{print $1}')"
printf '\n注意：压缩包包含 web-agent/.env，可能含有 API 密钥，请勿随意分享。\n'
if [ "${PACKAGE_NO_PAUSE:-0}" != "1" ] && [ -t 0 ]; then
  read -r -p '按回车键关闭窗口…' _
fi
