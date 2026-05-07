#!/usr/bin/env bash
set -euo pipefail

START_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$START_DIR/.." && pwd)"
MODE="${1:-start}"

print_usage() {
  printf "用法：%s [start|end|logs]\n" "$START_DIR/start.sh" >&2
}

detect_platform() {
  local ostype_value="${OSTYPE:-}"
  local uname_value
  uname_value="$(uname -s 2>/dev/null || true)"

  case "$ostype_value" in
    darwin*)
      printf "macos\n"
      return
      ;;
    msys*|cygwin*)
      printf "windows\n"
      return
      ;;
  esac

  case "$uname_value" in
    Darwin*)
      printf "macos\n"
      return
      ;;
    MINGW*|MSYS*|CYGWIN*)
      printf "windows\n"
      return
      ;;
  esac

  if [ "${OS:-}" = "Windows_NT" ]; then
    printf "windows\n"
    return
  fi

  printf "unsupported\n"
}

main() {
  case "$MODE" in
    start|end|logs)
      ;;
    *)
      print_usage
      exit 1
      ;;
  esac

  export WEB_AUTOTEST_ENTRY="$START_DIR/start.sh"

  case "$(detect_platform)" in
    macos)
      exec "$START_DIR/script/macos-start.command" "$MODE"
      ;;
    windows)
      if ! command -v powershell.exe >/dev/null 2>&1; then
        printf "未找到 powershell.exe，无法在当前 Windows Bash 环境中运行启动脚本。\n" >&2
        exit 1
      fi
      exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$START_DIR/script/windows-start.ps1" "$MODE"
      ;;
    *)
      printf "不支持的运行环境。当前只支持 macOS 和 Windows Bash 环境。\n" >&2
      exit 1
      ;;
  esac
}

main
