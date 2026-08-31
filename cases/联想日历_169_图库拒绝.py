#!/usr/bin/env python3
"""169-图库拒绝: 权限弹窗选拒绝 → 提示授予相册权限"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_framework import TestCase
import _perm_helper as H

def run():
    t = TestCase("联想日历_169_图库拒绝")
    t.step("前提-清理")
    t.pm_clear(H.PKG)
    time.sleep(1)
    t.step("导航-课程表页")
    if not H.navigate_to_course_table(t):
        t.record("FAIL", "导航到课程表页失败")
        return t.finish()
    t.start_watchdog(policy="deny", verbose=True)
    t.step("图库权限-拒绝")
    H.test_gallery(t, allow=False)
    t.stop_watchdog()
    return t.finish()

if __name__ == "__main__":
    run()
