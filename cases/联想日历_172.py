#!/usr/bin/env python3
"""联想日历_172 用例：图库导入课程表图片→解析→确认流程
导航知识: 主页 → 右上角"更多" → 弹窗"课程表"
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase


def run():
    t = TestCase("联想日历_172")

    # ── 前置条件检查（系统级：网络判断）────────────────────────────
    t.step("前置条件检查")
    if not t.has_network():
        t.record("FAIL", "设备无活动网络（解析课程表需要联网）")
        t.screenshot("00_网络状态")
        t.blocked("环境原因：设备无网络，解析无法完成，用例终止")
        return t.finish()
    t.record("PASS", "设备网络正常")

    # ── Step 1: 图库导入+裁剪+触发解析 ─────────────────────────────
    t.step("Step1 从图库选图并完成裁剪后触发解析")

    # 1a. 回到主页（App 重启保证干净状态）
    subprocess.run(["adb", "shell", "am", "force-stop", "com.zui.calendar"],
                   capture_output=True)
    time.sleep(1)
    subprocess.run(["adb", "shell", "monkey", "-p", "com.zui.calendar",
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    time.sleep(2.5)
    t.record("INFO", "App 已重新启动")

    # 1b. 主页 → 右上角"更多" → 课程表
    t.tap_xy(1732, 203)   # 更多
    time.sleep(1)
    if not any("课程表" in x for x in t.screen_text()):
        t.record("FAIL", "未弹出'更多'菜单（找不到'课程表'选项）")
    else:
        t.record("PASS", "更多菜单弹出，包含'课程表'入口")
    t.tap_text("课程表")
    time.sleep(1.5)
    t.record("INFO", f"进入课程表展示页: 前台={t.current_package()}")

    # 1c. 课程表页 → 图库导入课程表
    t.tap_xy(1645, 203)   # 工具栏导入按钮
    time.sleep(1)
    texts = t.screen_text()
    if not any("图库导入" in x for x in texts):
        t.record("FAIL", "未找到'图库导入课程表'菜单项")
    else:
        t.record("PASS", "导入菜单出现（拍照导入/图库导入）")
    t.tap_text("图库导入课程表")
    time.sleep(1.5)

    # 1d. 提示弹窗 → 知道了
    texts = t.screen_text()
    if any("知道了" in x for x in texts):
        t.tap_text("知道了")
        t.record("PASS", "图片清晰度提示弹窗正常，已点'知道了'")
        time.sleep(1.5)
    else:
        t.record("WARN", "未出现提示弹窗，继续")

    # 1e. 系统相册选择器 → 选图（动态定位缩略图）
    texts = t.screen_text()
    if any("照片" in x for x in texts) or any("相册" in x for x in texts):
        t.record("PASS", "系统相册选择器打开")
        t.screenshot("01_相册选择器")
        # 动态找缩略图区第一个可点击项（y 500-1600 区间，图片网格）
        thumb = None
        for attempt in range(3):
            thumb = t.first_clickable(500, 1600)
            if thumb:
                break
            time.sleep(1)
        if thumb:
            t.record("INFO", f"定位到第一张缩略图: {thumb}")
            t.tap_xy(*thumb)
            time.sleep(2)
        else:
            t.record("FAIL", "相册选择器中未找到可点击缩略图")
            t.blocked("无法选图，后续步骤无法执行")
            return t.finish()
    else:
        t.record("FAIL", "相册选择器未打开")
        t.screenshot("01_相册选择器异常")
        t.blocked("无法选图，后续步骤无法执行")
        return t.finish()

    # 1f. 裁剪界面 → 完成
    texts = t.screen_text()
    if any("裁剪" in x for x in texts):
        t.record("PASS", "进入裁剪界面")
        t.screenshot("02_裁剪界面")
        t.tap_xy(1772, 189)  # 完成
        time.sleep(3)
    else:
        t.record("FAIL", f"未进入裁剪界面，当前屏幕={texts[:5]}")
        t.blocked("裁剪未完成，无法触发解析")
        return t.finish()

    # 1g. 解析结果判断
    texts = t.screen_text()
    if any("无法连接网络" in x for x in texts):
        t.record("FAIL",
                 "解析未完成：弹出'无法连接网络'提示（环境原因：设备无网络）")
        t.screenshot("03_网络错误")
        t.record("INFO", "注：App 对无网络的异常处理符合预期（正确提示）")
        # 关闭错误弹窗
        if any("知道了" in x for x in texts):
            t.tap_text("知道了")
            time.sleep(1)
        # 后续步骤依赖解析成功 → 阻塞
        t.blocked("步骤1解析未完成（环境无网络），步骤2-5 无法执行")
        return t.finish()
    elif any("下一步" in x for x in texts):
        t.record("PASS", "解析流程正常完成，出现'下一步'按钮")
    else:
        t.record("FAIL", f"解析后状态未知，当前屏幕={texts[:6]}")
        t.screenshot("03_解析后状态")
        t.blocked("无法确认解析结果，后续步骤无法执行")
        return t.finish()

    # ── Step 2: 点击"下一步" ────────────────────────────────────────
    t.step("Step2 点击下一步")
    if t.tap_text("下一步") is None:
        t.record("FAIL", "未找到'下一步'按钮")
    else:
        time.sleep(1.5)
        texts = t.screen_text()
        t.record("PASS" if any("课程表名称" in x or "基本信息" in x for x in texts)
                 else "FAIL",
                 f"点击下一步后界面: {texts[:6]}")
        t.screenshot("04_确认页")

    # ── Step 3: 左上角返回并重新进入确认流程 ────────────────────────
    t.step("Step3 返回并重新进入确认流程")
    t.tap_xy(123, 203)   # 左上角返回
    time.sleep(1.5)
    t.record("INFO", f"返回后前台: {t.current_package()}")
    # 重新进入（重新走导入流程的确认页入口）
    t.tap_xy(1645, 203)
    time.sleep(1)
    if any("图库导入" in x for x in t.screen_text()):
        t.record("PASS", "返回操作正常，可重新进入导入流程")
        t.tap_text("图库导入课程表")
        time.sleep(1.5)
    else:
        t.record("FAIL", "返回后无法重新进入导入流程")
        t.blocked("步骤3未通过，后续无法执行")
        return t.finish()

    # ── Step 4: 基本信息页点完成 ────────────────────────────────────
    t.step("Step4 基本信息页面点击完成")
    texts = t.screen_text()
    if any("完成" in x for x in texts):
        t.tap_text("完成")
        time.sleep(2)
        t.record("INFO", f"点击完成后前台: {t.current_package()}")
    else:
        t.record("FAIL", "未找到'完成'按钮")

    # ── Step 5: 查看课程表列表 ──────────────────────────────────────
    t.step("Step5 查看课程表列表")
    texts = t.screen_text()
    if any("课程表" in x for x in texts):
        t.record("PASS", f"课程表列表可见: {texts[:6]}")
        t.screenshot("05_课程表列表")
        has_current = any("当前" in x for x in texts)
        t.record("PASS" if has_current else "FAIL",
                 f"列表中存在'当前'标签: {has_current}")
    else:
        t.record("FAIL", f"未进入课程表列表，屏幕={texts[:6]}")

    return t.finish()


if __name__ == "__main__":
    run()
