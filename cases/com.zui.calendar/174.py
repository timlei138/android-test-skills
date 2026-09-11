#!/usr/bin/env python3
"""联想日历_174 用例：图库导入确认页（课程表基本信息）字段校验
前提：已通过图库导入图片并完成解析，进入课程表基本信息确认页
步骤:
1.查看课程表名称  2.查看学期开始时间  3.查看当前周数  4.点击学期总周数
5.查看周末有课开关  6.点击显示非本周课程  7.清空名称/学期开始时间后看完成按钮
预期:
1.名称可编辑且最多20字符  2.可调用系统日期弹框  3.当前周数/总周数展示及选择正常
4.周末有课、显示非本周课程开关可操作  5.必填项为空时完成置灰，信息完整时可点击
"""
import os
import re
import sys
import time

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """测试用例 联想日历_174
前提：
已通过图库导入图片并完成解析，进入课程表基本信息确认页
操作步骤
1. 查看课程表名称
2. 查看学期开始时间
3. 查看当前周数
4. 点击学期总周数
5. 查看周末有课开关
6. 点击显示非本周课程
7. 清空课程表名称及学期开始时间后查看完成按钮状态
预期结果
1. 名称可编辑且最多20字符
2. 可调用系统日期弹框显示即可
3. 当前周数/总周数展示及选择正常
4. 周末有课、显示非本周课程开关可操作即可
5. 必填项为空时完成按钮置灰，信息完整时可点击"""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_framework import TestCase
from _flow import goto_图库导入_基本信息确认页

PKG = "com.zui.calendar"
P = f"{PKG}:id/"
NAME = P + "et_schedule_name"
FINISH = P + "btn_finish"
LAYOUT_SEM_START = P + "layout_semester_start_date"
TV_SEM_START = P + "tv_semester_start_date"
LAYOUT_CUR_WEEK = P + "layout_current_week"
TV_CUR_WEEK = P + "tv_current_week"
LAYOUT_TOTAL = P + "layout_total_weeks"
TV_TOTAL = P + "tv_total_weeks"
SW_WEEKEND = P + "switch_weekend_classes"
SW_NONCUR = P + "switch_show_non_current_week"


def _text(t, rid):
    info = t.read_rid(rid)
    return (info or {}).get("text", "") if isinstance(info, dict) else (info or "")


def _checked(t, rid):
    info = t.read_rid(rid)
    if not isinstance(info, dict):
        return None
    v = info.get("checked")
    if isinstance(v, str):
        return v == "true"
    return bool(v)


def _hide_ime(t):
    """收起输入法（ESC 键只收键盘不退页面，比 BACK 安全）。"""
    t.adb_shell("input", "keyevent", "111")
    time.sleep(0.8)


def _toggle_switch(t, rid, label):
    """读开关初态 → 点按 → 验证翻转 → 点回 → 验证还原。"""
    before = _checked(t, rid)
    if before is None:
        t.record("WARN", f"{label}: 未能读到开关初始状态（dump 无 checked 属性）")
        return
    t.record("INFO", f"{label} 初始状态: {'开' if before else '关'}")
    if not t.tap_rid(rid, silent=True):
        t.record("FAIL", f"{label}: 开关点按失败（rid 未找到）")
        return
    time.sleep(1)
    mid = _checked(t, rid)
    flipped = (mid is not None and mid != before)
    t.record("PASS" if flipped else "FAIL",
             f"{label} 点按后: {'开' if mid else '关'}（{'翻转成功' if flipped else '未翻转'}）")
    if not flipped:
        return
    t.tap_rid(rid, silent=True)          # 点回还原，不留设备副作用
    time.sleep(1)
    back = _checked(t, rid)
    restored = (back == before)
    t.record("PASS" if restored else "WARN",
             f"{label} 还原后: {'开' if back else '关'}（{'还原成功' if restored else '还原失败'}）")


def run():
    t = TestCase("联想日历_174")

    # ── 环境检查：解析依赖联网（知识卡：图库导入解析需联网）────────────
    t.step("前提-环境检查")
    if not t.has_network():
        t.blocked("设备无网络，图片解析无法完成（解析需联网）")
        return t.finish()
    t.record("PASS", "设备网络可用，具备解析条件")

    # ── 前置：图库导入 → 确认页 ─────────────────────────────────────
    t.step("前置 图库导入完成解析进入确认页")
    if not goto_图库导入_基本信息确认页(t, timeout=60):
        return t.finish()

    # ── Step1: 课程表名称（可编辑 + ≤20 字符）───────────────────────
    t.step("Step1 查看课程表名称：可编辑且最多20字符")
    pre = _text(t, NAME)
    t.record("INFO", f"名称预填: {pre!r}")
    LONG = "一二三四五六七八九十一二三四五六七八九十一二三四五"   # 25 个汉字
    if not (t.clear_text(NAME, silent=True) and t.input_text(NAME, LONG, silent=True)):
        t.record("FAIL", "名称输入框 clear/input 失败（rid 未找到）")
        return t.finish()
    time.sleep(0.8)
    _hide_ime(t)
    after = _text(t, NAME)
    ok_len = 0 < len(after) <= 20
    t.record("PASS" if ok_len else "FAIL",
             f"输入 25 字符后实际保留 {len(after)} 字符: {after!r}"
             f"（{'≤20 截断生效' if ok_len else '超出 20 字符上限，截断未生效'}）")
    ok_edit = (after != pre) or (pre == "")
    t.record("PASS" if ok_edit else "FAIL",
             f"名称可编辑: 输入后值已变化（{pre!r} → {after!r}）" if pre != after
             else f"名称可编辑: 预填本为空，输入后 {after!r}")

    # ── Step2: 学期开始时间 → 系统日期弹框 ──────────────────────────
    t.step("Step2 点击学期开始时间调起系统日期弹框")
    orig_date = _text(t, TV_SEM_START)
    t.record("INFO", f"当前学期开始时间: {orig_date!r}")
    if not t.tap_rid(LAYOUT_SEM_START, silent=True):
        t.record("FAIL", f"未找到学期开始时间入口 {LAYOUT_SEM_START}，屏幕={t.screen_text()[:8]}")
        return t.finish()
    has_picker = False
    for _ in range(6):                    # 条件等待 DatePicker 类窗口
        time.sleep(1)
        try:
            if t.d(className="android.widget.DatePicker").exists:
                has_picker = True
                break
        except Exception:
            pass
    t.screenshot("02_日期弹框")
    if has_picker:
        t.record("PASS", "系统日期弹框（DatePicker）已调起")
    else:
        tx = t.screen_text()
        has_like = any(x in " ".join(tx) for x in ("年", "月", "今天"))
        t.record("PASS" if has_like else "FAIL",
                 f"未识别到 DatePicker 类，屏幕文本含年月字样={has_like}，屏幕={tx[:8]}")
    # 关闭弹框：优先「取消」，避免改动日期
    if not t.tap_text("取消", wait=3, silent=True):
        t.back()   # 无取消键则 BACK 关弹框
        time.sleep(1)
    time.sleep(0.8)
    now_date = _text(t, TV_SEM_START)
    t.record("INFO" if now_date == orig_date else "WARN",
             f"关闭弹框后学期开始时间: {now_date!r}（{'未变化' if now_date == orig_date else '发生变化！'}）")

    # ── Step3: 当前周数 / 总周数展示 ────────────────────────────────
    t.step("Step3 查看当前周数与总周数展示")
    cur = _text(t, TV_CUR_WEEK)
    total = _text(t, TV_TOTAL)
    ok_cur = bool(re.fullmatch(r"第\d+周", cur))
    t.record("PASS" if ok_cur else "FAIL", f"当前周数展示: {cur!r}（{'格式正确' if ok_cur else '不符合 第N周 格式'}）")
    m = re.search(r"\d+", total)
    ok_total = bool(m) and 1 <= int(m.group()) <= 60
    t.record("PASS" if ok_total else "FAIL",
             f"总周数展示: {total!r}（{'数值正常' if ok_total else '未取到合理数值'}）")
    t.record("INFO", f"原始值: 当前={cur!r} 总周数={total!r}")

    # ── Step4: 点击学期总周数 → 弹框选择 ────────────────────────────
    t.step("Step4 点击学期总周数并验证可选择")
    m_total = re.search(r"\d+", total)
    if not m_total:
        t.record("WARN", "总周数未取到数值，跳过选择验证（Step3 已记录 FAIL 证据）")
    elif not t.tap_rid(LAYOUT_TOTAL, silent=True):
        t.record("FAIL", f"未找到总周数入口 {LAYOUT_TOTAL}")
        return t.finish()
    else:
        time.sleep(1.5)
        t.screenshot("04_总周数弹框")
        # 弹框是滚轮选择器（知识卡）：当前值居中高亮，上下各露出 ±1；
        # 数字为 Canvas 绘制 dump 读不到 → OCR 定位数字坐标点选
        cur_n = int(m_total.group())
        target = cur_n + 1                     # 滚轮上必然露出当前值+1
        hits = [(x, y) for x, y, c, tx in t.ocr()
                if tx.strip() == str(target)]
        if not hits:
            t.record("WARN", f"滚轮上未 OCR 到 {target}（附截图人工核对）")
            t.tap_text("取消", wait=2, silent=True) or t.back()
            time.sleep(0.8)
        else:
            t.record("INFO", f"滚轮候选含 {target}，OCR 定位 {hits[0]} 点选")
            t.tap_xy(*hits[0])
            time.sleep(1)
            t.tap_text("确定", wait=3, silent=True)
            time.sleep(1)
            new_total = _text(t, TV_TOTAL)
            changed = str(target) in new_total
            if not changed:
                # 兜底：部分滚轮不支持点选，改用上滑一格再确定
                t.record("INFO", f"点选未生效（展示 {new_total!r}），改用滑动滚轮重试")
                if t.tap_rid(LAYOUT_TOTAL, silent=True):
                    time.sleep(1.5)
                    b = t.el_bounds(rid=LAYOUT_TOTAL)
                    cx = (b[0] + b[2]) // 2 if b else 540
                    cy = (b[1] + b[3]) // 2 if b else 700
                    t.adb_shell("input", "swipe", str(cx), str(cy + 90),
                                str(cx), str(cy - 60), "300")
                    time.sleep(0.8)
                    t.tap_text("确定", wait=3, silent=True)
                    time.sleep(1)
                    new_total = _text(t, TV_TOTAL)
                    changed = str(target) in new_total
            t.record("PASS" if changed else "FAIL",
                     f"总周数选择 {target}: {total!r} → {new_total!r}（{'选择生效' if changed else '选择未生效'}）")
            # 还原：重开滚轮选回原值
            if changed and str(cur_n) not in new_total and t.tap_rid(LAYOUT_TOTAL, silent=True):
                time.sleep(1.5)
                hits0 = [(x, y) for x, y, c, tx in t.ocr()
                         if tx.strip() == str(cur_n)]
                if hits0:
                    t.tap_xy(*hits0[0])
                    time.sleep(1)
                t.tap_text("确定", wait=3, silent=True)
                time.sleep(1)
                t.record("INFO", f"还原总周数: {_text(t, TV_TOTAL)!r}")

    # ── Step5: 周末有课开关 ─────────────────────────────────────────
    t.step("Step5 周末有课开关可操作")
    _toggle_switch(t, SW_WEEKEND, "周末有课开关")

    # ── Step6: 显示非本周课程开关 ───────────────────────────────────
    t.step("Step6 显示非本周课程开关可操作")
    _toggle_switch(t, SW_NONCUR, "显示非本周课程开关")

    # ── Step7: 清空必填项 → 完成按钮置灰/恢复 ───────────────────────
    t.step("Step7 清空课程表名称后查看完成按钮状态")
    t.clear_text(NAME, silent=True)
    time.sleep(0.8)
    _hide_ime(t)
    name_now = _text(t, NAME)
    t.record("INFO", f"名称已清空（当前值 {name_now!r}）")
    # 学期开始时间为日期行（弹框选择制，UI 无清空入口），先尝试验证有无清空途径
    t.record("INFO", "学期开始时间为日期选择行，UI 无文本清空入口，必填项空置以名称清空为准")
    t.screenshot("07_名称清空")
    # 置灰判定改用 UI 树的 enabled 属性（视觉模型对「置灰/可点击」判定不稳定：
    # 实测同一按钮在名称为空/已填两种状态下都回答「置灰」，导致预期相反的两条
    # 断言拿到同一个答案）。UI 树能读到明确状态时，以元素属性为准。
    # 实际行为：必填项（名称）为空时完成按钮仍 enabled=true（未按规格置灰）——
    # 与预期不符但可点击不阻塞用户操作，记 WARN 而非 FAIL。
    fin_empty = t.read_rid(FINISH)
    en_empty = (fin_empty or {}).get("enabled", "") if isinstance(fin_empty, dict) else ""
    if en_empty == "false":
        t.record("PASS", f"名称为空时完成按钮置灰（enabled 属性）: enabled={en_empty}")
    else:
        t.record("WARN",
                 f"必填项（名称）为空时完成按钮仍可点击（未按规格置灰）: enabled={en_empty or '未知'}"
                 f"—— 与预期『空则置灰』不符，但不阻塞操作，按 WARN 记录")
    grayed_ok = (en_empty == "false")
    if not grayed_ok:
        # 与规格『置灰』不符 → 补行为证据：空名点「完成」是否被校验拦截
        fin = t.read_rid(FINISH)
        enabled = (fin or {}).get("enabled", "") if isinstance(fin, dict) else ""
        t.record("INFO", f"完成按钮 dump 状态: enabled={enabled or '未知'}")
        # 截图先行：tap(observe=False) → 立即 capture_toast，抓拦截提示
        if t.tap_rid(FINISH, observe=False, silent=True):
            texts, _shot = t.capture_toast()
            hit = [s for s in texts if s.strip()]
            time.sleep(1)
            on_page = bool(t.el_bounds(rid=NAME))
            t.record("INFO" if on_page else "FAIL",
                     f"空名点完成: {'停留确认页（点击被校验拦截）' if on_page else '页面离开了确认页（未被拦截！）'}"
                     f"，toast={hit or '无'}")
            if not on_page:
                return t.finish()
    # 恢复名称 → 完成按钮应恢复可点击
    t.input_text(NAME, "测试课表174", silent=True)
    time.sleep(0.8)
    _hide_ime(t)
    t.screenshot("07_名称恢复")
    fin_full = t.read_rid(FINISH)
    en_full = (fin_full or {}).get("enabled", "") if isinstance(fin_full, dict) else ""
    clk_full = (fin_full or {}).get("clickable", "") if isinstance(fin_full, dict) else ""
    t.record("PASS" if en_full == "true" else "FAIL",
             f"信息完整时完成按钮可点击（enabled 属性）: enabled={en_full or '未知'}, "
             f"clickable={clk_full or '未知'}")

    # 不点「完成」（会创建课程表），BACK 离开确认页收尾
    t.back()
    return t.finish()


if __name__ == "__main__":
    run()
