#!/usr/bin/env python3
"""联想日历_172 用例：图库导入解析 → 确认页 → 返回重进 → 完成 → 列表验证

步骤（用户口述）:
  1. 从图库选择固定课程表图片并完成裁剪后触发解析
  2. 点击"下一步"按钮 → 进入课程表基本信息确认页
  3. 点击左上角返回按钮并重新进入确认流程
  4. 在课程表基本信息页面点击右上角"完成"
  5. 查看课程表列表（列表显示新课程表且存在"当前"标签）

前置链路（选图/裁剪/解析）复用本 App 的 _flow.py，
本文件只写断言逻辑（_flow 准入规则：≥2 用例共享才提取进去）。
"""
import os
import sys

# 用户原始输入（口述用例）：run_case.py 提取后入库
USER_INPUT = """用这个skills执行测试用例 联想日历_172
操作步骤
1. 从图库选择固定课程表图片并完成裁剪后触发解析
2. 点击"下一步"按钮
3. 点击左上角返回按钮并重新进入确认流程
4. 在课程表基本信息页面点击右上角"完成"
5. 查看课程表列表
预期结果
1. 解析流程正常完成
2. 进入课程表基本信息确认页
3. 返回操作正常
4. 点击完成后进入课程表列表
5. 列表显示新课程表列表存在当前标签"""

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))
from test_framework import TestCase
from _flow import (BTN_FINISH, BTN_NEXT, NAME_RID, PAGE_CONFIRM,
                   goto_图库导入_基本信息确认页)

PKG = os.path.basename(_HERE)


def run():
    t = TestCase("联想日历_172")

    # ── 环境检查：解析依赖联网（知识卡：图库导入解析需联网）────────────
    t.step("前提-环境检查")
    if not t.has_network():
        t.blocked("设备无网络，图片解析无法完成（解析需联网）")
        return t.finish()
    t.record("PASS", "设备网络可用，具备解析条件")

    # ── Step1: 图库选图 → 裁剪 → 触发解析（前置链路复用 _flow）────────
    t.step("Step1 从图库选择课程表图片并完成裁剪触发解析")
    if not goto_图库导入_基本信息确认页(t, pm_clear=True, timeout=60):
        # 链路内部已按原因 record BLOCKED（如无素材/解析超时），不额外记 FAIL，
        # 否则会把环境类 BLOCKED 误升级成 FAIL（最终结论 FAIL > BLOCKED）。
        return t.finish()
    t.record("PASS", "解析流程正常完成（已走完 选图→裁剪→解析→进入确认页 链路）")
    t.screenshot("01_解析完成进入确认页")

    # ── Step2: 验证已进入「确认课程表基本信息」页 ───────────────────────
    t.step("Step2 点击进入确认课程表基本信息页")
    on_confirm = t.wait_text(PAGE_CONFIRM, timeout=8)
    t.record("PASS" if on_confirm else "FAIL",
             f"已进入'确认课程表基本信息'页: {on_confirm}，"
             f"当前屏幕={t.screen_text()[:6]}")
    if not on_confirm:
        return t.finish()
    name_state = t.read_rid(NAME_RID)
    t.record("INFO", f"课表名称预填值: "
                     f"{name_state['text'] if name_state else '未读到'}")
    t.screenshot("02_基本信息确认页")

    # ── Step3: 点左上角返回 → 应回到「确认识别结果」页 ─────────────────
    # 知识卡：确认页返回 → 回到确认识别结果页（有"下一步"），不是课程表页/主页。
    # 返回键坐标由元素 bounds 推导，禁止写死 (123,203)。
    t.step("Step3 点击左上角返回按钮")
    back = t.el_bounds(desc="转到上一层级")
    if not back:
        # 兜底：取顶栏最左侧无文字的可点击节点（返回键通常无 text）
        for n in t.find_nodes(clickable=True):
            b = n.get("bounds_xy")
            if b and 100 <= b[1] <= 300 and not n["text"]:
                back = b
                break
    if not back:
        t.record("FAIL", f"确认页未定位到左上角返回按钮，屏幕={t.screen_text()[:6]}")
        return t.finish()
    t.tap_xy((back[0] + back[2]) // 2, (back[1] + back[3]) // 2)
    # 等「下一步」重新出现 = 回到识别结果页（条件等待，替代固定 sleep）
    back_ok = t.wait_rid(BTN_NEXT, timeout=8)
    t.record("PASS" if back_ok else "FAIL",
             f"返回操作正常（回到确认识别结果页，'下一步'重新出现: {back_ok}），"
             f"当前 Activity={t.current_activity()}")
    t.screenshot("03_返回后识别结果页")
    if not back_ok:
        return t.finish()

    # ── Step3b: 重新进入确认流程（再点「下一步」）─────────────────────
    t.step("Step3b 重新进入确认流程")
    t.require_tap_rid(BTN_NEXT, wait=8,
                      msg="重进确认流程失败：未找到'下一步'按钮")
    reenter = t.wait_text(PAGE_CONFIRM, timeout=10)
    t.record("PASS" if reenter else "FAIL",
             f"重新进入'确认课程表基本信息'页: {reenter}")
    if not reenter:
        return t.finish()
    t.screenshot("04_重进确认页")

    # ── Step4: 点右上角「完成」→ 进入课程表列表 ───────────────────────
    t.step("Step4 点击右上角完成按钮")
    finish_state = t.read_rid(BTN_FINISH)
    t.record("INFO", f"完成按钮状态: {finish_state}")
    t.require_tap_rid(BTN_FINISH, wait=8,
                      msg="未找到确认页'完成'按钮（rid=btn_finish）")
    # 等列表页特征（知识卡：列表含「全部课程表 / <课表名> / 当前 / 设置」）
    listed = t.wait_text("全部课程表", timeout=12)
    t.record("PASS" if listed else "FAIL",
             f"点击完成后进入课程表列表: {listed}，屏幕={t.screen_text()[:8]}")
    if not listed:
        return t.finish()
    t.screenshot("05_课程表列表")

    # ── Step5: 列表验证——新课程表存在 + 存在「当前」标签 ───────────────
    t.step("Step5 查看课程表列表")
    texts = t.screen_text()
    has_current = any("当前" in x for x in texts if x)
    t.record("PASS" if has_current else "FAIL",
             f"列表存在'当前'标签: {has_current}，列表内容={texts[:12]}")
    # 新课表存在：图库导入预填名「学生课程表」，或除"全部课程表"外仍有课表项
    named = [x for x in texts if x and "全部课程表" not in x
             and ("课表" in x or "课程表" in x)]
    t.record("PASS" if named else "FAIL",
             f"列表显示新导入的课程表: {named[:5]}")
    t.screenshot("06_课程表列表_当前标签")

    return t.finish()


if __name__ == "__main__":
    run()
