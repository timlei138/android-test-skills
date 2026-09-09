#!/usr/bin/env python3
"""视觉定位点击（Vision Tap）：SoM 网格 / 归一化坐标两种定位策略。

服务对象：view tree（rid/text/desc）与 OCR 都无法定位的元素——
Canvas、自定义滚轮、色盘、无文字图标、WebView 私有控件。

策略（vision.json 的 tap_strategy 显式配置优先，auto 按模型名启发）：
- som         通用多模态模型：外扩画布画网格 + 行列标签，模型报格子引用，
              纯索引换算格子中心 x_img = int((col_idx + 0.5) * cell_w)，
              device = (x_img, y_img) + offset。只接受格子引用
              （col/row → cell 两层容错），不支持 x/y 像素回退。
- coordinate  专用 GUI/grounding 模型：模型直接输出 0-1000 归一化坐标，
              device_x = x / 1000 * img_w / scale + offset_x（允许压缩）。

坐标换算不变式：
- SoM 路径不压缩（scale=1.0）：网格画在送模前最后一张图上；margin 仅用于
  绘图（外扩画布放标签），不参与坐标换算。
- 归一化坐标路径输出的是比例而非像素，允许 resize_for_vision 压缩。

本模块不含任何设备交互：定位坐标的计算与解析在此，截图/点击由
TestCase.tap_vision 编排（见 test_framework.py）。
"""
import re
from dataclasses import dataclass

from PIL import Image, ImageDraw

DEFAULT_STRATEGY = "som"

# 模型名关键字表（resolve_strategy 的 auto 启发用）：
# coordinate 只放已验证的专用 GUI/grounding 模型，避免 "gui" 这类过宽词
# 把通用 VLM 误切到坐标策略；som 匹配的通用多模态模型走网格。
VISION_STRATEGIES = {
    "coordinate": ("autoglm", "qwen2-vl", "showui", "oscopilot", "cogagent"),
    "som": ("gpt-4o", "deepseek", "claude", "gemini", "yi-vl", "llava"),
}

# 列标签字母池（cols 上限 8，A-H；余量为解析容错留到 J）
_COL_LETTERS = "ABCDEFGHIJ"


def resolve_strategy(model_name: str, explicit: str | None = None) -> str:
    """定位策略选择：显式配置（som/coordinate）优先；空串/None/auto 按模型名
    关键字启发；非法值记 WARN 回退默认；无模型名时回落 som（Agent 注入路径
    没有模型名可猜）。运行时不跨策略静默降级，尊重用户显式选择。"""
    if explicit in ("som", "coordinate"):
        return explicit
    if explicit and explicit != "auto":
        print(f"[WARN] tap_strategy={explicit!r} 非法，回退到 {DEFAULT_STRATEGY}")
        return DEFAULT_STRATEGY
    name = (model_name or "").lower()
    for strategy, keywords in VISION_STRATEGIES.items():
        if any(k in name for k in keywords):
            return strategy
    return DEFAULT_STRATEGY


# ── SoM 网格（策略 A，复刻 AiAgentTest perceive_tools 并适配）─────────


@dataclass
class SOMGridMeta:
    cols: int
    rows: int
    margin: int      # 外扩画布像素，仅用于绘图，不参与坐标换算
    cell_w: float
    cell_h: float


def _grid_size(img_w: int, img_h: int) -> tuple[int, int]:
    """行列自适应（代码内常量，不暴露配置，避免配置面扩大导致误配）：
    裁剪图越小格子越少。cols = clamp(img_w//60, 4, 8)，rows 同理上限 12。"""
    cols = min(8, max(4, img_w // 60))
    rows = min(12, max(4, img_h // 60))
    return cols, rows


def _draw_som_grid(pil_img: Image.Image, margin: int = 30):
    """在截图外扩画布上画 SoM 网格（半透明灰线 + 红色行列标签）。

    返回 (带网格的图, SOMGridMeta)。网格画在送模前的最后一张图上；
    标签画在外扩 margin 区，不遮挡原图内容（小目标保护）。
    """
    img = pil_img.convert("RGB")
    w, h = img.size
    cols, rows = _grid_size(w, h)
    cell_w, cell_h = w / cols, h / rows
    canvas = Image.new("RGB", (w + 2 * margin, h + 2 * margin), (255, 255, 255))
    canvas.paste(img, (margin, margin))
    # 半透明灰网格线：RGBA overlay 合成，避免实线遮挡小目标
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)
    line = (128, 128, 128, 110)
    for i in range(1, cols):
        x = margin + int(round(i * cell_w))
        dr.line([(x, margin), (x, margin + h)], fill=line, width=2)
    for j in range(1, rows):
        y = margin + int(round(j * cell_h))
        dr.line([(margin, y), (margin + w, y)], fill=line, width=2)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    # 行列标签（红色）：列字母画顶部 margin 区，行号画左侧 margin 区
    dr = ImageDraw.Draw(canvas)
    for i in range(cols):
        cx = margin + int(round((i + 0.5) * cell_w))
        dr.text((cx - 4, margin // 2 - 7), _COL_LETTERS[i], fill=(220, 0, 0))
    for j in range(rows):
        cy = margin + int(round((j + 0.5) * cell_h))
        dr.text((margin // 2 - 4, cy - 7), str(j + 1), fill=(220, 0, 0))
    return canvas, SOMGridMeta(cols=cols, rows=rows, margin=margin,
                               cell_w=cell_w, cell_h=cell_h)


def _col_index(col) -> int | None:
    """列引用 → 0 基索引：单字母（A=0）；数字按 1 基（与 cell 引用一致）。"""
    if col is None:
        return None
    s = str(col).strip()
    if len(s) == 1 and s.isalpha():
        i = ord(s.upper()) - ord("A")
        return i if 0 <= i < len(_COL_LETTERS) else None
    try:
        i = int(s) - 1
        return i if i >= 0 else None
    except (TypeError, ValueError):
        return None


def _row_index(row) -> int | None:
    """行引用 → 0 基索引：数字按 1 基（第 3 行 → 索引 2）。"""
    if row is None:
        return None
    try:
        i = int(str(row).strip()) - 1
        return i if i >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_som_response(data, meta: SOMGridMeta):
    """容错解析链 col/row → cell；返回裁剪图坐标系下的格子中心 (x_img, y_img)。

    换算：x_img = int((col_idx + 0.5) * cell_w)，margin 不参与公式。
    只接受格子引用；模型直接返回像素/归一化坐标（x/y 键）视为解析失败
    返回 None——不在 margin 语义上开例外口子。越界引用同样返回 None。
    """
    col, row = None, None
    if isinstance(data, dict):
        col, row = data.get("col"), data.get("row")
        if (col is None or row is None) and isinstance(data.get("cell"), str):
            m = re.fullmatch(r"\s*([A-Ja-j])\s*(\d{1,2})\s*", data["cell"])
            if m:
                col, row = m.group(1), m.group(2)
    ci, ri = _col_index(col), _row_index(row)
    if ci is None or ri is None:
        return None
    if not (0 <= ci < meta.cols) or not (0 <= ri < meta.rows):
        return None
    return int((ci + 0.5) * meta.cell_w), int((ri + 0.5) * meta.cell_h)


_SOM_PROMPT = """这是一张带 SoM（Set-of-Mark）定位网格的 Android 截图。
网格线和边缘的行列标签【不是界面内容】，仅用于定位：顶部字母标列（从左到右），左侧数字标行（从上到下）。
请找到目标「{desc}」所在的一个格子。
- 若目标跨多个格子，报告其中心所在的格子。
- 只返回 JSON：{{"col": "列字母", "row": 行号数字, "reason": "简短理由"}}。
- 例如目标中心在 C 列第 5 行：{{"col": "C", "row": 5, "reason": "..."}}。
"""


def _som_tap(vision, screen_image, description, timeout=None):
    """SoM 网格定位 → 设备绝对坐标 (x, y) + reason。

    不变式：screen_image 必须未压缩（scale=1.0，调用方保证）——网格画在
    送模前最后一张图上，格子中心纯索引换算后 + offset 即设备坐标。
    解析失败抛 ValueError（由 tap_vision catch 后降级 WARN）。
    vision 参数为 VisionProvider（或任何 ask_json 兼容对象）。
    """
    from screenshot import encode_base64
    som_img, meta = _draw_som_grid(screen_image.image)
    data = vision.ask_json(
        _SOM_PROMPT.format(desc=description),
        encode_base64(som_img),
        fields=["col", "row", "reason"],
        temperature=0.0,   # 定位任务需要确定性采样
        timeout=timeout,
    )
    if not isinstance(data, dict):
        raise ValueError(f"SoM 响应不是 JSON 对象: {str(data)[:120]}")
    pt = _parse_som_response(data, meta)
    if pt is None:
        raise ValueError(f"SoM 响应无法解析为合法格子引用: {str(data)[:120]}")
    x_img, y_img = pt
    return (int(round(x_img + screen_image.offset[0])),
            int(round(y_img + screen_image.offset[1])),
            str(data.get("reason") or ""))


# ── 归一化坐标（策略 B，参考 Open-AutoGLM）───────────────────────────

_COORD_PROMPT = """你要在手机上完成点击操作。截图尺寸为 {w}x{h}。
请返回目标「{desc}」的中心点坐标。
坐标必须是 0-1000 的归一化整数：左上角为 [0,0]，右下角为 [1000,1000]。
只返回 JSON: {{"x": 0到1000, "y": 0到1000, "reason": "简短理由"}}。
"""


def _coordinate_tap(vision, screen_image, description, timeout=None):
    """归一化坐标定位 → 设备绝对坐标 (x, y) + reason（专用 GUI/grounding 模型）。

    允许压缩（输出是比例而非像素）：device_x = x/1000 * img_w / scale + offset_x。
    兼容两种输出：JSON {"x","y"} 与 Open-AutoGLM 动作语法
    do(action="Tap", element=[x, y])。解析失败抛 ValueError
    （由 tap_vision catch 后降级 WARN，不跨策略静默降级）。
    """
    from screenshot import encode_base64
    w, h = screen_image.image.size
    data = vision.ask_json(
        _COORD_PROMPT.format(w=w, h=h, desc=description),
        encode_base64(screen_image),
        fields=["x", "y", "reason"],
        temperature=0.0,
        timeout=timeout,
    )
    parsed = _parse_normalized(data)
    if parsed is None:
        raise ValueError(f"坐标响应无法解析: {str(data)[:120]}")
    nx, ny = parsed
    s = screen_image.scale
    reason = str(data.get("reason") or "") if isinstance(data, dict) else ""
    return (int(round(nx / 1000 * w / s + screen_image.offset[0])),
            int(round(ny / 1000 * h / s + screen_image.offset[1])),
            reason)


def _parse_normalized(data):
    """从模型响应提取 0-1000 归一化 (x, y)；失败或越界返回 None。

    兼容 Open-AutoGLM 动作语法：模型把 do(action="Tap", element=[512,384])
    塞进任意字段值时，用正则提取 element（模型输出不保证是合法 Python，
    不用 ast.literal_eval）。越界（<0 或 >1000）视作解析失败，与 SoM 路径
    的越界引用返回 None 对齐，避免静默点到屏外。
    """
    def _in_range(x, y):
        try:
            return 0 <= int(x) <= 1000 and 0 <= int(y) <= 1000
        except (TypeError, ValueError):
            return False

    if isinstance(data, dict):
        try:
            x, y = int(data["x"]), int(data["y"])
            if _in_range(x, y):
                return x, y
        except (KeyError, TypeError, ValueError):
            pass
        for v in data.values():
            if isinstance(v, str) and "element" in v:
                m = re.search(r"do\s*\(\s*action\s*=\s*['\"]Tap['\"]\s*,\s*"
                              r"element\s*=\s*\[*(\d+)\s*,\s*(\d+)\]*\s*\)", v)
                if m and _in_range(m.group(1), m.group(2)):
                    return int(m.group(1)), int(m.group(2))
    return None


# ── 弹窗 bounds 识别（视觉定位的裁剪辅助，参考 AiAgentTest）──────────


def find_dialog_bounds(xml: str, screen_size) -> tuple | None:
    """从 UI 树识别弹窗容器 bounds（tap_vision 的 crop_dialog 辅助）。

    启发式：找 class 含 "Panel"（parentPanel/customPanel 等弹窗容器惯例）
    且面积占屏 5%~90%（排除全屏容器与碎块）的节点，多个时取面积最大者。
    找不到返回 None，调用方回退全屏——识别失败不阻塞（参考实现行为）。
    """
    if not xml:
        return None
    sw, sh = screen_size
    screen_area = float(sw) * float(sh)
    if screen_area <= 0:
        return None
    best, best_area = None, 0
    for tag in re.finditer(r"<node\b[^>]*>", xml):
        s = tag.group(0)
        mc = re.search(r'class="([^"]*)"', s)
        mb = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', s)
        if not mc or not mb or "panel" not in mc.group(1).lower():
            continue
        x1, y1, x2, y2 = map(int, mb.groups())
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            continue
        area = w * h
        if area <= 0.05 * screen_area or area >= 0.9 * screen_area:
            continue
        if area > best_area:
            best, best_area = (x1, y1, x2, y2), area
    return best
