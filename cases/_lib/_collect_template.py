#!/usr/bin/env python3
"""采集骨架模板：新页面探索的「第一步」只做采集，不做试探。

工作流（详见 SKILL.md「新页面探查与用例生成 SOP」）：
  ① 本脚本真机采集（1-3 分钟）：goto 复用 _flow 直达已知页 / app_start 冷启首页起步，
     未知下钻一轮一跳 probe_page(label) 落盘；开头 set_trace() 开采集会话档案。
  ② 离线生成库存 + 逐句预检（零真机）：
        python cases/_lib/inventory.py <pkg> inventory        # 页面有什么料
        python cases/_lib/inventory.py <pkg> verify <label>  # 断言先查缓存命中
            附加 --rids tv_x --texts "文案" --re '正则'
  ③ 对照库存 + _flow/知识卡模式合成正式用例 → 真机终验一次。

用法：复制本文件到 cases/<包名>/ 下改名（如 _collect_176.py），改 PKG 与导航段后运行。
      探完即删（沉淀在 knowledge/<包名>.md，不留一次性脚本）。
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))

from test_framework import TestCase          # noqa: E402

# 被测 App 包名（改这里）
PKG = "com.zui.calendar"

# ========== 全新 App 无 _flow 的起步方式（二选一，有 _flow 直接跳过） ==========
# from test_framework import TestCase
# t = TestCase("探索_<App名>")                 # 自动绑设备/唤醒
# t.d.app_start(PKG)                            # 冷启动目标 App
# t.set_trace()                                 # 开采集会话档案
# info = t.probe_page("首页", ocr=False)        # 首页落盘 → 看摘要定下一跳
# =============================================================================


def collect():
    t = TestCase("采集_新页面")
    t.start_watchdog(policy="allow")
    t.set_trace()                              # 会话档案：dump 快照 + events 全落盘

    # ── 已知导航（复用 _flow 厚流程；参照 knowledge/<包名>.md）──
    # from _flow import goto_xxx
    # if not goto_xxx(t, ...):
    #     return t.finish()
    t.d.app_start(PKG)                         # 模板默认：无 _flow 时冷启动起步
    time.sleep(2)

    # ── 未知下钻：一轮一跳 ─────────────────────────────────────
    # 1) 当前页先落盘：info = t.probe_page("页面A")  打印摘要
    # 2) 看摘要里可交互项，挑下一跳文本追加一行：
    #        if not t.tap_text("下一跳入口", wait=4): print("没找到，检查上一步摘要")
    # 3) 再 probe_page("页面B") → 如此推进；每轮真机 2-5s
    # 4) 关键动作（保存/提交/改配置后确定等）后立即抓反馈落盘——
    #    探索要走完业务闭环，动态文案（toast）必须此刻记录，用例期不许猜：
    #        t.tap_text("完成")
    #        texts, shot = t.capture_toast()        # 截屏 OCR，截图自动留证
    #        print("动作后 toast/屏面文本:", texts)
    info = t.probe_page("页面A")               # ← 首跳：把"页面A"换成语义名
    print("页面A texts:", info["texts"][:30])
    print("页面A rids :", info["rids"][:30])

    # Canvas / UI 树读不到的区域需要补 OCR 时：probe_page(label, ocr=True)
    #（OCR 一次 ~2.5s 且混背景，能不用就不用）

    t.stop_watchdog()
    return t.finish()


if __name__ == "__main__":
    sys.exit(0 if collect() else 1)
