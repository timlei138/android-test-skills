#!/usr/bin/env python3
"""联想日历_169 用例：相机/图库权限弹窗（允许与拒绝两条路径）
前提: 未授予相机/图库权限 + 无课程表（pm clear）
"""
import os
import re
import subprocess
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试联想日历 169 号用例。
前提：1.日历未授予相机/图库权限 2.设备无课程表（清理APP数据）
操作步骤：
1. 日历点击页面右上角更多按钮，选择课程表，点击"拍照导入课程表"按钮
2. 返回课程表设置页，点击"从图库导入课程表"按钮
预期结果：
1. 弹出权限弹窗，选择允许后打开相机即可，选择拒绝后提示需要授予相机权限
2. 弹出权限弹窗，可选择部分照片或全部允许，选择后可进入照片选择界面，选择拒绝后提示需要授予相册权限"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_framework import TestCase

PKG = "com.zui.calendar"


def goto_course_table(t):
    """从任意页面导航回课程表空状态页（相机/相册返回后 App 会退到主界面）
    带重试：返回动画/权限变更异步期间图标可能短暂不可见。"""
    import subprocess as _sp
    act = t.current_activity()
    if "TimetableActivity" in act and "crop" not in act:
        # 已在课程表页（可能是展示页），看是否有导入按钮
        if any("拍照导入" in x for x in t.screen_text()):
            return True
    # 回到主界面（多按几次返回，等稳定）
    for _ in range(3):
        act = t.current_activity()
        if "AllInOne" in act:
            break
        _sp.run(["adb", "shell", "input", "keyevent", "KEYCODE_BACK"],
                capture_output=True)
        time.sleep(1)
    # 等主界面工具栏图标出现（最多 8s，覆盖返回动画/权限变更异步）
    rm = None
    for _ in range(8):
        rm = t.top_rightmost_icon()
        if rm:
            break
        time.sleep(1)
    if not rm:
        return False
    # 更多 → 课程表（点击后重试判断，菜单弹出有动画）
    for attempt in range(3):
        t.tap_xy(*rm)
        time.sleep(1.2)
        if any("课程表" in x for x in t.screen_text()):
            break
        time.sleep(0.8)
    else:
        return False
    t.tap_text("课程表")
    for _ in range(10):
        if any("拍照导入" in x for x in t.screen_text()):
            return True
        time.sleep(0.6)
    return False


def run():
    t = TestCase("联想日历_169")

    # ── 前提：清理（重置权限 + 清空课程表）────────────────────────
    t.step("前提-清理应用数据")
    t.pm_clear(PKG)
    time.sleep(1)

    # ── 导航到课程表空状态页（首启用顺序式处理，更可靠）────────────
    t.step("导航-进入课程表页")
    subprocess.run(["adb", "shell", "monkey", "-p", PKG,
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    ok = t.dismiss_first_use_dialogs(policy="allow", max_rounds=15, verbose=True)
    if not ok:
        t.record("WARN", "首启弹窗可能未处理完")
    # 等主界面出现
    rightmost = None
    for _ in range(10):
        rightmost = t.top_rightmost_icon()
        if rightmost:
            break
        time.sleep(1)
    if not rightmost:
        t.record("FAIL", "未找到'更多'按钮")
        return t.finish()
    t.tap_xy(*rightmost)
    time.sleep(1)
    if not any("课程表" in x for x in t.screen_text()):
        t.record("FAIL", "更多菜单未出现'课程表'")
        return t.finish()
    t.tap_text("课程表")
    time.sleep(2)
    t.record("INFO", "已进入课程表页")
    # 测试步骤的权限弹窗用看门狗处理
    t.start_watchdog(policy="allow", verbose=False)

    # 课程表页应显示导入按钮（空状态）
    texts = t.screen_text()
    if not any("拍照导入" in x for x in texts) or not any("图库导入" in x for x in texts):
        t.record("FAIL", f"课程表页未显示导入按钮，屏幕={texts[:6]}")
        t.stop_watchdog()
        return t.finish()
    t.record("PASS", "课程表空状态页显示 拍照导入/从图库导入 按钮")

    # ── Step1a: 拍照导入-允许（先允许，避免 USER_FIXED 后无法重询）──
    t.step("Step1a 拍照导入-允许后打开相机")
    t.watchdog_policy("allow")
    t.tap_text("拍照导入课程表")
    # 看门狗处理弹窗，轮询等相机打开（最长 10s）
    act = ""
    for _ in range(14):
        time.sleep(0.7)
        act = t.current_activity()
        if "camera" in act.lower():
            break
    camera_open = "camera" in act.lower() or "zui.camera" in act.lower()
    t.record("PASS" if camera_open else "FAIL",
             f"允许后相机打开: {act}")
    t.screenshot("01_相机打开")
    # 返回课程表页
    subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_BACK"],
                   capture_output=True)
    time.sleep(2)

    # ── Step1b: 拍照导入-拒绝（revoke 重置恢复"未授予"前提）────────
    t.step("Step1b 拍照导入-拒绝相机权限")
    t.adb_shell("pm", "revoke", PKG, "android.permission.CAMERA")
    t.record("INFO", "已 pm revoke CAMERA（重置前提，非绕过弹窗）")
    if not goto_course_table(t):
        t.record("FAIL", "重新导航到课程表页失败")
        t.stop_watchdog()
        return t.finish()
    t.watchdog_policy("deny")
    t.tap_text("拍照导入课程表")
    time.sleep(2.5)   # 看门狗处理: 知道了 + 拒绝
    texts = t.screen_text()
    denied_prompt = any("相机权限" in x for x in texts) or any("前往设置" in x for x in texts)
    t.record("PASS" if denied_prompt else "FAIL",
             f"拒绝后提示需要授予相机权限: {texts[:5]}")
    t.screenshot("02_拍照拒绝提示")
    if any("取消" in x for x in texts):
        t.tap_text("取消")
        time.sleep(1)

    # ── Step2a: 图库导入-允许 ──────────────────────────────────────
    t.step("Step2a 图库导入-允许后进入照片选择")
    t.watchdog_policy("allow")
    t.tap_text("从图库导入课程表")
    time.sleep(3)     # 看门狗处理: 知道了 + 全部允许
    act = t.current_activity()
    picker_open = "photopicker" in act.lower() or "PhotoPicker" in act
    t.record("PASS" if picker_open else "FAIL",
             f"允许后进入照片选择界面: {act}")
    t.screenshot("03_照片选择界面")

    # ── Step2b: 图库导入-拒绝（revoke 重置）────────────────────────
    t.step("Step2b 图库导入-拒绝相册权限")
    # 先 revoke 重置权限（权限变更可能触发 App 刷新界面，故先重置再导航）
    t.adb_shell("pm", "revoke", PKG, "android.permission.READ_MEDIA_IMAGES")
    t.adb_shell("pm", "revoke", PKG, "android.permission.READ_MEDIA_VISUAL_USER_SELECTED")
    t.record("INFO", "已 pm revoke 相册权限（重置前提）")
    time.sleep(1)
    # 再返回课程表页
    if not goto_course_table(t):
        t.record("FAIL", "重新导航到课程表页失败")
        t.stop_watchdog()
        return t.finish()
    t.watchdog_policy("deny")
    t.tap_text("从图库导入课程表")
    time.sleep(3)     # 看门狗处理: 知道了 + 拒绝
    texts = t.screen_text()
    denied_prompt = any("权限" in x for x in texts) and any("前往设置" in x for x in texts)
    t.record("PASS" if denied_prompt else "FAIL",
             f"拒绝后提示需要授予相册权限: {texts[:5]}")
    t.screenshot("04_图库拒绝提示")

    t.stop_watchdog()
    return t.finish()


if __name__ == "__main__":
    run()
