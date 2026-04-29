#!/usr/bin/env bash

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "用法: $0 <日志文件>"
  exit 1
fi

LOG_FILE="$1"

if [ ! -f "$LOG_FILE" ]; then
  echo "日志文件不存在: $LOG_FILE"
  exit 1
fi

# 需要高亮的字符，可以继续往这里追加
HIGHLIGHT_PATTERNS=(
  '"tool_start"} name='
  'demo'
)

# 颜色：大红色加粗
RED_BOLD='\033[1;91m'
RESET='\033[0m'

# 使用一个极少出现在日志里的分隔符，避免关键词里包含空格时被拆开
SEP=$'\034'
PATTERN_TEXT=""

for pattern in "${HIGHLIGHT_PATTERNS[@]}"; do
  PATTERN_TEXT+="${pattern}${SEP}"
done

tail -F "$LOG_FILE" | awk \
  -v red_bold="$RED_BOLD" \
  -v reset="$RESET" \
  -v sep="$SEP" \
  -v patterns="$PATTERN_TEXT" '
BEGIN {
  n = split(patterns, pats, sep)
}

{
  line = $0

  for (i = 1; i <= n; i++) {
    if (pats[i] != "") {
      gsub(pats[i], red_bold pats[i] reset, line)
    }
  }

  print line
  fflush()
}
'
