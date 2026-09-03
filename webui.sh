#!/usr/bin/env bash
# Android 测试台 Web 界面 一键启动/停止（skill 包版）
# 本脚本位于 skill 包根目录，webui.py 在同目录 framework/ 下；
# 数据（SQLite/知识库）固定在工作区 DSH_ANDROID_TEST_DIR（默认 ~/dsh-android-test）。
# 用法:
#   ./webui.sh         启动（默认 8900 端口）
#   ./webui.sh 9000    指定端口启动
#   ./webui.sh stop    停止
#   ./webui.sh status  查看状态
set -e
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
FRAMEWORK="$SKILL_DIR/framework"
WEBUI="$FRAMEWORK/webui.py"

# 测试工作区：环境变量 > 默认 ~/dsh-android-test
TEST_DIR="${DSH_ANDROID_TEST_DIR:-$HOME/dsh-android-test}"
# venv：工作区 .venv > 默认
VENV="${DSH_ANDROID_TEST_VENV:-$TEST_DIR/.venv}"
PYTHON="$VENV/bin/python"

PORT="${2:-8900}"
PIDFILE="$SKILL_DIR/.webui.pid"
LOG="$SKILL_DIR/webui.log"

require_python() {
  if [ ! -x "$PYTHON" ]; then
    echo "❌ 未找到 venv: $PYTHON （先在工作区跑 bash setup.sh 装环境）"
    exit 1
  fi
}

case "${1:-start}" in
  start)
    require_python
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "⚠️  已在运行 (PID $(cat "$PIDFILE"), http://127.0.0.1:${PORT})"
      exit 0
    fi
    # 显式指定数据目录与 skill 包位置，保证从任何位置启动都读写同一份资产
    DSH_ANDROID_TEST_DIR="$TEST_DIR" DSH_SKILL_DIR="$SKILL_DIR" \
    nohup "$PYTHON" "$WEBUI" --port "$PORT" >"$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1.5
    echo "✅ 已启动: http://127.0.0.1:${PORT}"
    echo "   工作区: $TEST_DIR"
    echo "   日志: $LOG"
    ;;
  stop)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      kill "$(cat "$PIDFILE")"
      rm -f "$PIDFILE"
      echo "🛑 已停止"
    else
      echo "ℹ️  未在运行"
    fi
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "✅ 运行中: http://127.0.0.1:${PORT} (PID $(cat "$PIDFILE"))"
      echo "   工作区: $TEST_DIR"
    else
      echo "❌ 未运行"
    fi
    ;;
  *)
    echo "用法: $0 [start|stop|status] [端口]"
    exit 1
    ;;
esac
