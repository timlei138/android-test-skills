#!/usr/bin/env python3
"""169 权限测试共享辅助：导航 + 相机/图库 允许/拒绝 单行为测试
拆分原因：Android 权限拒绝后 USER_FIXED，同轮多次测会级联失败；每个行为独立干净环境最可靠
"""
import os
import subprocess
import sys
import time

PKG = "com.zui.calendar"


def navigate_to_course_table(t):
    """启动 App + 处理首启弹窗 + 导航到课程表空状态页"""
    # 确保干净前台：杀掉日历和相机（上个用例可能停在相机）
    subprocess.run(["adb", "shell", "am", "force-stop", PKG], capture_output=True)
    subprocess.run(["adb", "shell", "am", "force-stop", "com.zui.camera"],
                   capture_output=True)
    time.sleep(1)
    subprocess.run(["adb", "shell", "monkey", "-p", PKG,
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    # 等 App 就绪（有弹窗或有主界面工具栏），最多 10s
    for _ in range(10):
        texts = t.screen_text()
        if any(w in " ".join(texts) for w in ("同意", "允许", "我知道了", "知道了")):
            break
        if t.top_rightmost_icon():
            break
        time.sleep(1)
    t.dismiss_first_use_dialogs(policy="allow", max_rounds=15, verbose=True)
    rightmost = None
    for _ in range(10):
        rightmost = t.top_rightmost_icon()
        if rightmost:
            break
        time.sleep(1)
    if not rightmost:
        print("[导航] 未找到'更多'按钮, 屏幕:", t.screen_text()[:6])
        return False
    t.tap_xy(*rightmost)
    time.sleep(1)
    if not any("课程表" in x for x in t.screen_text()):
        print("[导航] 更多菜单无'课程表', 屏幕:", t.screen_text()[:6])
        return False
    t.tap_text("课程表")
    for _ in range(10):
        if any("拍照导入" in x for x in t.screen_text()):
            return True
        time.sleep(0.6)
    return False


def test_camera(t, allow):
    """相机权限: allow=True 期望相机打开; allow=False 期望提示授予权限"""
    t.watchdog_policy("allow" if allow else "deny")
    t.tap_text("拍照导入课程表")
    if allow:
        act = ""
        for _ in range(14):
            time.sleep(0.7)
            act = t.current_activity()
            if "camera" in act.lower():
                break
        ok = "camera" in act.lower() or "zui.camera" in act.lower()
        t.record("PASS" if ok else "FAIL",
                 f"允许后相机打开: {act}")
        t.screenshot("相机_允许")
    else:
        time.sleep(3)
        texts = t.screen_text()
        denied = any("相机权限" in x for x in texts) or any("前往设置" in x for x in texts)
        t.record("PASS" if denied else "FAIL",
                 f"拒绝后提示需要授予相机权限: {texts[:5]}")
        t.screenshot("相机_拒绝")


def test_gallery(t, allow):
    """图库权限: allow=True 期望进入照片选择; allow=False 期望提示授予权限"""
    t.watchdog_policy("allow" if allow else "deny")
    t.tap_text("从图库导入课程表")
    if allow:
        act = ""
        for _ in range(14):
            time.sleep(0.7)
            act = t.current_activity()
            if "photopicker" in act.lower():
                break
        ok = "photopicker" in act.lower() or "PhotoPicker" in act
        t.record("PASS" if ok else "FAIL",
                 f"允许后进入照片选择界面: {act}")
        t.screenshot("图库_允许")
    else:
        time.sleep(3)
        texts = t.screen_text()
        denied = any("权限" in x for x in texts) and any("前往设置" in x for x in texts)
        t.record("PASS" if denied else "FAIL",
                 f"拒绝后提示需要授予相册权限: {texts[:5]}")
        t.screenshot("图库_拒绝")
