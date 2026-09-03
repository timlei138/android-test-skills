#!/usr/bin/env python3
"""联想日历_170 用例：图库导入创建新课表，验证原有课表不被覆盖
前提: 设备已有课程表（无则先手动创建）
"""
import os
import re
import subprocess
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试联想日历 170 号用例。
前提：1. 设备已有课程表（如果没有需要先手动创建一个课程表）
操作步骤：
1. 进入已保存的课程表，点击右上角添加按钮
2. 选择"图库导入"
3. 选择固定课程表图片并完成后续导入流程
预期结果：
1. 可正常选择图库导入
2. 导入成功后创建新的课程表
3. 原有课程表未被覆盖"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase
from _flow import tap_more_menu, top_bar_icons

PKG = "com.zui.calendar"


def run():
    t = TestCase("联想日历_170")

    # ── 前提：确保有课程表（无则手动创建"原课表"）──────────────────
    t.step("前提-创建课程表")
    t.pm_clear(PKG)
    time.sleep(1)
    subprocess.run(["adb", "shell", "am", "force-stop", "com.zui.camera"],
                   capture_output=True)
    subprocess.run(["adb", "shell", "monkey", "-p", PKG,
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    t.dismiss_first_use_dialogs(policy="allow", max_rounds=12, verbose=False)
    if not tap_more_menu(t):
        t.record("FAIL", "无法打开'更多'菜单")
        return t.finish()
    t.tap_text("课程表")
    time.sleep(2)
    if not t.tap_text("手动创建课程表", wait=4):
        t.record("FAIL", "未找到'手动创建课程表'")
        return t.finish()
    t.input_text("com.zui.calendar:id/et_schedule_name", "原课表")
    time.sleep(0.5)
    t.tap_el(rid="com.zui.calendar:id/save_view")   # 完成
    time.sleep(2)
    texts = t.screen_text()
    t.record("PASS" if any("原课表" in x for x in texts) else "FAIL",
             f"已创建课程表'原课表': {texts[:6]}")
    t.screenshot("00_原课表创建")

    # ── Step1: 进入课表 → 右上角添加按钮 → 图库导入 ────────────────
    t.step("Step1 进入课表，点添加按钮选图库导入")
    # 创建后停在编辑页，BACK 退出到展示页（显示 原课表+周视图）
    for _ in range(3):
        texts = t.screen_text()
        if any("第1周" in x for x in texts) and any("周一" in x for x in texts):
            break
        subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_BACK"],
                       capture_output=True)
        time.sleep(1.2)
    texts = t.screen_text()
    if not (any("原课表" in x for x in texts) and any("周一" in x for x in texts)):
        t.record("FAIL", f"未到达课程表展示页，屏幕={texts[:6]}")
        return t.finish()
    t.record("INFO", "已进入课程表展示页（原课表）")
    # 显示页工具栏添加按钮（第一个非返回图标，打开导入菜单）
    add_icon = None
    for _ in range(6):
        icons = top_bar_icons(t)
        if icons:
            add_icon = icons[0]   # 最左侧工具栏图标 = 导入菜单
            break
        time.sleep(1)
    if not add_icon:
        t.record("FAIL", "未找到课表页添加按钮")
        return t.finish()
    t.tap_xy(*add_icon[:2])
    time.sleep(1)
    texts = t.screen_text()
    if not any("图库导入" in x for x in texts):
        t.record("FAIL", f"未弹出导入菜单，屏幕={texts[:6]}")
        return t.finish()
    t.record("PASS", "图库导入入口可点击（拍照导入/图库导入菜单出现）")
    # 展示页弹窗文字是"图库导入课程表"（无"从"字，与空状态页不同）
    # 先启动看门狗：提示弹窗 + 照片权限弹窗需立即点击（8秒自动消失）
    t.start_watchdog(policy="allow", verbose=False)
    if not t.tap_text("图库导入课程表", wait=4):
        t.record("FAIL", "未找到'图库导入课程表'菜单项")
        t.stop_watchdog()
        return t.finish()
    time.sleep(2)

    # ── Step2: 选图 + 裁剪 + 触发解析 ──────────────────────────────
    t.step("Step2 选择固定课程表图片并完成导入流程")
    # 相册选择器 → OCR 定位课程表图片（按"课程表/学生"文字，全屏找，
    # 不限 y——缩略图可能在中上部；避免选到测试截图/其它图）
    thumb = None
    for _ in range(6):
        for x, y, c, tx in t.ocr(200, 1900):
            if "课表" in tx or "学生" in tx:
                thumb = (x, y)
                break
        if thumb:
            break
        time.sleep(1)
    if not thumb:
        # OCR 没找到文字缩略图：选相册里第一张可点击缩略图（中部区域）
        thumb = t.first_clickable(500, 1600)
    if not thumb:
        t.record("FAIL", "相册选择器未打开或无可选图片")
        return t.finish()
    t.record("INFO", f"选中图片: {thumb}")
    t.tap_xy(*thumb)
    time.sleep(2)
    texts = t.screen_text()
    if any("裁剪" in x for x in texts) or any("完成" in x for x in texts):
        t.record("PASS", "进入裁剪界面")
        # 点"完成"确认裁剪（裁剪页右上角；点击后进入解析）
        confirmed = False
        for attempt in range(10):
            if t.tap_text("完成", wait=2):
                time.sleep(2.5)
                texts = t.screen_text()
                if any("正在解析" in x for x in texts) or not any("左转" in x for x in texts):
                    confirmed = True
                    break
            time.sleep(1)
        if not confirmed:
            t.record("FAIL", "裁剪确认未生效（仍停留裁剪页）")
            return t.finish()
    else:
        t.record("FAIL", f"未进入裁剪界面，屏幕={texts[:6]}")
        return t.finish()

    # 看门狗持续处理弹窗；轮询等待解析结果（网络错误 或 确认页）。
    # 知识卡：解析约 20s 且需联网，轮询窗口必须给到 60s——
    # 原 20×0.75s≈15s 会在「正在解析课程表 90%」中途误判超时（170 曾因此只出 WARN）。
    parse_ok = None
    for _ in range(60):
        time.sleep(1)
        texts = t.screen_text()
        if any("无法连接网络" in x for x in texts):
            parse_ok = False
            break
        if any("下一步" in x for x in texts) or any("课程表名称" in x for x in texts):
            parse_ok = True
            break
    t.stop_watchdog()
    if parse_ok is False:
        t.record("FAIL", "解析未完成：无法连接网络（环境原因：设备无网络）")
        t.screenshot("01_网络错误")
        t.blocked("解析需要联网，设备无网络；无法验证新课表创建与原有课表保留")
        return t.finish()
    if parse_ok is True:
        t.record("PASS", "解析完成，进入确认流程")
    else:
        t.record("WARN", "未等到解析结果，屏幕=" + str(t.screen_text()[:6]))
        return t.finish()

    # ── Step3: 确认流程 → 验证新课表创建 + 原有课表保留 ─────────────
    t.step("Step3 确认导入并验证新课表创建且原课表未覆盖")
    # 确认流程：预览页"下一步" → 确认页"完成" → 回到课程表列表
    if not t.tap_text("下一步", wait=4):
        t.record("FAIL", "未找到预览页'下一步'按钮")
        return t.finish()
    time.sleep(2)
    if not t.tap_text("完成", wait=4):
        t.record("FAIL", "未找到确认页'完成'按钮")
        return t.finish()
    time.sleep(2)
    texts = t.screen_text()
    t.record("INFO", f"完成后列表: {texts[:10]}")
    has_original = any("原课表" in x for x in texts)
    t.record("PASS" if has_original else "FAIL",
             f"原有课程表'原课表'未被覆盖: {has_original}")
    new_created = any("学生课程表" in x for x in texts) \
        or len([x for x in texts if "课表" in x or "课程表" in x]) >= 2
    t.record("PASS" if new_created else "FAIL",
             f"导入创建了新的课程表（列表出现 ≥2 个课表）: {texts[:8]}")
    t.screenshot("02_课程表列表")

    return t.finish()


if __name__ == "__main__":
    run()
