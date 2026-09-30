#!/bin/bash
# 本脚本负责用本机已有的 Node 运行 zhaopin CLI，免去手动配置 PATH。
#
# 用法（在 zhaopin-cli 目录下）：
#   bash scripts/run.sh login              打开登录页
#   bash scripts/run.sh positions          查看职位
#   bash scripts/run.sh recommend 后端开发   读取候选人
#   bash scripts/run.sh self-check         运行环境自检
#
# 查找顺序：ZHAOPIN_NODE_BIN 环境变量 → PATH 中的 node → 本机已有运行时目录。

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_ENTRY="$SCRIPT_DIR/../dist/cli/index.js"
SELF_CHECK="$SCRIPT_DIR/self-check.mjs"

# 按优先级查找一个可用的 node 可执行文件。
find_node() {
  if [ -n "${ZHAOPIN_NODE_BIN:-}" ] && [ -x "${ZHAOPIN_NODE_BIN}" ]; then
    printf '%s' "$ZHAOPIN_NODE_BIN"
    return
  fi
  if command -v node >/dev/null 2>&1; then
    command -v node
    return
  fi
  local candidate
  for candidate in "$HOME"/.workbuddy/binaries/node/versions/*/bin/node; do
    if [ -x "$candidate" ]; then
      printf '%s' "$candidate"
      return
    fi
  done
}

NODE_BIN="$(find_node)"
if [ -z "$NODE_BIN" ]; then
  echo "未找到可用的 Node。请安装 Node.js 20 或更高版本，或用 ZHAOPIN_NODE_BIN 指定路径。" >&2
  exit 1
fi

# self-check 直接运行自检脚本，其余参数交给 CLI。
if [ "${1:-}" = "self-check" ]; then
  shift
  exec "$NODE_BIN" "$SELF_CHECK" "$@"
fi

if [ ! -f "$CLI_ENTRY" ]; then
  echo "未找到构建产物 $CLI_ENTRY。请先执行 npm run build。" >&2
  exit 1
fi

exec "$NODE_BIN" "$CLI_ENTRY" "$@"
