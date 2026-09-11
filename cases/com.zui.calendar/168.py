#!/usr/bin/env python3
"""联想日历_168 用例：无课程表时验证图库导入入口
前提：设备无课程表（pm clear 重置）
步骤: 更多→课程表 → 图库导入课程表 → 验证进入图片选择
"""
import os
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试用例 联想日历_168
前提：确保设备没有课程表。如果存在需要删除所有课程表
操作步骤
1.日历点击页面右上角更多按钮，选择课程表
2. 点击"从图库导入课程表"按钮
3. 验证可进入图片选择入口
预期结果
1. 可打开课程表页面
2. 图库导入入口可点击
3. 可正常进入图片选择入口"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase
from _flow import tap_more_menu, top_bar_icons

PKG = "com.zui.calendar"


def run():
    t = TestCase("联想日历_168")

    # 清场钩子：登记"退出时还原自动旋转"。
    # 本用例无显式转屏代码，但 pm_clear + 首启授权流程会隐式把
    # accelerometer_rotation 从 0 变成 1（实测报告记了污染 0→1），
    # 残留会让后续用例坐标系全错（见 lock_portrait 注释的 119 血泪教训）。
    # add_prop_restore 先读原值、退出时自动还原，且任何退出路径都执行
    # （含中途 return / 异常），比写在函数末尾可靠。
    t.add_prop_restore("accelerometer_rotation")

    # ── 前置条件：确保无课程表（pm clear 重置到首次使用）────────────
    t.step("前置条件-清空课程表并授权")
    t.pm_clear(PKG)
    # 图库导入依赖存储权限；先授予，避免弹"需要权限"说明弹窗导致无法进入选择器
    t.grant_permission(PKG, "android.permission.READ_EXTERNAL_STORAGE")
    t.grant_permission(PKG, "android.permission.READ_MEDIA_IMAGES")
    time.sleep(1)
    # 启动弹窗看门狗：检测到权限/引导弹窗立即点击（按测试要求同意）
    t.start_watchdog(policy="allow")

    # ── Step 1: 更多→课程表 ────────────────────────────────────────
    t.step("Step1 主页→更多→课程表")
    # 启动 App（t.launch_app 绑定本用例 serial，多设备不串台）
    t.launch_app(PKG)
    # 等主界面就绪：「更多」按钮出现 = 首页渲染完成。
    # 轮询期间每次 dump 都顺带驱动看门狗，首启弹窗被自动处理。
    t.wait_rid("com.zui.calendar:id/iv_more", timeout=15)
    # 打开"更多"菜单（元素定位 iv_more + 重试）
    if not tap_more_menu(t):
        t.record("FAIL", f"未能打开'更多'菜单（含课程表项），屏幕={t.screen_text()[:6]}")
        t.blocked("无法进入课程表")
        return t.finish()
    t.record("PASS", "更多菜单弹出，包含'课程表'入口")
    t.tap_text("课程表", silent=True)   # 后续 wait_text 验证，避免双重 WARN
    # pm_clear 后必为空状态页；等到空态文案再截图，而非固定 sleep
    if not t.wait_text("还未添加课程表", timeout=8):
        t.record("WARN", "课程表空状态文案未在 8s 内出现，按当前屏幕继续")
    t.screenshot("01_课程表页面")

    # ── Step 2: 图库导入课程表按钮可点击 ───────────────────────────
    t.step("Step2 点击图库导入课程表")
    texts = t.screen_text()
    if any("图库导入课程表" in x for x in texts):
        # 空状态：页面直接有大按钮
        t.record("PASS", "空状态页显示'从图库导入课程表'按钮（可点击）")
        t.tap_text("从图库导入课程表", silent=True)   # Step3 用 Activity 验证
    else:
        # 非空状态：顶栏图标（最左 = 导入菜单）
        for attempt in range(3):
            icons = top_bar_icons(t)
            if icons:
                t.tap_xy(*icons[0][:2])
                # 等菜单项出现（条件等待，替代固定 sleep）
                if t.wait_text("图库导入", timeout=3):
                    t.record("PASS", "工具栏导入菜单出现，'图库导入课程表'可点击")
                    t.tap_text("图库导入课程表", silent=True)
                break
            time.sleep(1)
        else:
            t.record("FAIL", f"未找到图库导入入口，屏幕={texts[:6]}")
            return t.finish()

    # ── Step 3: 验证进入图片选择入口（用前台 Activity 判定，防假通过）──
    t.step("Step3 验证进入图片选择入口")
    # 看门狗自动处理权限/提示弹窗，等前台 Activity 进入选择器（最多 15s）
    act = t.wait_activity("photopicker", timeout=15)
    if act:
        t.record("PASS", f"已进入系统图片选择入口（前台 Activity: {act}）")
    else:
        t.record("FAIL", f"未进入图片选择入口，当前 Activity={act}，屏幕={t.screen_text()[:5]}")

    t.stop_watchdog()
    return t.finish()


if __name__ == "__main__":
    run()
