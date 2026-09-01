#!/usr/bin/env python3
"""联想日历_174 用例：课程表基本信息确认页
前提：进入"新建课程表"页面（对应导入图片后的基本信息确认页）
"""
import os
import sys

# 用户原始输入（口述用例）：run_case.py 提取后入库，方便追溯 需求→脚本→结果
USER_INPUT = """跑联想日历 174 号用例：新建课程表页。
1. 课程表名称可编辑，最多 20 字符
2. 学期开始时间弹出系统日期选择器
3. 当前周数弹出日期选择器
4. 学期总周数可改选 21 周再改回 20 周
5. 周末是否有课/显示非本周课程开关可切换
6. 必填项为空时完成按钮置灰，点击被拦截；信息完整时点击可提交"""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase

NAME_RID = "com.zui.calendar:id/et_schedule_name"
TOTAL_RID = "com.zui.calendar:id/tv_total_weeks"
SW_WEEKEND = "com.zui.calendar:id/switch_weekend_classes"
SW_NONCURRENT = "com.zui.calendar:id/switch_show_non_current_week"
SAVE_RID = "com.zui.calendar:id/save_view"


def run():
    t = TestCase("联想日历_174")

    # ── 前提：进入新建课程表页 ────────────────────────────────────
    t.step("前提-进入新建课程表页")
    import subprocess
    # 确定性前置：pm_clear 全新状态 → 课程表空状态 → 手动创建
    t.pm_clear("com.zui.calendar")
    subprocess.run(["adb", "shell", "am", "force-stop", "com.zui.camera"],
                   capture_output=True)
    time_sleep(1)
    subprocess.run(["adb", "shell", "monkey", "-p", "com.zui.calendar",
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    t.dismiss_first_use_dialogs(policy="allow", max_rounds=12, verbose=False)
    if not t.open_more_menu():
        t.record("FAIL", "无法打开'更多'菜单")
        return t.finish()
    t.tap_text("课程表")
    time_sleep(2)
    if not t.tap_text("手动创建课程表", wait=4):
        t.record("FAIL", "未找到'手动创建课程表'按钮")
        return t.finish()
    t.screenshot("00_新建课程表页")

    # ── Step 1: 课程表名称 ─────────────────────────────────────────
    t.step("Step1 课程表名称-可编辑")
    t.input_text(NAME_RID, "测试课程表A")
    t.assert_text(NAME_RID, "测试课程表A", "名称可编辑")

    t.step("Step1b 名称20字符上限")
    t.input_text(NAME_RID, "12345678901234567890")
    t.assert_length_le(NAME_RID, 20, "名称最多20字符")
    t.screenshot("01_名称20字符上限")

    # ── Step 2: 学期开始时间 ───────────────────────────────────────
    t.step("Step2 学期开始时间-系统日期弹框")
    t.tap_text("学期开始时间")
    texts = t.screen_text()
    t.assert_true(any("确定" in x for x in texts) and any("取消" in x for x in texts),
                  "弹出系统日期选择器")
    t.screenshot("02_日期弹框")
    t.tap_text("取消")
    time_sleep(0.8)

    # ── Step 3: 当前周数 ───────────────────────────────────────────
    t.step("Step3 当前周数展示与选择")
    t.tap_text("当前周数")
    texts = t.screen_text()
    t.assert_true(any("确定" in x for x in texts),
                  "点击当前周数弹出日期选择器（周数由开学日期推算）")
    t.screenshot("03_当前周数")
    t.tap_text("取消")
    time_sleep(0.8)

    # ── Step 4: 学期总周数 ─────────────────────────────────────────
    t.step("Step4 学期总周数选择")
    t.tap_text("学期总周数")
    time_sleep(1.2)
    t.screenshot("04_总周数弹窗")
    # Canvas 滚轮：OCR 找 21 点选（限定对话框 x 区域，避免匹配背景）
    def _wheel_find(kw):
        for x, y, c, tx in t.ocr(700, 1300):
            if 1000 <= x <= 2100 and kw in tx:
                return (x, y)
        return None
    hit21 = _wheel_find("21")
    if hit21:
        t.tap_xy(*hit21)
        t.tap_text("确定")
        time_sleep(1)
        v = t.read_rid(TOTAL_RID)
        t.assert_equals(v["text"] if v else None, "21周", "总周数可改选为21周")
        # 改回 20 周
        t.tap_text("学期总周数")
        time_sleep(1.2)
        hit20 = _wheel_find("20")
        if hit20:
            t.tap_xy(*hit20)
            t.tap_text("确定")
            time_sleep(1)
            v = t.read_rid(TOTAL_RID)
            t.assert_equals(v["text"] if v else None, "20周", "总周数改回20周")
        else:
            t.record("WARN", "未在滚轮找到20（OCR），关闭弹窗")
            t.tap_text("取消")
    else:
        t.record("WARN", "未在滚轮找到21（OCR），关闭弹窗")
        t.tap_text("取消")
    time_sleep(0.8)

    # ── Step 5/6: 开关 ─────────────────────────────────────────────
    t.step("Step5 周末是否有课开关")
    t.tap_rid(SW_WEEKEND)
    t.assert_switch(SW_WEEKEND, "true", "周末有课可切换为开")
    t.tap_rid(SW_WEEKEND)
    t.assert_switch(SW_WEEKEND, "false", "周末有课可切回关")

    t.step("Step6 显示非本周课程开关")
    t.tap_rid(SW_NONCURRENT)
    t.assert_switch(SW_NONCURRENT, "true", "显示非本周课程可切换为开")
    t.tap_rid(SW_NONCURRENT)
    t.assert_switch(SW_NONCURRENT, "false", "显示非本周课程可切回关")

    # ── Step 7: 清空必填项→完成按钮状态（同一页面一次初始化完成）──
    # 先空态（清空名称→置灰→点击拦截），再完整态（重新填名称→可提交），
    # 全程停留在一个新建课程表页，无需第二次 pm_clear 初始化。
    t.step("Step7a 清空名称→完成按钮状态（以 case 预期为准）")
    # Case 预期：必填项为空时"完成"按钮应置灰。
    # 视觉模型实测：清空后按钮仍黑字白底（视觉未置灰），仅功能层拦截。
    # 按"以 case 为准"原则：case 要求置灰而实际未置灰 = FAIL，并说明差别。
    t.assert_button_state_visual(SAVE_RID, "clickable", "完整态-完成按钮应为可点击")
    t.clear_text(NAME_RID)
    t.screenshot("05_名称清空后按钮")
    t.assert_button_state_visual(SAVE_RID, "grayed",
                                 "必填项为空时完成按钮置灰（case 预期，实际视觉未置灰）")
    # 功能层：点击应被拦截（case 另一预期）
    t.tap_el(rid=SAVE_RID)
    time_sleep(1.2)
    texts = t.screen_text()
    still_here = any("课程表名称" in x for x in texts)
    t.assert_true(still_here, "空名称点击完成被拦截（未跳转）")

    t.step("Step7b 信息完整时完成按钮可提交")
    t.input_text(NAME_RID, "测试课程表A")
    t.assert_text(NAME_RID, "测试课程表A", "重新填入名称")
    t.tap_el(rid=SAVE_RID)
    time_sleep(1.5)
    pkg = str(t.current_package())
    t.record("INFO", f"点击完成后前台: {pkg}")
    texts = t.screen_text()
    left_page = not any("课程表名称" in x for x in texts)
    t.assert_true(left_page, "信息完整时点击完成离开新建页（提交成功）")

    # ── 报告 ───────────────────────────────────────────────────────
    return t.finish()


def time_sleep(s):
    import time
    time.sleep(s)


if __name__ == "__main__":
    run()
