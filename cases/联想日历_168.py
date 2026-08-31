#!/usr/bin/env python3
"""联想日历_168 用例：无课程表时验证图库导入入口
前提：设备无课程表（pm clear 重置）
步骤: 更多→课程表 → 图库导入课程表 → 验证进入图片选择
"""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_framework import TestCase

PKG = "com.zui.calendar"


def run():
    t = TestCase("联想日历_168")

    # ── 前置条件：确保无课程表（pm clear 重置到首次使用）────────────
    t.step("前置条件-清空课程表")
    t.pm_clear(PKG)
    time.sleep(1)
    # 启动弹窗看门狗：检测到权限/引导弹窗立即点击（按测试要求同意）
    t.start_watchdog(policy="allow")

    # ── Step 1: 更多→课程表 ────────────────────────────────────────
    t.step("Step1 主页→更多→课程表")
    # 启动 App
    subprocess.run(["adb", "shell", "monkey", "-p", PKG,
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    time.sleep(2.5)
    # 首启弹窗由看门狗自动处理，等主界面出现
    time.sleep(3)
    # 打开"更多"菜单（元素定位+重试）
    if not t.open_more_menu():
        t.record("FAIL", f"未能打开'更多'菜单（含课程表项），屏幕={t.screen_text()[:6]}")
        t.blocked("无法进入课程表")
        return t.finish()
    t.record("PASS", "更多菜单弹出，包含'课程表'入口")
    t.tap_text("课程表")
    time.sleep(1.5)
    t.screenshot("01_课程表页面")

    # ── Step 2: 图库导入课程表按钮可点击 ───────────────────────────
    t.step("Step2 点击图库导入课程表")
    texts = t.screen_text()
    if any("图库导入课程表" in x for x in texts):
        # 空状态：页面直接有大按钮
        t.record("PASS", "空状态页显示'从图库导入课程表'按钮（可点击）")
        t.tap_text("从图库导入课程表")
        time.sleep(1.5)
    else:
        # 非空状态：工具栏图标 → 菜单
        import_icon = None
        for attempt in range(3):
            xml = t.d.dump_hierarchy()
            cands = []
            for n in re.findall(r"<node[^>]*>", xml):
                if 'clickable="true"' not in n:
                    continue
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
                if not b:
                    continue
                x1, y1, x2, y2 = map(int, b.groups())
                if y1 < 300 and x1 > 1000:
                    cands.append((x1, (x1 + x2) // 2, (y1 + y2) // 2))
            if cands:
                cands.sort()
                t.tap_xy(cands[0][1], cands[0][2])
                time.sleep(1)
                texts = t.screen_text()
                if any("图库导入" in x for x in texts):
                    t.record("PASS", "工具栏导入菜单出现，'图库导入课程表'可点击")
                    t.tap_text("图库导入课程表")
                    time.sleep(1.5)
                break
            time.sleep(1)
        else:
            t.record("FAIL", f"未找到图库导入入口，屏幕={texts[:6]}")
            return t.finish()

    # ── Step 3: 验证进入图片选择入口（用前台 Activity 判定，防假通过）──
    t.step("Step3 验证进入图片选择入口")
    # 看门狗自动处理权限/提示弹窗，等待进入选择器（轮询 Activity 最多 15s）
    act = ""
    for _ in range(20):
        time.sleep(0.75)
        act = t.current_activity()
        if "photopicker" in act.lower() or "PhotoPicker" in act:
            break
    is_picker = ("photopicker" in act.lower()) or ("PhotoPicker" in act)
    if is_picker:
        t.record("PASS", f"已进入系统图片选择入口（前台 Activity: {act}）")
    else:
        t.record("FAIL", f"未进入图片选择入口，当前 Activity={act}，屏幕={t.screen_text()[:5]}")

    t.stop_watchdog()
    return t.finish()


if __name__ == "__main__":
    run()
