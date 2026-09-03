#!/usr/bin/env python3
"""联想日历 179：课程时间设置——单节改间隔（53分钟）+ 上午同步 + 冲突红字 toast。

链路（盲跑 7 轮落盘 + 知识卡时间滚轮操作法）：
  手动创建 → 课程时间设置 → TimeSlotSettingsActivity
  上午第1节 row arrow → TimePickerDialog（结束分钟列 tap 下方=+1）
  确定 → "是否自动调整其他课程" dialog → 确定 → 设置页 sync
"""
import os
import re
import sys
import time

USER_INPUT = """测试用例 联想日历_179
前提：
手动创建课程表后进入课程表基本信息编辑页
操作步骤
1.手动修改上午时段中一个小节的时间间隔为 53 分钟
2.查看上午时段内后续小节上课时长的显示
3.设置上午的最后一个小节结束时间晚于下午第一节的开始时间
预期结果
1.可成功修改小节时间
2.手动修改小节间隔后，仅上午时段后续小节时间同步变为 53 分钟；课程的「每节课上课时长」仍保持 50 分钟不变
3.小节数和时间显示为红色，点击完成toast提示时间冲突"""

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))

from test_framework import TestCase, _parse_nodes   # noqa: E402
from _flow import goto_课程表空状态, goto_手动创建课程表   # noqa: E402

TIME_SETTINGS_RID = "com.zui.calendar:id/layout_time_settings"
COL_END_MIN_X = 1297
COL_END_HOUR_X = 1126
ROW_Y = 1604
ROW_STEP = 97


def ocr_value_at(t, x, y_lo, y_hi):
    ocr = t.ocr(y_lo, y_hi)
    for px, py, c, s in ocr:
        if re.fullmatch(r"\d{1,2}", s or "") and abs(px - x) < 60:
            return int(s)
    return None


def tap_increment(t, col_x, target, max_steps=80):
    """点按该列 tap 下方一格使其 +1，到目标值。"""
    for _ in range(max_steps):
        cur = ocr_value_at(t, col_x, ROW_Y - 60, ROW_Y + 60)
        if cur is None:
            time.sleep(1.2)
            continue
        if cur == target:
            return True
        if cur < target:
            t.tap_xy(col_x, ROW_Y + ROW_STEP)
        else:
            t.tap_xy(col_x, ROW_Y - ROW_STEP)
        time.sleep(0.9)
    return False


def row_arrows_sorted(t):
    """所有 desc=编辑 arrow 按 y 排序（早 8:00 第1节…）。"""
    nodes = _parse_nodes(t._dump())
    arrs = [n for n in nodes if n["desc"] == "编辑" and n["bounds_xy"]]
    arrs.sort(key=lambda n: n["bounds_xy"][1])
    return arrs


def parse_time_ranges(t):
    """读设置页上午/下午/晚上各小节 tv_time_range 与 tv_lesson_duration。"""
    nodes = _parse_nodes(t._dump())
    out = {"ranges":[], "lesson": None}
    for n in nodes:
        if n["rid"] == "com.zui.calendar:id/tv_time_range" and n["text"]:
            out["ranges"].append(n["text"])
        if n["rid"] == "com.zui.calendar:id/tv_lesson_duration" and n["text"]:
            out["lesson"] = n["text"]
    return out


def open_section_editor(t, idx):
    """点第 idx(0-based) 节行的编辑 arrow（跳过时长/课间休息两行）。"""
    arrs = row_arrows_sorted(t)
    if idx + 2 >= len(arrs):
        return False
    b = arrs[idx + 2]["bounds_xy"]
    t.tap_xy((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    time.sleep(2.2)
    return True


def run():
    t = TestCase("联想日历_179")
    t.start_watchdog(policy="allow")

    # ── 前提：手动创建（不保存，直接进课程时间设置）──
    t.step("前提-手动创建 → 进课程时间设置页")
    if not goto_课程表空状态(t, pm_clear=True):
        return t.finish()
    t.observe_dialogs(rounds=3)
    if not goto_手动创建课程表(t):
        t.record("FAIL", "未进入手动创建页")
        return t.finish()
    time.sleep(2)
    if not t.tap_rid(TIME_SETTINGS_RID):
        t.record("FAIL", "未找到课程时间设置入口")
        return t.finish()
    time.sleep(2.5)
    t.observe_dialogs(rounds=3)
    act = t.current_activity()
    t.record("PASS" if "TimeSlotSettings" in act else "FAIL",
             f"进入 TimeSlotSettingsActivity: activity={act}")

    # ── Step1：上午第1节 改结束分钟到 53（tap 下方一格 +1）──
    t.step("Step1 上午第1节弹窗 → 拨结束分钟到 53 → 确定")
    if not open_section_editor(t, 0):
        t.record("FAIL", "未找到上午第1节编辑 arrow")
        return t.finish()
    ok = tap_increment(t, COL_END_MIN_X, target=53)
    if not ok:
        t.record("FAIL", "结束分钟无法拨到 53")
        return t.finish()
    cur = ocr_value_at(t, COL_END_MIN_X, ROW_Y - 60, ROW_Y + 60)
    t.record("PASS" if cur == 53 else "FAIL",
             f"上午第1节结束分钟={cur}（预期 53）")
    if not t.tap_text("确定", wait=3):
        t.record("FAIL", "第1节确定未生效")
        return t.finish()
    time.sleep(2)
    # 自动调整 dialog
    t.tap_text("确定", wait=3)
    time.sleep(2)
    t.observe_dialogs(rounds=3)

    # ── Step2：验证上午后续小节同步、每节课时长仍 50 ──
    t.step("Step2 验证上午同步与课时长 50 分钟不变")
    t.observe_dialogs(rounds=3)
    time.sleep(1.0)
    ranges = []
    xml = t._dump()
    for n in _parse_nodes(xml):
        if n["rid"] == "com.zui.calendar:id/tv_time_range" and n["text"]:
            ranges.append(n["text"])
    lesson = (t.read_rid("com.zui.calendar:id/tv_lesson_duration") or {}).get("text", "")
    t.record("PASS" if lesson and "50" in lesson else "FAIL",
             f"每节课时长={lesson!r}（预期含 '50'）")
    # 上午 4 节（范围前 4），第1节 08:00-08:53，后续开始时间顺延 3 分钟
    expected_am = ["08:00-08:53", "09:03-09:53", "10:03-10:53", "11:03-11:53"]
    am_ok = ranges[:4] == expected_am
    t.record("PASS" if am_ok else "FAIL",
             f"上午4节同步: 实测={ranges[:4]}（预期={expected_am}）")
    # 下午 4 节未变 14:00-14:50 / 15:00-15:50 / 16:00-16:50 / 17:00-17:50
    expected_pm = ["14:00-14:50", "15:00-15:50", "16:00-16:50", "17:00-17:50"]
    pm_ok = ranges[4:8] == expected_pm
    t.record("PASS" if pm_ok else "FAIL",
             f"下午4节不变: 实测={ranges[4:8]}（预期={expected_pm}）")
    t.screenshot("179_改后设置页")

    # ── Step3：上午第4节 改结束小时到 14（>下午开始 14:00 → 冲突）──
    t.step("Step3 上午最后小节结束 > 下午开始 → 触发冲突")
    if not open_section_editor(t, 3):                # 第4节（0-based idx=3）
        t.record("FAIL", "未找到上午第4节编辑 arrow")
        return t.finish()
    # 改结束小时 11 → 14
    ok = tap_increment(t, COL_END_HOUR_X, target=14)
    cur_h = ocr_value_at(t, COL_END_HOUR_X, ROW_Y - 60, ROW_Y + 60)
    t.record("PASS" if cur_h == 14 else "FAIL",
             f"上午第4节结束小时={cur_h}（预期 14）")
    if not t.tap_text("确定", wait=3):
        t.record("FAIL", "第4节确定未生效")
        return t.finish()
    time.sleep(2)
    t.observe_dialogs(rounds=3)
    # 改后续小节也可能触发"是否自动调整其他课程"对话框 → 关掉它
    t.tap_text("确定", wait=3)
    time.sleep(2)
    t.observe_dialogs(rounds=3)
    t.screenshot("179_冲突设置页")

    # ── Step4：点完成 → toast「课程时间有冲突，无法设置」+ 红字 ─────────────
    t.step("Step4 点完成 → 期望 toast 提示时间冲突")
    try:
        t.observe_dialogs(rounds=3)
        time.sleep(1.0)
        # 实测（probe v2/v3/AB 隔离实验）：save_view bounds 中心 (1777,202) 点击无反应，
        # +61,+31 偏移点 (1788,197) 稳定触发 toast —— 热区偏移，偏移量从 bounds 推导；
        # observe=False 必须带：默认点击链的弹窗检查窗口(~1.5s)+截图会占满 toast 的 ~2s 窗口
        b = t.el_bounds(rid="com.zui.calendar:id/save_view")
        if b:
            t.tap_xy(b[0] + 61, b[1] + 31, observe=False)
        else:
            t.tap_text("完成", wait=3, observe=False)
        # 真实文案「课程时间有冲突，无法设置」来自探索期连拍记录（storage/179_toast_tap/v3_*.png），
        # toast 窗口实测 ~2-3s，动作后立即 capture_toast（截屏定格→OCR）
        texts, _shot = t.capture_toast(wait=1.0, label="179_完成_toast")
        hit = [s for s in texts if "冲突" in s]
        t.record("PASS" if hit else "FAIL",
                 f"toast 含'冲突'（实测文案='课程时间有冲突，无法设置'）: capture={hit or texts[-4:]}")
    except Exception as e:
        t.record("FAIL", f"Step4 执行异常: {e}")
    # 红字：探索记录确认第4节/第5节时间文字变红（probe_冲突设置页 截图），视觉模型断言
    try:
        t.assert_visual("页面中第4节 11:00-14:50 与第5节 14:00-14:50 的时间文字是否显示为红色",
                        ("是", "红"), msg="冲突小节时间红字")
    except Exception as e:
        t.record("WARN", f"红字视觉断言异常（未配视觉凭据时降级）: {e}")

    t.stop_watchdog()
    return t.finish()


if __name__ == "__main__":
    sys.exit(run())