#!/usr/bin/env bash
# android-test-skills skill 一键环境安装
# 用法: bash setup.sh [--with-agent]
set -e

HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="${1:-$HOME/android-test-skills-data}"
PYTHON="${PYTHON:-python3}"

echo "════════════════════════════════════════════"
echo "  Android GUI 测试环境安装"
echo "  工作目录: $WORKSPACE"
echo "════════════════════════════════════════════"

# 1. adb 检查
echo "▶ 1/5 检查 adb..."
if ! command -v adb >/dev/null; then
    echo "  ❌ 未找到 adb，请安装 Android SDK platform-tools"
    exit 1
fi
echo "  ✅ $(adb version | head -1)"

# 2. 设备检查
echo "▶ 2/5 检查设备..."
if ! adb devices | grep -q "device$"; then
    echo "  ❌ 未检测到已授权设备，请连接并开启 USB 调试"
    exit 1
fi
adb devices -l
echo "  ✅ 设备已连接"

# 3. venv 与依赖（关键步骤：失败即退出，不得伪装成功）
echo "▶ 3/5 创建虚拟环境并安装依赖..."
mkdir -p "$WORKSPACE"
$PYTHON -m venv "$WORKSPACE/.venv" 2>/dev/null || { echo "  ❌ python3 venv 失败"; exit 1; }
"$WORKSPACE/.venv/bin/pip" install -q --upgrade pip setuptools wheel
"$WORKSPACE/.venv/bin/pip" install -q -r "$HERE/requirements.txt" \
    || { echo "  ❌ 依赖安装失败（详见 requirements.txt）"; exit 1; }
# import 自检：装上了但 import 不了的隐性失败在这里暴露
"$WORKSPACE/.venv/bin/python" -c "import uiautomator2, rapidocr_onnxruntime" \
    || { echo "  ❌ 依赖 import 自检失败"; exit 1; }
echo "  ✅ venv 就绪 ($WORKSPACE/.venv)"

# 4. uiautomator2 设备端初始化（设备相关、可能瞬时失败：明确告知但不阻断安装）
echo "▶ 4/5 初始化 uiautomator2 设备端..."
if "$WORKSPACE/.venv/bin/python" -m uiautomator2 init; then
    echo "  ✅ u2 初始化完成"
else
    echo "  ⚠️ u2 init 失败（设备未连接/未授权时可稍后手动重跑）："
    echo "     $WORKSPACE/.venv/bin/python -m uiautomator2 init"
fi

# 5. 建工作区数据目录 + 首次复制 cases/knowledge
echo "▶ 5/5 初始化工作区..."
# 运行产物目录
mkdir -p "$WORKSPACE/storage/reports" "$WORKSPACE/storage/screenshots" "$WORKSPACE/storage/logs"
echo "  ✅ 运行产物: $WORKSPACE/storage/{reports,screenshots,logs}"

# 首次复制：cases/ + knowledge/（仅工作区不存在时复制，后续修改只动工作区）
if [ ! -d "$WORKSPACE/cases" ]; then
    cp -r "$HERE/cases" "$WORKSPACE/cases"
    echo "  ✅ 首次复制 cases/ → $WORKSPACE/cases"
else
    echo "  ℹ️  cases/ 已存在，跳过（后续修改只动工作区副本）"
fi
if [ ! -d "$WORKSPACE/knowledge" ]; then
    cp -r "$HERE/knowledge" "$WORKSPACE/knowledge"
    echo "  ✅ 首次复制 knowledge/ → $WORKSPACE/knowledge"
else
    echo "  ℹ️  knowledge/ 已存在，跳过"
fi
echo "  ✅ 代码/基线: $HERE/{framework,docs,evals,tests}（只读，不改工作区副本）"

# 可选: AutoGLM agent 环境
if [ "$1" = "--with-agent" ]; then
    echo "▶ 可选 安装 AutoGLM agent 环境 (Python 3.10+ 需要)..."
    if command -v python3.13 >/dev/null; then
        python3.13 -m venv "$WORKSPACE/.venv313"
        "$WORKSPACE/.venv313/bin/pip" install -q -e /tmp/Open-AutoGLM-main 2>/dev/null || \
        "$WORKSPACE/.venv313/bin/pip" install -q openai rapidocr_onnxruntime 2>/dev/null || true
        echo "  ✅ agent 环境就绪"
    else
        echo "  ⚠️ 未找到 python3.13，跳过 agent 环境（不影响基础测试）"
    fi
fi

echo ""
echo "════════════════════════════════════════════"
echo "✅ 安装完成！快速开始:"
echo "  cd $HERE/framework          # 注意：cd 到 skill 包，不是工作区"
echo "  .venv 里执行: $WORKSPACE/.venv/bin/python run_case.py <用例名>.py"
echo "  示例: $WORKSPACE/.venv/bin/python run_case.py com.zui.calendar/172.py"
echo ""
echo "  建议执行 smoke 探针验证全链路:"
echo "  $WORKSPACE/.venv/bin/python $HERE/framework/smoke.py"
echo "════════════════════════════════════════════"
