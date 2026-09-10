#!/usr/bin/env bash
# 导出本 skill 为可分发的压缩包
# 用法: bash export.sh [版本号]
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(basename "$HERE")"          # android-gui-testing
# 版本号优先取 framework/VERSION；命令行参数可覆盖
VERSION="${1:-$(cat "$HERE/framework/VERSION" 2>/dev/null || echo v1)}"
OUT="$HERE/../${SKILL_DIR}-${VERSION}.zip"

cd "$HERE/.."

# 排除缓存/临时文件
zip -r "$OUT" "$SKILL_DIR" \
    -x "*__pycache__*" \
    -x "*.DS_Store" \
    -x "*.pyc" \
    >/dev/null

echo "✅ 已导出: $OUT"
echo ""
echo "════════ 给对方的使用说明 ════════"
echo "1. 解压:   unzip ${SKILL_DIR}-${VERSION}.zip -d ~/.dsh/skills/"
echo "           # 或放到项目根 .dsh/skills/ 目录（仅本项目可用）"
echo "2. 装环境: cd ~/.dsh/skills/${SKILL_DIR} && bash setup.sh"
echo "           # 需要: 电脑有 adb、设备开 USB 调试并授权"
echo "3. 开始用:"
echo "   - AI 会话中: 加载 android-gui-testing skill 后发用例"
echo "   - 或直接跑示例: python run_case.py com.zui.calendar/172.py"
echo "════════════════════════════════════"
