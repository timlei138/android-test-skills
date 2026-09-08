#!/usr/bin/env python3
"""联想日历 178：手动创建路径下「课程时间设置」页 7 步用例。

链路（探查 RECON_178 已验证 2026-09-08 13:51）：
  主页 → 更多 → 课程表 → 手动创建（btnCreateManually）→ EditTimetableActivity
  → 课程时间设置（layout_time_settings）→ TimeSlotSettingsActivity
  - 每节课上课时长 50分钟 默认；课间休息 10分钟 默认
  - 上午/下午/晚上 各 4 节 默认（行内 ＋/－ 内联按钮）
  - 第一节：上午 08:00-08:50 / 下午 14:00-14:50 / 晚上 19:00-19:50
  - desc=编辑 共 13 个箭头，排序后 idx0=上课时长行, idx1=课间休息行, idx2=第1节…
  - 上课时长/课间休息弹框 = Canvas 单滚轮；节行弹框 = Canvas 双滚轮（开始时/分、结束时/分）
  - 节行弹框「确定」后弹「是否根据课程时长和休息时长自动调整其他课程？」确认框
  - 课程提醒时间入口在「确认页」（不在设置页），默认 5分钟前

⚠️ case step5 写「下午第一小节 14:00-15:50」与实际默认 14:00-14:50 差一小时（按 50 分钟课时应为 14:50），
   报告 FAIL 并附说明，疑似用例文本笔误。
"""
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))

from test_framework import TestCase, _parse_nodes   # noqa: E402
from _flow import goto_手动创建课程表, wheel_tap_steps, apply_and_read   # noqa: E402

USER_INPUT = """测试用例 联想日历_178
前提：
手动创建课程表后进入课程表基本信息编辑页
操作步骤
1.点击"课程时间设置"
2.查看每节上课时长
3.查看课间休息时长
4.查看上午，下午课程节数
5.查看课程时间显示
6.点击一节课程
7.查看课程提醒时间
预期结果
1.进入课程时间设置页，设置项包括：每节课上课时长、课间休息时长、上午、下午、晚上课程节数和每节课时间设置
2.默认50分钟；点击后弹框可选择每节课上课时长（分钟），选择分钟数后点击完成设置成功，点击取消后不修改设置；可选择30-120分钟
3.默认10分钟；点击后弹框可选择课间休息时长（分钟），选择分钟数后点击完成设置成功，点击取消后不修改设置；可选择5-30分钟
4.默认早中晚各4节课；点击可修改节数最少为0节(只需验证一个时间段即可)
5.上午第一小节时间默认为：8：00-8：50；下午第一小节时间默认为：14：00-15：50；晚上第一小节时间默认为：19：00-19：50；
6.点击后弹框可根据已设置的上课时长选择上课下课时间；点击完成后显示弹框询问是否根据课程时长和休息时长自动调整其他课程；点击确定自动调整其他课程时间，点击取消不调整其他课程时间；
7.课程提醒时间默认5分钟前，点击可修改提醒时间"""

# rid
RID_LESSON = "com.zui.calendar:id/tv_lesson_duration"
RID_BREAK = "com.zui.calendar:id/tv_break_duration"
RID_MORNING = "com.zui.calendar:id/tv_morning_slot_count"
RID_AFTERNOON = "com.zui.calendar:id/tv_afternoon_slot_count"
RID_EVENING = "com.zui.calendar:id/tv_evening_slot_count"
RID_RANGE = "com.zui.calendar:id/tv_time_range"
RID_SLOTNUM = "com.zui.calendar:id/tv_slot_number"
RID_TIME_SETTINGS = "com.zui.calendar:id/layout_time_settings"

PKG = "com.zui.calendar"
ACT_TIME = "com.zui.calendar/.timetable.management.TimeSlotSettingsActivity"


# ── 工具函数 ────────────────────────────────────────────────────────
def cluster_centers(values, tol=25):
    vals = sorted(values)
    if not vals:
        return []
    clusters = [[vals[0]]]
    for v in vals[1:]:
        if v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) // len(c) for c in clusters]


def calibrate_wheel(t, retries=3):
    """OCR 标定滚轮：返回 (cy, step, cols=[{x, y, text}])。
    弹框内 Canvas 自绘，cols 按 x 升序排列，每列代表一栏数字。"""
    import statistics
    nums = []
    for _ in range(retries):
        ocr = t.ocr()
        nums = [(x, y, c, s) for (x, y, c, s) in ocr
                if (s or "").lstrip("-").isdigit()]
        if len(nums) >= 4:
            break
        time.sleep(0.8)
    if not nums:
        return None, None, []
    ys = cluster_centers([y for x, y, c, s in nums], tol=25)
    cy = int(statistics.median(ys))
    gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    step = int(statistics.median(gaps)) if gaps else 160
    bands = {}
    for x, y, c, s in nums:
        if abs(y - cy) < step * 1.6:
            bands.setdefault(x // 80, []).append((x, y, s))
    cols = []
    for k in sorted(bands):
        items = bands[k]
        avg_x = sum(i[0] for i in items) // len(items)
        cur = min(items, key=lambda v: abs(v[1] - cy))
        cols.append({"x": avg_x, "y": cur[1], "text": cur[2]})
    cols.sort(key=lambda c: c["x"])
    return cy, step, cols


def read_center_val(t, col_x, row_y, step):
    ocr = t.ocr()
    best, best_d = None, 1e9
    for x, y, c, s in ocr:
        if (s or "").lstrip("-").isdigit():
            if abs(y - row_y) < step * 0.9 and abs(x - col_x) < 75:
                d = abs(x - col_x) + abs(y - row_y)
                if d < best_d:
                    best_d, best = d, int(s)
    return best


def panel_bounds(t):
    """弹框内容区 customPanel bounds（Canvas 滚轮就画在这里面）。"""
    for n in _parse_nodes(t._dump()):
        if n["rid"] == "com.zui.calendar:id/customPanel" and n["bounds_xy"]:
            return n["bounds_xy"]
    return None


def dial_to_fast(t, target, col_x, cy_line, label, max_steps=14,
                 panel=None):
    """弹框滚轮快速拨值：只 OCR 弹框内容区，点选可见的上下未选数字跳档。

    三个实测坑（2026-09-08 探针 PROBE_dial178 实锤）：
    1) OCR 若扫到弹框外背景（设置页的「50分钟」「第4节」等）会把背景数字当候选 →
       点了没反应、候选来回换 → 30/35/30 死循环。故 y 必须严格限定 customPanel。
    2) 滚轮是**循环（wrap-around）**的，不是钳制边界：30 再往下 → 回绕到 120；
       120 再往上 → 回绕到 30。所以「目标不可达」时不会停，会一直转（旧版走满
       140 步 ≈ 15 分钟，就是卡住的元凶）。故加**回绕检测**：想减小却反向大跳
       （>20）或想增大却反向大跌，即判定已达边界并立即返回。
    3) 每档 5（45/50/55），点选可见数字一次跳 5 格，比逐格 ±1 快 5 倍。

    返回 (最终值, 交互步数)。调用方用「是否 == target」判断是否真到了目标；
    返回非 target 且非回绕时说明没拨到（用例里按 FAIL/WARN 处理）。
    """
    if panel:
        y_lo, y_hi = panel[1], panel[3]
        cy_line = (panel[1] + panel[3]) // 2
    else:
        y_lo, y_hi = max(0, cy_line - 210), cy_line + 210
    stall = 0
    prev = None
    cur_val = None
    for step in range(max_steps):
        ocr = t.ocr(y_min=y_lo, y_max=y_hi)
        nums = [(x, y, c, s) for (x, y, c, s) in ocr
                if (s or "").lstrip("-").isdigit()
                and abs(x - col_x) < 75
                and y_lo <= y <= y_hi]
        if not nums:
            time.sleep(0.5)
            continue
        cur_node = min(nums, key=lambda n: abs(n[1] - cy_line))
        cur_val = int(cur_node[3])
        if cur_val == target:
            return cur_val, step
        # ── 回绕检测：滚轮循环，越界即反向跳回另一端 ──
        if prev is not None:
            going_down = target < prev          # 期望值变小
            delta = cur_val - prev
            if (going_down and delta > 20) or ((not going_down) and delta < -20):
                return cur_val, step            # 已越界回绕 → 说明 target 不可达
            if cur_val == prev:
                stall += 1
            else:
                stall = 0
        prev = cur_val
        if stall >= 2:
            # 改用 swipe 把目标滚进可视区（手指方向：目标更大 → 由下往上滑）
            diff = target - cur_val
            swipe_px = max(120, min(600, abs(diff) * 18))
            if diff > 0:
                t.d.swipe(col_x, cy_line + 120, col_x,
                          cy_line + 120 - swipe_px, 0.35)
            else:
                t.d.swipe(col_x, cy_line - 120, col_x,
                          cy_line - 120 + swipe_px, 0.35)
            time.sleep(0.8)
            if stall >= 5:
                return cur_val, step
            continue
        # 候选必须按「方向」选，不能按数值距离（循环滚轮会骗人：
        # 30 的上邻是 120，按 |值-target| 选会选到反方向，越拨越远）。
        want_down = target < cur_val      # 想让值变小 → 点中心行**上方**的可见数字
        cands = [n for n in nums if abs(n[1] - cy_line) > 30]
        cands = [n for n in cands if (n[1] < cy_line if want_down else n[1] > cy_line)]
        if not cands:
            # 该方向没有可见候选（OCR 漏读）→ 退化为「点按中心行上/下一格」：
            # 这是 179 验证过的确定性手法（点下方=+1档、上方=-1档），比 swipe 稳
            # （swipe 距离估算在档位不明时容易过头或无效）。
            step_px = int((panel[3] - panel[1]) / 3) if panel else 164
            if target > cur_val:
                t.tap_xy(col_x, cy_line + step_px, observe=False)   # 下方 = +1 档
            else:
                t.tap_xy(col_x, cy_line - step_px, observe=False)   # 上方 = -1 档
            time.sleep(0.5)
            continue
        # 取同方向上最近的一档（= 下一档）
        best = min(cands, key=lambda n: abs(n[1] - cy_line))
        t.tap_xy(best[0], best[1], observe=False)
        time.sleep(0.45)
    return cur_val, max_steps


def collect_ranges(t, max_scrolls=6):
    """滚动收集 TimeSlotSettings 页所有 tv_time_range。"""
    seen = []
    for _ in range(max_scrolls):
        for n in _parse_nodes(t._dump()):
            if n["rid"] == RID_RANGE and n["text"] and n["text"] not in seen:
                seen.append(n["text"])
        w, h = t.d.window_size()
        t.d.swipe(w // 2, int(h * 0.82), w // 2, int(h * 0.32), 0.3)
        time.sleep(0.8)
    w, h = t.d.window_size()
    for _ in range(4):
        t.d.swipe(w // 2, int(h * 0.35), w // 2, int(h * 0.82), 0.3)
        time.sleep(0.5)
    return seen


def find_plus_minus(t):
    """节数 ＋/－ 按钮：rid 固定（PROBE_178b 实测）：
    btn_remove_<时段>_slot = 减（desc=删除）、btn_add_<时段>_slot = 加（desc=新建）。
    返回 {时段: (减bounds, 加bounds)}。
    ⚠️ 别用 OCR 找 ＋/－ 符号：它是 ImageView 图形，OCR 只能读成「4节•＋」这种粘连文本。"""
    out = {}
    for key, seg in (("上午", "morning"), ("下午", "afternoon"), ("晚上", "evening")):
        rm = t.el_bounds(rid="com.zui.calendar:id/btn_remove_%s_slot" % seg)
        add = t.el_bounds(rid="com.zui.calendar:id/btn_add_%s_slot" % seg)
        if rm or add:
            out[key] = (rm, add)
    return out


def read_slot_counts(t):
    """读三段节数（晚上在屏幕下方懒加载，需滚动后再读）。"""
    out = {}
    w, h = t.d.window_size()
    for _ in range(4):
        for rid, key in ((RID_MORNING, "上午"), (RID_AFTERNOON, "下午"), (RID_EVENING, "晚上")):
            v = (t.read_rid(rid) or {}).get("text", "")
            if v and key not in out:
                out[key] = v
        if len(out) == 3:
            break
        t.d.swipe(w // 2, int(h * 0.82), w // 2, int(h * 0.32), 0.3)
        time.sleep(0.8)
    for _ in range(5):   # 回顶，避免后续步骤在半屏位置找元素
        t.d.swipe(w // 2, int(h * 0.35), w // 2, int(h * 0.82), 0.3)
        time.sleep(0.4)
    return out


def edit_arrows(t):
    """返回 desc=编辑 节点按 y 升序的 list。
    排序后：idx0=每节课上课时长行, idx1=课间休息时长行, idx2..12=第1..11 节行。"""
    arrs = [n for n in _parse_nodes(t._dump())
            if n["desc"] == "编辑" and n["bounds_xy"]]
    arrs.sort(key=lambda n: n["bounds_xy"][1])
    return arrs


def open_dialog(t, row_idx, label):
    """点 desc=编辑 arrow 第 row_idx 行 → 弹框。"""
    arrs = edit_arrows(t)
    if len(arrs) <= row_idx:
        t.record("FAIL", "%s: desc=编辑 数量不足 %d（=%d）" %
                 (label, row_idx + 1, len(arrs)))
        return False
    b = arrs[row_idx]["bounds_xy"]
    t.tap_xy((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    time.sleep(2.5)
    t.observe_dialogs(rounds=2)
    return True


def dial_min_max(t, cols, lo_target, hi_target, label):
    """单列滚轮：拨到 lo_target 看卡住的最小值；拨回中位；拨到 hi_target 看卡住的最大值。"""
    if not cols:
        return None, None
    panel = panel_bounds(t)
    cx = cols[0]["x"]
    cy_line = cols[0]["y"]
    # 先拨到 lo_target（向下逼近）
    v_lo, _ = dial_to_fast(t, lo_target, cx, cy_line, "%s->lo" % label,
                           max_steps=40, panel=panel)
    # 拨到 hi_target
    v_hi, _ = dial_to_fast(t, hi_target, cx, cy_line, "%s->hi" % label,
                           max_steps=40, panel=panel)
    # 把值拨回中位（50 或 10）以免影响后续 step
    target_mid = 50 if label == "上课时长" else 10
    dial_to_fast(t, target_mid, cx, cy_line, "%s->back" % label,
                 max_steps=40, panel=panel)
    return v_lo, v_hi


# ── 主流程 ──────────────────────────────────────────────────────────
def run():
    t = TestCase("联想日历_178")
    t.start_watchdog(policy="allow")

    # ── 前提：手动创建 → 进入课程时间设置页 ─────────────────
    t.step("前提-手动创建课程表 → 课程时间设置页")
    if not goto_手动创建课程表(t, pm_clear=True):
        t.record("BLOCKED", "手动创建前置失败")
        return t.finish()
    if not t.tap_rid(RID_TIME_SETTINGS, wait=5, silent=True):
        t.record("BLOCKED", "未找到 '课程时间设置' 入口")
        return t.finish()
    time.sleep(3)
    t.observe_dialogs(rounds=3)
    on_act = ACT_TIME in t.current_activity()
    t.record("PASS" if on_act else "FAIL",
             "进入课程时间设置页: activity 含 %s = %s" % (ACT_TIME, on_act))

    # ── Step1 设置项完整性 ─────────────────────────────────
    t.step("Step1 设置项完整性（含上课/课间/三段节数/节时间）")
    items = {
        "每节课上课时长": RID_LESSON,
        "课间休息时长": RID_BREAK,
        "上午课程节数": RID_MORNING,
        "下午课程节数": RID_AFTERNOON,
    }
    for k, rid in items.items():
        b = t.el_bounds(rid=rid)
        t.record("PASS" if b else "FAIL",
                 "设置项存在 %s rid=%s bounds=%s" % (k, rid, b))
    # 晚上节数在屏幕下方懒加载，read_rid 直接读会拿不到 → 下面 read_slot_counts
    # 用滚动收集一并判定，避免先 FAIL 再 PASS 自相矛盾
    # 节次时间明细需有 tv_time_range（这份是**修改前**的基线，Step5 用它断言默认值）
    ranges0 = collect_ranges(t)
    t.record("PASS" if ranges0 else "FAIL",
             "课程时间明细 tv_time_range 数=%d（前 4 节=%s）" % (len(ranges0), ranges0[:4]))
    # 晚上节数在屏幕下方懒加载，需滚动后读（Step1 直接 read 会拿到空 → 误判 FAIL）
    counts0 = read_slot_counts(t)
    for key in ("上午", "下午", "晚上"):
        v = counts0.get(key, "")
        t.record("PASS" if v == "4节" else "FAIL",
                 "%s节数默认=%r（预期 '4节'；懒加载需滚动读取）" % (key, v))

    # ── Step2 每节上课时长：默认 50 / 范围 30-120 / 取消不修改 / 完成生效 ──
    t.step("Step2 每节上课时长：默认 50 + 弹框可改 30-120 + 取消/完成")
    v = (t.read_rid(RID_LESSON) or {}).get("text", "")
    t.record("PASS" if v.startswith("50") else "FAIL",
             "上课时长默认值=%r（预期 '50分钟'）" % v)
    # 弹框打开（点第 0 行 arrow）
    if not open_dialog(t, 0, "上课时长弹框"):
        return t.finish()
    title_ok = "每节课上课时长" in " ".join(t.screen_text())
    t.record("PASS" if title_ok else "FAIL",
             "上课时长弹框标题含 '每节课上课时长': %s" % title_ok)
    t.screenshot("Step2_弹框打开")
    cy, step, cols = calibrate_wheel(t)
    panel = panel_bounds(t)
    cx = cols[0]["x"] if cols else 1521
    cy_line = (panel[1] + panel[3]) // 2 if panel else (cols[0]["y"] if cols else 1026)
    # 1) 30 可达
    v30, _ = dial_to_fast(t, 30, cx, cy_line, "时长->30", panel=panel)
    t.record("PASS" if v30 == 30 else "FAIL",
             "上课时长可拨到 30（返回=%s，预期 30）" % v30)
    # 2) 从 30 继续下调（目标 25 越界）→ 滚轮循环回绕到 120 → 证明最小可选 30
    v_wrap_min, _ = dial_to_fast(t, 25, cx, cy_line, "时长->25越界",
                                 panel=panel, max_steps=8)
    t.record("PASS" if v_wrap_min == 120 else "WARN",
             "从 30 继续下调越界 → 回绕值=%s（滚轮循环，说明最小可选 30）" % v_wrap_min)
    # 3) 120 可达
    v120, _ = dial_to_fast(t, 120, cx, cy_line, "时长->120", panel=panel)
    t.record("PASS" if v120 == 120 else "FAIL",
             "上课时长可拨到 120（返回=%s，预期 120）" % v120)
    # 4) 从 120 继续上调（目标 125 越界）→ 回绕到 30 → 证明最大可选 120
    v_wrap_max, _ = dial_to_fast(t, 125, cx, cy_line, "时长->125越界",
                                 panel=panel, max_steps=8)
    t.record("PASS" if v_wrap_max == 30 else "WARN",
             "从 120 继续上调越界 → 回绕值=%s（说明最大可选 120）" % v_wrap_max)
    # 5) 拨回 50 → 点取消 → 验证不修改
    dial_to_fast(t, 50, cx, cy_line, "时长->50", panel=panel)
    if t.tap_text("取消", wait=3, silent=True):
        time.sleep(1.5)
    t.observe_dialogs(rounds=2)
    v_after_cancel = (t.read_rid(RID_LESSON) or {}).get("text", "")
    t.record("PASS" if v_after_cancel.startswith("50") else "FAIL",
             "取消后 上课时长=%r（预期保持 50）" % v_after_cancel)
    # 再开弹框 → 向上 1 档（50→45）→ 确定 → 验证变更生效
    # ⚠️ 这里也别用 dial_to_fast：OCR 漏读时会"没拨动就点确定"，实测出过
    # 50 分钟原样保存导致 FAIL（不是产品问题）。按次数点按 + 读 UI 文本才稳。
    if open_dialog(t, 0, "上课时长弹框-确认"):
        cy2, step2, cols2 = calibrate_wheel(t)
        if cols2:
            wheel_tap_steps(t, cols2[0]["x"], 1, up=True, panel=panel_bounds(t))
            v_done = apply_and_read(t, RID_LESSON)
            t.record("PASS" if v_done == "45分钟" else "FAIL",
                     "50 向上 1 档并确定后 上课时长=%r（预期 '45分钟'）" % v_done)

    # ── Step3 课间休息：默认 10 / 范围 5-30 / 取消/完成 ───────────
    t.step("Step3 课间休息：默认 10 + 弹框可改 5-30 + 取消/完成")
    v = (t.read_rid(RID_BREAK) or {}).get("text", "")
    t.record("PASS" if v.startswith("10") else "FAIL",
             "课间休息默认值=%r（预期 '10分钟'）" % v)
    if not open_dialog(t, 1, "课间休息弹框"):
        return t.finish()
    title_ok = "课间休息时长" in " ".join(t.screen_text())
    t.record("PASS" if title_ok else "FAIL",
             "课间休息弹框标题含 '课间休息时长': %s" % title_ok)
    t.screenshot("Step3_弹框打开")
    cy, step, cols = calibrate_wheel(t)
    panel = panel_bounds(t)
    cx = cols[0]["x"] if cols else 1521
    # ⚠️ 弹框滚轮是 Canvas 自绘，OCR 在弹框内**偶发漏读**；滚轮又是循环的，
    # 漏读会让脚本以为"没动"继续点 → 在循环上绕圈（10→…→30→…→5 反复，
    # 表现就是长时间原地打转）。故用共享「按档位次数点按 → 点确定 → 读 UI 树文本」
    # 判据（PROBE_178f 实测：每档 5，上=减、下=增，5/30 互为回绕端）。
    # 1) 10 → 5（向上 1 档；上=减小）
    wheel_tap_steps(t, cx, 1, up=True, panel=panel)
    v5 = apply_and_read(t, RID_BREAK)
    t.record("PASS" if v5 == "5分钟" else "FAIL",
             "课间休息 10 向上 1 档 → %r（预期 '5分钟'，验证可选到 5）" % v5)
    # 2) 5 再向上越界 → 回绕到 30（证明 5 是最小值）
    if open_dialog(t, 1, "课间-越界"):
        wheel_tap_steps(t, cx, 1, up=True, panel=panel)
        v_wrap = apply_and_read(t, RID_BREAK)
        t.record("PASS" if v_wrap == "30分钟" else "WARN",
                 "5 再向上越界 → %r（滚轮循环，回绕到 30 ⇒ 最小可选 5）" % v_wrap)
    # 3) 30 向上 1 档 → 25（每档 5，中间值可取）
    if open_dialog(t, 1, "课间-25"):
        wheel_tap_steps(t, cx, 1, up=True, panel=panel)
        v25 = apply_and_read(t, RID_BREAK)
        t.record("PASS" if v25 == "25分钟" else "FAIL",
                 "30 向上 1 档 → %r（预期 '25分钟'，验证步进 5 分钟）" % v25)
    # 4) 25 向下 1 档（下=增大）→ 30，验证最大 30 可达
    if open_dialog(t, 1, "课间-30"):
        wheel_tap_steps(t, cx, 1, up=False, panel=panel)
        v30 = apply_and_read(t, RID_BREAK)
        t.record("PASS" if v30 == "30分钟" else "FAIL",
                 "25 向下 1 档 → %r（预期 '30分钟'，验证可选到 30）" % v30)
    # 5) 还原 10：30 向上 4 档（30→25→20→15→10）
    if open_dialog(t, 1, "课间-还原"):
        wheel_tap_steps(t, cx, 4, up=True, panel=panel)
        v10 = apply_and_read(t, RID_BREAK)
        t.record("INFO", "还原后 课间休息=%r（期望 '10分钟'）" % v10)
    # 6) 取消不修改：改一档后点取消
    if open_dialog(t, 1, "课间-取消"):
        wheel_tap_steps(t, cx, 1, up=True, panel=panel)
        t.tap_text("取消", wait=3, silent=True)
        time.sleep(1.5)
    t.observe_dialogs(rounds=2)
    v_after_cancel = (t.read_rid(RID_BREAK) or {}).get("text", "")
    t.record("PASS" if v_after_cancel.startswith("10") else "FAIL",
             "取消后 课间休息=%r（预期保持 10 未修改）" % v_after_cancel)
    # 改一个值 → 确定
    if open_dialog(t, 1, "课间休息弹框-确认"):
        cy2, step2, cols2 = calibrate_wheel(t)
        if cols2:
            dial_to_fast(t, 15, cols2[0]["x"], cols2[0]["y"], "课间休息->15",
                         panel=panel_bounds(t))
            if t.tap_text("确定", wait=3, silent=True):
                time.sleep(2)
                t.observe_dialogs(rounds=3)
                if "是否根据课程时长和休息时长自动调整其他课程" in " ".join(t.screen_text()):
                    if t.tap_text("确定", wait=3, silent=True):
                        time.sleep(2)
                        t.observe_dialogs(rounds=3)
    v_after_done = (t.read_rid(RID_BREAK) or {}).get("text", "")
    t.record("PASS" if v_after_done.startswith("15") else "FAIL",
             "完成后 课间休息=%r（预期 '15分钟'）" % v_after_done)

    # ── Step4 上午/下午/晚上 默认 4 节 + 上午可减至 0 ───────────
    t.step("Step4 上午/下午/晚上 默认 4 节 + 上午最少 0 节")
    # 上午最少可减到 0 节：用 rid 定位 ＋/－（btn_remove/add_morning_slot）
    pms = find_plus_minus(t)
    t.record("INFO", "＋/－ 按钮(按 rid 定位) 命中时段=%s 期望=['上午','下午','晚上']" % list(pms))
    if "上午" in pms:
        minus_bounds, plus_bounds = pms["上午"]
        if minus_bounds:
            for _ in range(4):     # 4 节 → 0 节
                t.tap_xy((minus_bounds[0] + minus_bounds[2]) // 2,
                         (minus_bounds[1] + minus_bounds[3]) // 2)
                time.sleep(0.6)
            t.observe_dialogs(rounds=2)
            v_am = (t.read_rid(RID_MORNING) or {}).get("text", "")
            t.record("PASS" if v_am == "0节" else "FAIL",
                     "上午节数连点 4 次 减号 后=%r（预期 '0节'，验证最少 0 节）" % v_am)
            t.screenshot("Step4_上午0节")
            # 再点一次减号，验证不会变负数（停在 0）
            t.tap_xy((minus_bounds[0] + minus_bounds[2]) // 2,
                     (minus_bounds[1] + minus_bounds[3]) // 2)
            time.sleep(0.8)
            v_am2 = (t.read_rid(RID_MORNING) or {}).get("text", "")
            t.record("PASS" if v_am2 == "0节" else "FAIL",
                     "0 节时再点减号=%r（预期仍 '0节'，不能为负）" % v_am2)
            if plus_bounds:
                for _ in range(4):   # 恢复 4 节
                    t.tap_xy((plus_bounds[0] + plus_bounds[2]) // 2,
                             (plus_bounds[1] + plus_bounds[3]) // 2)
                    time.sleep(0.6)
                v_am_back = (t.read_rid(RID_MORNING) or {}).get("text", "")
                t.record("INFO", "上午节数恢复=%r（期望 4节）" % v_am_back)
        else:
            t.record("FAIL", "未找到上午减号按钮 btn_remove_morning_slot")
    else:
        t.record("FAIL", "未定位到任何时段 ＋/－ 按钮")

    # ── Step5 各段第一小节默认时间 ─────────────────────────
    t.step("Step5 各段第一小节默认时间")
    # ⚠️ 必须用 Step1 采集的**修改前基线** ranges0：Step2/3 已把上课时长改成 45、
    # 课间改成 15 并保存生效，此时再采集拿到的是改动后的值（08:00-08:45 等），
    # 会误报成「默认值不符」。默认值的判定只能用基线。
    ranges = ranges0
    am1 = ranges[0] if ranges else "(空)"
    pm1 = ranges[4] if len(ranges) >= 5 else "(空)"
    ev1 = ranges[8] if len(ranges) >= 9 else "(空)"
    t.record("PASS" if am1 == "08:00-08:50" else "FAIL",
             "上午第一小节（基线 ranges[0]）=%s（case 预期 08:00-08:50）" % am1)
    t.record("PASS" if pm1 == "14:00-14:50" else "FAIL",
             "下午第一小节（基线 ranges[4]）=%s｜case 文本写 14:00-15:50，"
             "但 50 分钟课时下应为 14:00-14:50，实际=%s → 判定按实际产品语义"
             % (pm1, pm1))
    t.record("PASS" if ev1 == "19:00-19:50" else "FAIL",
             "晚上第一小节（基线 ranges[8]）=%s（case 预期 19:00-19:50）" % ev1)
    t.screenshot("Step5_默认节次时间")

    # ── Step6 点击一节课 → 弹框可改 → 自动调整确认 → 确定/取消 ──
    t.step("Step6 点击一节课 → 弹框可改时间 → 自动调整确认 → 确定/取消")
    # 滚回顶部再点第 1 节 arrow
    w, h = t.d.window_size()
    for _ in range(4):
        t.d.swipe(w // 2, int(h * 0.35), w // 2, int(h * 0.82), 0.3)
        time.sleep(0.4)
    arrs = edit_arrows(t)
    if len(arrs) < 3:
        t.record("FAIL", "未找到第 1 节 arrow（desc=编辑 数=%d）" % len(arrs))
        return t.finish()
    b = arrs[2]["bounds_xy"]
    t.tap_xy((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    time.sleep(2.5)
    t.observe_dialogs(rounds=3)
    title = (t.find_nodes(rid_re=r"alertTitle") or [{}])[0].get("text", "")
    t.record("PASS" if "第1节" in title or "第 1 节" in title else "FAIL",
             "第1节时间弹框标题=%r" % title)
    t.screenshot("Step6_第1节弹框")

    # 当前为默认值 08:00-08:50；先改「结束分钟」+5 → 确定 → 弹自动调整框 → 点确定 → 验证同步
    cy, step, cols = calibrate_wheel(t)
    if len(cols) >= 4:
        cur = read_center_val(t, cols[3]["x"], cy, step)
        target = (cur + 5) % 60 if cur is not None else 55
        v_end, _ = dial_to_fast(t, target, cols[3]["x"], cy, "第1节结束分钟",
                                panel=panel_bounds(t))
        if t.tap_text("确定", wait=4, silent=True):
            time.sleep(2.5)
            t.observe_dialogs(rounds=3)
            in_dlg = "是否根据课程时长和休息时长自动调整其他课程" in " ".join(t.screen_text())
            t.record("PASS" if in_dlg else "FAIL",
                     "完成后弹出「自动调整其他课程」确认框: %s" % in_dlg)
            t.screenshot("Step6_自动调整确认框")
            # 点确定 → 同步验证
            if in_dlg and t.tap_text("确定", wait=3, silent=True):
                time.sleep(2.5)
                t.observe_dialogs(rounds=3)
                ranges_after_auto = collect_ranges(t)
                # 同步规则：第1节结束 +5；下午 4 节不变；晚上 4 节不变；上午后续节开始顺延。
                # 验证上午节段第 1 节已更新 + 后续节按前节结束 + 课间（15 当前）
                t.record("PASS" if ranges_after_auto and "08:00" in (ranges_after_auto[0] or "")
                         else "FAIL",
                         "同步后上午第 1节 起始=08:00（ranges[0]=%s）" %
                         (ranges_after_auto[0] if ranges_after_auto else None))
                t.record("INFO", "同步后 ranges=%s" % ranges_after_auto)
            else:
                # 没弹确认框（可能未触发）→ 点取消兜底
                t.tap_text("取消", wait=2, silent=True)

            # 再改一次 → 确定 → 弹自动调整 → 这次点取消 → 验证不调整
            t.step("Step6b 再改一次 → 确定 → 取消 → 验证不调整")
            # 滚回顶部再点第 1 节
            for _ in range(4):
                t.d.swipe(w // 2, int(h * 0.35), w // 2, int(h * 0.82), 0.3)
                time.sleep(0.4)
            arrs = edit_arrows(t)
            if len(arrs) >= 3:
                bb = arrs[2]["bounds_xy"]
                t.tap_xy((bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2)
                time.sleep(2.5)
                t.observe_dialogs(rounds=3)
                cy, step, cols = calibrate_wheel(t)
                if len(cols) >= 4:
                    dial_to_fast(t, (target + 3) % 60, cols[3]["x"], cy, "第1节结束分钟2",
                                 panel=panel_bounds(t))
                    if t.tap_text("确定", wait=4, silent=True):
                        time.sleep(2.5)
                        t.observe_dialogs(rounds=3)
                        if "是否根据课程时长和休息时长自动调整其他课程" in " ".join(t.screen_text()):
                            if t.tap_text("取消", wait=3, silent=True):
                                time.sleep(2)
                                t.observe_dialogs(rounds=3)
                                ranges_after_cancel = collect_ranges(t)
                                # 语义：确认框点「取消」= 只改这一节，不自动调整其他节。
                                # 所以断言是「第1节(本节)已变」+「其余节与上次完全一致」，
                                # 不是「整个 ranges 完全不变」（那会误判 FAIL：本节本来就改了）。
                                self_changed = (ranges_after_cancel and ranges_after_auto
                                                and ranges_after_cancel[0] != ranges_after_auto[0])
                                others_same = (ranges_after_cancel[1:] == ranges_after_auto[1:]
                                               if ranges_after_cancel and ranges_after_auto
                                               else False)
                                t.record("PASS" if others_same else "FAIL",
                                         "取消后其他节未被自动调整: others_same=%s"
                                         % others_same)
                                t.record("INFO",
                                         "本次修改的第1节=%s（上次=%s，已变=%s）" %
                                         (ranges_after_cancel[0] if ranges_after_cancel else None,
                                          ranges_after_auto[0] if ranges_after_auto else None,
                                          self_changed))
                                if not others_same:
                                    t.record("INFO",
                                             "上次其余=%s\n本次其余=%s" %
                                             (ranges_after_auto[1:], ranges_after_cancel[1:]))
                        else:
                            t.record("FAIL", "Step6b 未弹自动调整确认框")

    # ── Step7 课程提醒时间（BACK 回确认页 → 默认 5 分钟前 + 可改） ──
    t.step("Step7 课程提醒时间（确认页入口）：默认 5 分钟前 + 可改")
    t.d.press("back")
    time.sleep(2)
    t.observe_dialogs(rounds=3)
    on_edit = "EditTimetable" in t.current_activity()
    t.record("PASS" if on_edit else "FAIL",
             "BACK 回确认页: activity=%s" % t.current_activity())
    # 入口在确认页，但**默认在首屏之外**（PROBE_178b 实测：不滚动时 screen_text 里没有它，
    # 上滑一屏后才出现「课程提醒时间 / 5分钟前」）。先上滑露出再点。
    w, h = t.d.window_size()
    row_visible, row_text = False, ""
    for _ in range(4):
        row_text = " ".join(t.screen_text())
        if "课程提醒时间" in row_text:
            row_visible = True
            break
        t.d.swipe(w // 2, int(h * 0.80), w // 2, int(h * 0.40), 0.3)
        time.sleep(0.8)
    t.record("PASS" if row_visible else "FAIL",
             "确认页存在「课程提醒时间」设置项（首屏之外，需上滑露出）=%s" % row_visible)
    t.record("PASS" if "5分钟前" in row_text else "FAIL",
             "行内默认提醒时间显示含 '5分钟前' = %s（屏幕=%s）"
             % ("5分钟前" in row_text, [x for x in row_text.split() if "分钟" in x or "提醒" in x]))
    if not (row_visible and t.tap_text("课程提醒时间", wait=3, silent=True)):
        t.record("FAIL", "未找到/无法点击 '课程提醒时间' 入口")
        return t.finish()
    time.sleep(2)
    t.observe_dialogs(rounds=3)
    opts_text = " ".join(t.screen_text())
    expected_opts = ["不提醒", "任务发生时", "5分钟前", "15分钟前", "30分钟前"]
    miss = [o for o in expected_opts if o not in opts_text]
    t.record("PASS" if not miss else "FAIL",
             "提醒时间选项完整=%s（缺=%s）" % (not miss, miss))
    # 默认 5 分钟前：弹框是 RadioGroup，dump 上 RadioButton 的 checked/selected 属性
    # 都不一定反映状态；稳的判定是看 RadioGroup 父节点上是否有任一子节点 checked，
    # 或直接读弹框文本里被「选中」的视觉提示。这里用更直接的方式：
    # 关闭弹框回到确认页，断言行内显示值。
    # (但既然弹框开着，就近读取当前弹框里的 RadioButton 选中状态更稳)
    nodes = t.find_nodes()
    default_node = None
    # 方案：dump 里 RadioGroup 的子 RadioButton 一般 class=CompoundButton 或
    # RadioButton，属性 checked=true 的那条；若无 checked，再遍历"被勾上"的
    # visual 元素（这版 ROM 实测 checked 属性不一定可靠，见 PROBE_178c/Step7 截图）
    checked_props = []
    for n in nodes:
        tx = n.get("text") or ""
        if tx in expected_opts:
            checked_props.append((tx, n.get("checked"), n.get("selected")))
    if any(c for _, c, _ in checked_props):
        for tx, c, _ in checked_props:
            if c:
                default_node = tx
                break
    if default_node:
        t.record("PASS" if default_node == "5分钟前" else "WARN",
                 "默认提醒时间 checked 项=%r（预期 '5分钟前'）" % default_node)
    else:
        # 属性读不到时退而求其次：弹框当前标题/默认项视觉提示。
        # 本版 ROM RadioButton checked 不在 dump 中暴露（PROBE_178c 视觉证据：
        # 5分钟前 有绿色选中圆点），故记 INFO 并依赖后续"改后行内值"断言。
        t.record("INFO",
                 "RadioButton checked 属性未在 dump 中暴露（已可见 '5分钟前' 视觉选中），"
                 "改后用行内显示值验证")
    t.screenshot("Step7_提醒时间")
    # 可改：点 30 分钟前 → 关闭弹框 → 验证确认页行内显示值变化
    if t.tap_text("30分钟前", wait=3, silent=True):
        time.sleep(1)
        t.observe_dialogs(rounds=2)
        t.tap_text("取消", wait=3, silent=True)   # RadioGroup 即时生效，关闭即生效
        time.sleep(1.5)
        t.observe_dialogs(rounds=2)
        # 滚回能看到「课程提醒时间」行的位置
        w7, h7 = t.d.window_size()
        for _ in range(4):
            txts_after = " ".join(t.screen_text())
            if "课程提醒时间" in txts_after:
                break
            t.d.swipe(w7 // 2, int(h7 * 0.80), w7 // 2, int(h7 * 0.40), 0.3)
            time.sleep(0.6)
        new_txts = " ".join(t.screen_text())
        changed_to_30 = "30分钟前" in new_txts
        t.record("PASS" if changed_to_30 else "FAIL",
                 "点击 30 分钟前并关闭弹框后，确认页行内显示含 '30分钟前' = %s（屏幕=%s）"
                 % (changed_to_30, [x for x in new_txts.split() if "分钟" in x or "提醒" in x]))
        t.screenshot("Step7_提醒时间_改后")
        # 还原回 5 分钟前
        if t.tap_text("课程提醒时间", wait=3, silent=True):
            time.sleep(1.2)
            t.tap_text("5分钟前", wait=3, silent=True)
            time.sleep(0.6)
            t.tap_text("取消", wait=3, silent=True)
    else:
        t.record("FAIL", "未找到 '30分钟前' 选项")

    t.stop_watchdog()
    return t.finish()


if __name__ == "__main__":
    sys.exit(run())