#!/usr/bin/env python3
"""
确定性 Canvas 滚轮控制器 v2 —— 点按版（用户建议：点按未选中数字比滑动有效）
原理: 点按上方行 = -1，点按下方行 = +1；目标可见时直接点按目标数字
每步重读验证，零惯性零过冲。
目标: 第1节 = 08:30 - 09:15
"""
import subprocess
import time

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

import sys as _sys

_COL_X = {"开始时": 1178, "开始分": 1345, "结束时": 1694, "结束分": 1865}
_COL_SPAN = {"开始时": 24, "开始分": 60, "结束时": 24, "结束分": 60}


def _parse_targets():
    """python set_time_tap.py 9 0 9 40"""
    if len(_sys.argv) >= 5:
        h1, m1, h2, m2 = map(int, _sys.argv[1:5])
    else:
        h1, m1, h2, m2 = 8, 30, 9, 15
    return [("开始时", h1), ("开始分", m1), ("结束时", h2), ("结束分", m2)]


TARGETS = _parse_targets()
COLS = [(name, _COL_X[name], target, _COL_SPAN[name]) for name, target in TARGETS]
MID_Y = 1025       # 选中行 y
ROW_GAP = 166      # 行间距
_ocr = RapidOCR()


def screencap():
    subprocess.run(["adb", "exec-out", "screencap", "-p"],
                   stdout=open("/tmp/pk.png", "wb"))
    return Image.open("/tmp/pk.png")


def read_col(cx):
    img = screencap()
    region = img.crop((cx - 42, 800, cx + 42, 1250))
    region = region.resize((region.width * 3, region.height * 3), Image.LANCZOS)
    res, _ = _ocr(np.asarray(region))
    rows = []
    for box, text, conf in res or []:
        ys = [p[1] for p in box]
        cy = sum(ys) / 4 / 3 + 800
        if text.strip().isdigit() and float(conf) > 0.5:
            rows.append((cy, int(text.strip())))
    rows.sort()
    return rows


def tap(cx, y):
    subprocess.run(["adb", "shell", "input", "tap", str(cx), str(y)],
                   capture_output=True)


def adjust(cx, target, span):
    """点按调整: 目标可见→直接点目标；否则点方向行，每步重读"""
    for i in range(span):
        rows = read_col(cx)
        if not rows:
            return False
        mid = min(rows, key=lambda r: abs(r[0] - MID_Y))
        cur = mid[1]
        if cur == target:
            return True
        # 最短方向
        d = (target - cur) % span
        if d > span / 2:
            d -= span
        # 若目标当前可见，直接点按它（一次到位）
        hit = [y for y, v in rows if v == target]
        if hit:
            tap(cx, hit[0])
        else:
            if d > 0:  # 需增大 → 点下方行
                below = [r for r in rows if r[0] > mid[0]]
                ty = min(below, key=lambda r: r[0])[0] if below else mid[0] + ROW_GAP
            else:      # 需减小 → 点上方行
                above = [r for r in rows if r[0] < mid[0]]
                ty = max(above, key=lambda r: r[0])[0] if above else mid[0] - ROW_GAP
            tap(cx, ty)
        time.sleep(0.35)
    return False


def main():
    print("读取当前选择器状态:")
    for name, cx, _, _ in COLS:
        rows = read_col(cx)
        mid = min(rows, key=lambda r: abs(r[0] - MID_Y)) if rows else None
        print(f"  {name}(x={cx}): {mid[1] if mid else '?'}  可见={[v for _, v in rows]}")
    print("\n开始点按调整:")
    expect = dict(TARGETS)
    for name, cx, target, span in COLS:
        ok = adjust(cx, target, span)
        print(f"  {name}: {'✓' if ok else '✗'}")
    print("\n=== 最终验证 ===")
    vals = {}
    for name, cx, _, _ in COLS:
        rows = read_col(cx)
        mid = min(rows, key=lambda r: abs(r[0] - MID_Y)) if rows else None
        vals[name] = mid[1] if mid else None
    print(vals)
    print("达成目标:", "✅" if vals == expect else f"❌ {vals}")
    if vals == expect:
        print("\n点击确定...")
        tap(1795, 1403)
        time.sleep(1.5)
        print("已点击确定")


if __name__ == "__main__":
    main()
