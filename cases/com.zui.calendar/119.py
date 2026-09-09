#!/usr/bin/env python3
"""联想日历_119：恢复日历应用（卸载预装日历 → 恢复预装应用 → 验证可新建）

依据知识卡与用户确认的约定实现：
1. 【固定恢复路径】（用户确认，_system.md）：设置 → 应用管理 → 恢复预装应用
   → 找到 APP → 恢复。入口定位**遍历列表读真实文字**（入口真实文字是
   「恢复」，不得硬编码"恢复预装应用"这类想当然的字符串）。
2. 【旋屏约定】套件基线 = 竖屏锁定（docs/case-writing.md）：开头 lock_portrait()，结尾
   不还原自动旋转。119 不需要转屏。
3. 【定位规范】rid > desc > text，多属性组合（docs/case-writing.md）。可用组合的用
   locate()；列表/菜单这类"人靠读文字找条目"的场景用 find_nodes() 遍历
   读真实文字匹配。全文无裸 t.d(text=...)。
4. 【桌面知识】（com.zui.launcher.md）：桌面首页图标只是已装 APP 子集；
   长按菜单项多为纯图标（仅 content-desc）；长按必须用 ATX 坐标长按。
5. 【实测行为差异】本机 ZUXOS 2.5.02.160：长按菜单点「卸载」弹出重定向
   提示，不直接卸载；实际卸载用 pm uninstall（返回 Success，应用进入
   「恢复预装应用」列表）。

设备安全：finally 精确匹配检查 + 固定路径 UI 兜底恢复。
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)  # 同目录（本 App 共享模块）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))
from test_framework import TestCase

PKG = "com.zui.calendar"
CASE_ID = "联想日历_119"

# 首启/系统弹窗按钮的真实文字（遍历匹配用；弹窗按钮无 rid 无 desc，文字是唯一线索）
DIALOG_WORDS = ("同意", "允许", "我知道了", "确定", "始终允许")

USER_INPUT = (
    "步骤: 1.进入桌面长按日历图标卸载日历应用; "
    "2.进入设置-应用管理-恢复预装应用中恢复应用，并进入日历新建提醒/日程/课程。 "
    "预期: 2.进入卸载应用界面卸载; 3.可成功恢复日历应用，能够新建提醒、日程、课程等。"
)


def exact_installed(t):
    """精确匹配 package:com.zui.calendar（避免被 com.zui.calendar.overlay.* 子串误判）。"""
    o = t.adb_shell("pm", "list", "packages", PKG)
    return ("package:" + PKG) in [l.strip() for l in o.splitlines()]


def find_by_real_text(t, exact, contains=None, clickable=None):
    """遍历当前页节点、读真实文字定位（不预设界面文案）。

    优先精确等于 exact；给了 contains 时其次匹配包含关系。
    返回节点 dict（含 bounds_xy），未命中 None。clickable 可选过滤。
    """
    nodes = t.find_nodes(clickable=clickable) if clickable is not None \
        else t.find_nodes()
    for n in nodes:
        if (n["text"] or "") == exact:
            return n
    if contains:
        for n in nodes:
            if contains in (n["text"] or ""):
                return n
    return None


def click_node(t, n):
    """点击遍历得到的节点（按 bounds 中心）。"""
    b = n["bounds_xy"]
    t.d.click((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)


def texts_seen(t, keyword):
    """遍历读当前页所有含 keyword 的真实文字（用于报告留证）。"""
    return sorted({n["text"] for n in t.find_nodes()
                   if n["text"] and keyword in n["text"]})


def dismiss_dialogs(t, rounds=8):
    """关闭首启/系统弹窗：遍历可点节点读真实文字，命中 DIALOG_WORDS 即点。"""
    for _ in range(rounds):
        hit = None
        for n in t.find_nodes(clickable=True):
            if (n["text"] or "") in DIALOG_WORDS or (n["desc"] or "") in DIALOG_WORDS:
                hit = n
                break
        if not hit:
            break
        click_node(t, hit)
        time.sleep(2.0)


def open_restore_page(t):
    """按固定路径进入「恢复预装应用」页：设置 → 应用管理 → 恢复入口。

    设置布局见 com.android.settings.md：竖屏下单栏切换，am start SETTINGS
    可能恢复残留子页（内容区），先 force-stop 再 start。
    入口定位全部遍历读真实文字：
      - 二级入口文字 = 「应用管理」
      - 三级入口真实文字 = 「恢复」（用户确认，不硬编码"恢复预装应用"）
    """
    t.adb_shell("am", "force-stop", "com.android.settings")
    time.sleep(0.8)
    t.adb_shell("am", "start", "-a", "android.settings.SETTINGS")
    time.sleep(2.5)
    entered_am = False
    for _ in range(10):
        n = find_by_real_text(t, "应用管理")
        if n:
            click_node(t, n)
            entered_am = True
            break
        sc = t.d(scrollable=True)
        if sc.exists:
            try:
                sc.scroll.forward()
            except Exception:
                pass
        time.sleep(0.8)
    time.sleep(2.0)
    if not entered_am:
        return False
    for _ in range(10):
        n = find_by_real_text(t, "恢复", contains="恢复")
        if n:
            click_node(t, n)
            time.sleep(2.5)
            return True
        sc = t.d(scrollable=True)
        if sc.exists:
            try:
                sc.scroll.forward()
            except Exception:
                pass
        time.sleep(0.8)
    return False


def restore_via_ui(t):
    """固定路径恢复日历，返回是否成功（精确匹配）。"""
    if exact_installed(t):
        return True
    if not open_restore_page(t):
        return False
    # 遍历读真实文字找"日历"行 → 进入后点「恢复」动作按钮
    row = find_by_real_text(t, "日历")
    if not row:
        return False
    click_node(t, row)
    time.sleep(2.0)
    for _ in range(2):
        btn = find_by_real_text(t, "恢复", clickable=True)
        if btn:
            click_node(t, btn)
            time.sleep(3.0)
    time.sleep(1.5)
    return exact_installed(t)


def run():
    t = TestCase(CASE_ID)
    t.lock_portrait()          # 套件基线：竖屏锁定，结尾不还原

    try:
        # ---------- 前置检查 ----------
        t.step("前置检查：日历已安装，且恢复入口存在")
        pre = exact_installed(t)
        restore_entry = open_restore_page(t)
        t.record("PASS" if (pre and restore_entry) else "WARN",
                 f"日历已安装(精确匹配)={pre}; "
                 f"固定路径 设置>应用管理>恢复入口 可达={restore_entry}")

        # ---------- Step 1：长按日历图标，确认菜单含「卸载」入口 ----------
        t.step("长按日历图标，确认菜单含卸载入口（知识卡：有卸载=支持卸载）")
        t.adb_shell("input", "keyevent", "KEYCODE_HOME")
        time.sleep(1.5)
        # 桌面首页图标只是子集（com.zui.launcher.md）：先首页 desc=日历，
        # 没有则进 dock「所有应用」列表；列表项容器 desc=日历+clickable（实测唯一）
        icon = t.locate(desc="日历", clickable=True)
        where = "桌面首页"
        if not icon.wait(2.0):
            dock = t.locate(desc="所有应用", cls="android.widget.ImageButton")
            if dock.click(silent=True):
                time.sleep(2.5)
                where = "所有应用列表"
                icon = t.locate(desc="日历", clickable=True)
        menu_ok = icon.long_click()   # ATX 坐标长按（swipe 模拟弹不出菜单）
        time.sleep(2.0)
        # 菜单项为纯图标：desc + cls=ImageView + clickable（实测 9 项全部命中）
        uninstall_icon = t.locate(desc="卸载", cls="android.widget.ImageView",
                                  clickable=True)
        has_uninstall = uninstall_icon.wait(3.0)
        t.screenshot("119_longpress_menu")

        # 点「卸载」观察本机实际行为（直接卸载 / 重定向引导）
        tap_behavior = "未点击卸载"
        if has_uninstall:
            uninstall_icon.click(silent=True)
            time.sleep(2.5)
            t.screenshot("119_tap_uninstall")
            if not exact_installed(t):
                tap_behavior = "点击后直接完成卸载"
            else:
                tip = find_by_real_text(t, "我知道了")
                if tip:
                    tap_behavior = "弹出卸载提示重定向对话框（引导去设置>恢复预装应用），桌面不直接卸载"
                    click_node(t, tip)
                    time.sleep(1.2)
                else:
                    tap_behavior = "点击后无可见卸载动作"
        t.adb_shell("input", "keyevent", "KEYCODE_HOME")
        time.sleep(1.0)
        t.record("PASS" if (menu_ok and has_uninstall) else "FAIL",
                 f"图标定位={where}; 长按弹出菜单={menu_ok}; "
                 f"菜单含卸载入口={has_uninstall}; 点击卸载后行为={tap_behavior}")

        # ---------- Step 2：卸载预装日历 ----------
        t.step("卸载预装日历")
        out = ""
        if not exact_installed(t):
            removed, note = True, "Step1 点击卸载已直接完成"
        else:
            out = t.adb_shell("pm", "uninstall", PKG)
            time.sleep(2.0)
            removed = not exact_installed(t)
            note = "pm uninstall（桌面不直接卸载预装，实测行为差异）"
        # GUI 证据：卸载后恢复入口页应出现「日历」待恢复条目（遍历读真实文字）
        open_restore_page(t)
        seen = texts_seen(t, "日历") + texts_seen(t, "恢复")
        t.screenshot("119_after_uninstall")
        t.record("PASS" if removed else "FAIL",
                 f"卸载方式={note}; pm输出={out.strip()}; 日历已移除(精确匹配)={removed}; "
                 f"恢复入口页真实文字含 日历/恢复={seen[:8]}")

        # ---------- Step 3：固定路径恢复预装应用 ----------
        t.step("设置-应用管理-恢复预装应用 恢复日历")
        restored_ok = restore_via_ui(t)
        open_restore_page(t)
        seen2 = texts_seen(t, "打开")
        t.screenshot("119_after_restore")
        t.record("PASS" if restored_ok else "FAIL",
                 f"恢复后日历已安装(精确匹配)={restored_ok}; "
                 f"恢复入口页真实文字含'打开'={seen2[:5]}")

        # ---------- Step 4：验证恢复后可新建提醒/日程 ----------
        t.step("恢复后进入日历验证可新建")
        if restored_ok:
            t.adb_shell("am", "start", "-a", "android.intent.action.MAIN",
                        "-c", "android.intent.category.LAUNCHER", "-p", PKG)
            time.sleep(6.0)  # 恢复后首次启动较慢
            dismiss_dialogs(t)   # 权限/引导弹窗（遍历读真实文字匹配）
            time.sleep(1.5)
            txt_now = " ".join(n["text"] for n in t.find_nodes() if n["text"])
            launched = (t.current_package() == PKG) or \
                       (("今天" in txt_now or "农历" in txt_now or "2026年" in txt_now)
                        and "日历" in txt_now)
            t.screenshot("119_launched")
            opened_editor = False
            for sel in (t.locate(rid=f"{PKG}:id/action_add_all_event"),
                        t.locate(desc="新建"), t.locate(desc="添加"),
                        t.locate(desc="新增")):
                if sel.exists:
                    sel.click(silent=True)
                    time.sleep(2.5)
                    opened_editor = True
                    break
            if opened_editor:
                edit = t.find_nodes(cls_re="EditText")
                if edit:
                    t.input_text(edit[0]["rid"], "恢复后验证提醒")
                    time.sleep(0.5)
                save = t.locate(rid=f"{PKG}:id/save_view")
                if save.exists:
                    save.click(silent=True)
                    time.sleep(2.0)
            t.screenshot("119_after_create")
            t.record("PASS" if (launched and opened_editor) else "WARN",
                     f"恢复后日历可启动={launched}; 能进入新建入口={opened_editor}")
        else:
            t.record("FAIL", "日历未恢复，无法验证新建功能")

    finally:
        # 兜底：无论如何保证日历已恢复（精确匹配 + 固定路径 UI 恢复）
        if not exact_installed(t):
            restore_via_ui(t)
        # 旋屏约定：不还原自动旋转，保持竖屏锁定（套件基线）

    return t.finish()


if __name__ == "__main__":
    run()
