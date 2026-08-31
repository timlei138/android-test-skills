#!/usr/bin/env python3
"""169-相机允许: 权限弹窗选允许 → 相机打开"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_framework import TestCase
import _perm_helper as H

def run():
    t = TestCase("联想日历_169_相机允许")
    t.step("前提-清理")
    t.pm_clear(H.PKG)
    time.sleep(1)
    t.step("导航-课程表页")
    if not H.navigate_to_course_table(t):
        t.record("FAIL", "导航到课程表页失败")
        return t.finish()
    t.start_watchdog(policy="allow", verbose=True)
    t.step("相机权限-允许")
    H.test_camera(t, allow=True)
    t.stop_watchdog()
    return t.finish()

if __name__ == "__main__":
    run()
