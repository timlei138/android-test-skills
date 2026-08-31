#!/usr/bin/env python3
"""用例脚本模板：复制本文件为 cases/你的用例名.py，按下面结构填写。
不会写？直接把用例发给我（AI），我按这个模板帮你转成脚本。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_framework import TestCase


def run():
    t = TestCase("用例名称")  # ← 改成用例编号/名称

    # ── 前置条件（可选）────────────────────────────────────────────
    # 例1: 重置 App 到首次使用状态
    # t.pm_clear("com.example.app")
    # 例2: 环境判断，不满足直接 BLOCKED
    # if not t.has_network():
    #     t.blocked("环境原因：设备无网络")
    #     return t.finish()

    # ── 步骤 1 ─────────────────────────────────────────────────────
    t.step("步骤1: 打开XX页面")
    # 元素操作（选一种）
    # t.tap_text("确定")                    # 按文字点
    # t.tap_rid("com.example:id/btn")      # 按 resource-id 点
    # t.tap_xy(100, 200)                   # 按坐标点（慎用）
    # t.input_text("com.example:id/edit", "中文输入")
    # 断言
    # t.assert_text("com.example:id/result", "期望值", "说明")
    # t.assert_switch("com.example:id/switch", "true", "开关打开")
    t.screenshot("01_步骤1证据")           # 截图留证

    # ── 步骤 2 ─────────────────────────────────────────────────────
    t.step("步骤2: ...")
    # t.record("PASS", "说明")   /   t.record("FAIL", "说明")
    # t.blocked("无法执行原因")

    # ── 生成报告 ───────────────────────────────────────────────────
    return t.finish()


if __name__ == "__main__":
    run()
