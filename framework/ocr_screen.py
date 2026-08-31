#!/usr/bin/env python3
"""截图 + OCR 带坐标输出（venv313 环境，rapidocr 兼容处理）"""
import sys
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shot.png"
y_min = int(sys.argv[2]) if len(sys.argv) > 2 else 0
y_max = int(sys.argv[3]) if len(sys.argv) > 3 else 99999

img = Image.open(path)
w, h = img.size
scale = 1280 / max(w, h)
img2 = img.resize((int(w * scale), int(h * scale)))
r = RapidOCR()
res, _ = r(np.asarray(img2))
if not res:
    print("(无文字)")
    sys.exit(0)
for box, text, conf in res:
    xs = [p[0] / scale for p in box]
    ys = [p[1] / scale for p in box]
    cx, cy = int(sum(xs) / 4), int(sum(ys) / 4)
    if y_min <= cy <= y_max:
        print(f"({cx:5},{cy:5}) {float(conf):.2f} {text}")
