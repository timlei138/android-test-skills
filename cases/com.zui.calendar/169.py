#!/usr/bin/env python3
"""联想日历_169 用例：相机/图库权限弹窗（拒绝与允许两条路径）

排布原则（按权限分组、组内先拒绝后允许）：
  A 组 CAMERA: Step1 拒绝 → Step2 允许（同一权限连续测完再换下一个）
  B 组 MEDIA : Step3 拒绝 → Step4 允许
这样同一权限的两条路径共用一次导航，且"先拒绝后允许"符合系统弹窗的实际形态变化
（拒绝过一次后按钮由「拒绝」变为「拒绝并不再询问」，见下方说明）。

两类特殊权限的真实形态（2026-09-02 TB323FU 实测，见 knowledge/com.zui.calendar.md）：
  CAMERA 首次: 仅在使用时允许 / 仅本次使用时允许 / 拒绝
  CAMERA 拒绝后再请求: 仅在使用时允许 / 仅本次使用时允许 / 拒绝并不再询问
  MEDIA  首次: 选择照片 / 全部允许 / 拒绝
  MEDIA  拒绝后再请求: 选择照片 / 全部允许 / 拒绝并不再询问
→ 「拒绝」按钮文案会变成「拒绝并不再询问」，用 tap_text("拒绝") 精确匹配会失配，
  故本用例统一用 tap_re() 正则点击。
"""
import os
import re
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase
from _flow import goto_课程表空状态

PKG = "com.zui.calendar"

# 允许类按钮（按优先级取第一个命中的）。注意 CAMERA 的"仅在使用时允许"含"允许"，
# 故用正则整体匹配，不要按子串顺序乱点。
RE_ALLOW = r"^(仅在使用时允许|仅本次使用时允许|全部允许|选择照片|允许)$"
# 拒绝类按钮：覆盖"拒绝"与二次出现的"拒绝并不再询问"
RE_DENY = r"^拒绝(并不再询问)?$"
# App 内提示框的确认按钮
RE_HINT_OK = r"^知道了$"


def tap_re(t, pattern, timeout=8):
    """按正则点击（薄封装，直接透传框架 tap_text_re）。

    不用 tap_text 的原因：系统「拒绝」按钮在拒绝过一次后会变成「拒绝并不再询问」，
    精确匹配必然失配 —— 详见 knowledge/_system.md「运行时权限弹窗」。
    """
    return t.tap_text_re(pattern, timeout=timeout, clickable=True)


def tap_re_once(t, pattern):
    """点一次（不轮询），用于已经确认弹窗在屏上时。"""
    return t.tap_text_re(pattern, timeout=1.5, clickable=True)


def wait_activity_lower(t, key, timeout=25):
    """等前台 Activity 名包含 key（小写比较），返回最终 Activity。"""
    deadline = time.time() + timeout
    act = ""
    while time.time() < deadline:
        act = t.current_activity()
        if key in act.lower():
            return act
        time.sleep(0.6)
    return act


def wait_texts(t, keys, timeout=10):
    """等屏幕出现任一关键词，返回 (是否命中, 文本列表)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        texts = t.screen_text()
        if any(k in x for x in texts if x for k in keys):
            return True, texts
        time.sleep(0.4)
    return False, t.screen_text()


def import_and_handle(t, btn_text, allow, target_key, timeout=30):
    """点导入按钮 → 关 App 内提示框 → 按 allow 处理系统权限弹窗 → 等目标界面。

    知识卡「导入弹窗顺序」：点导入 → ①App 内提示框「请确保图片清晰、完整」
    （无条件弹、不会自动消失，不关则后面什么都不发生）
    → ②系统权限弹窗（仅当权限未授予时弹；已授予则跳过本步）。

    allow=True  → 点允许类按钮（CAMERA:仅在使用时允许 / MEDIA:全部允许）
    allow=False → 点拒绝类按钮（"拒绝" 或二次的 "拒绝并不再询问"）
    target_key  : 成功目标 Activity 关键字（camera / photopicker）

    返回 (是否到达目标, 最终 Activity, 权限按钮实际文案)
    """
    if not t.tap_text(btn_text, wait=5, silent=True):
        return False, f"未找到导入按钮 {btn_text!r}", ""

    # ① 关 App 内提示框（无条件弹，必须关）
    hint = tap_re(t, RE_HINT_OK, timeout=6)
    if not hint:
        # 可能已被看门狗关掉，或提示框未出现；继续按流程走
        pass

    # ② 系统权限弹窗：只在权限未授予时出现，点对应按钮
    btn = tap_re(t, RE_ALLOW if allow else RE_DENY, timeout=8)

    # ③ 等目标界面
    act = wait_activity_lower(t, target_key, timeout=timeout)
    return target_key in act.lower(), act, btn


def on_course_table(t):
    """当前是否在课程表空状态页。"""
    texts = t.screen_text()
    return any("拍照导入" in x for x in texts if x) \
        and any("图库导入" in x for x in texts if x)


def to_course_table(t, timeout=20):
    """从任意页面回到课程表空状态页。

    难点：从相机返回时日历可能停留在主界面（AllInOneActivity），而不是课程表页，
    单纯按返回键到不了；这里在超时后重新走「更多 → 课程表」导航。
    """
    # 先退后进：连按返回键，期间一旦回到课程表页就停
    deadline = time.time() + timeout
    while time.time() < deadline:
        if on_course_table(t):
            return True
        act = t.current_activity()
        # 在相机里 → 直接杀掉进程更快更稳（相机返回键可能被取景器吃掉）
        if "camera" in act.lower():
            t.force_stop("com.zui.camera")
            time.sleep(1.2)
            continue
        t.adb_shell("input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.2)
    if on_course_table(t):
        return True

    # 兜底：重新导航「更多 → 课程表」（此时日历应在主界面或课程表页）
    t.launch_app(PKG)
    for _ in range(10):
        time.sleep(1)
        if on_course_table(t):
            return True
        # 主界面：点「更多」→「课程表」
        b = t.el_bounds(rid="com.zui.calendar:id/iv_more")
        if b:
            t.tap_xy((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
            time.sleep(1.2)
            t.tap_text("课程表", wait=2, silent=True)
            time.sleep(2)
    return on_course_table(t)


def run():
    t = TestCase("联想日历_169")

    # ── 前提：清理数据（权限与课表一并重置到首次使用）────────────────
    t.step("前提-清理应用数据")
    t.pm_clear(PKG)
    time.sleep(1.5)
    t.force_stop("com.zui.camera")
    t.record("INFO", "已 pm clear（重置权限与课程表），并停止相机进程")

    # ── 导航到课程表空状态页（过首启弹窗）──────────────────────────
    t.step("导航-进入课程表页")
    if not goto_课程表空状态(t, pm_clear=False):
        t.record("BLOCKED", "未能进入课程表空状态页")
        return t.finish()
    texts = t.screen_text()
    if not (any("拍照导入" in x for x in texts) and any("图库导入" in x for x in texts)):
        t.record("FAIL", f"课程表页未显示导入按钮，屏幕={texts[:6]}")
        return t.finish()
    t.record("PASS", "课程表空状态页显示 拍照导入/从图库导入 按钮")

    # ══ A 组：CAMERA ═══════════════════════════════════════════════
    # ── Step1 拍照导入-拒绝（先拒绝，符合系统弹窗形态变化顺序）────────
    t.step("Step1 拍照导入-拒绝相机权限")
    ok, act, btn = import_and_handle(t, "拍照导入课程表", allow=False,
                                     target_key="camera", timeout=8)
    if ok:
        t.record("FAIL", f"拒绝后不应打开相机，却进入了 {act}")
    else:
        # 期望：提示需要授予相机权限
        hit, texts = wait_texts(t, ["相机权限", "需要权限", "前往设置"], 8)
        t.record("PASS" if hit else "FAIL",
                 f"拒绝后提示需要授予相机权限（点到的按钮={btn or '无'}）: {texts[:5]}")
    t.screenshot("01_拍照拒绝提示")
    # 关掉 App 的「需要权限」提示框
    for w in ("取消", "知道了"):
        if any(w in x for x in t.screen_text() if x):
            t.tap_text(w, wait=2, silent=True)
            break
    time.sleep(1)

    # ── Step2 拍照导入-允许（同一权限紧接着测允许，共用一次导航）──────
    t.step("Step2 拍照导入-允许后打开相机")
    if not to_course_table(t):
        t.record("FAIL", "未回到课程表页，无法继续相机允许路径")
        return t.finish()
    ok, act, btn = import_and_handle(t, "拍照导入课程表", allow=True,
                                     target_key="camera", timeout=25)
    t.record("PASS" if ok else "FAIL",
             f"允许后相机打开（点到的按钮={btn or '无'}）: {act}")
    t.screenshot("02_相机打开")
    if ok:
        # 退出相机，回到课程表页继续照片权限测试
        t.force_stop("com.zui.camera")
        t.adb_shell("input", "keyevent", "KEYCODE_BACK")
        time.sleep(2)

    # ══ B 组：MEDIA ════════════════════════════════════════════════
    # ── Step3 图库导入-拒绝 ─────────────────────────────────────────
    t.step("Step3 图库导入-拒绝相册权限")
    if not to_course_table(t):
        t.record("FAIL", "未回到课程表页，无法继续照片权限测试")
        return t.finish()
    ok, act, btn = import_and_handle(t, "从图库导入课程表", allow=False,
                                     target_key="photopicker", timeout=8)
    if ok:
        t.record("FAIL", f"拒绝后不应进入照片选择，却进入了 {act}")
    else:
        hit, texts = wait_texts(t, ["存储权限", "需要权限", "前往设置"], 8)
        t.record("PASS" if hit else "FAIL",
                 f"拒绝后提示需要授予相册权限（点到的按钮={btn or '无'}）: {texts[:5]}")
    t.screenshot("03_图库拒绝提示")
    for w in ("取消", "知道了"):
        if any(w in x for x in t.screen_text() if x):
            t.tap_text(w, wait=2, silent=True)
            break
    time.sleep(1)

    # ── Step4 图库导入-允许 ─────────────────────────────────────────
    t.step("Step4 图库导入-允许后进入照片选择")
    if not to_course_table(t):
        t.record("FAIL", "未回到课程表页，无法继续照片允许路径")
        return t.finish()
    ok, act, btn = import_and_handle(t, "从图库导入课程表", allow=True,
                                     target_key="photopicker", timeout=25)
    t.record("PASS" if ok else "FAIL",
             f"允许后进入照片选择界面（点到的按钮={btn or '无'}）: {act}")
    t.screenshot("04_照片选择界面")

    return t.finish()


if __name__ == "__main__":
    run()
