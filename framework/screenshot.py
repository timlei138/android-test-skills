#!/usr/bin/env python3
"""公共截图管线：采集、裁剪、压缩、编码（几何信息显式化）。

所有视觉 / OCR 操作的图像处理公共层：不依赖任何视觉模型，只处理图像。
坐标换算契约（docs/VISION_TAP_PLAN.md §3.6）——ScreenImage 的 image 像素
坐标 P 与设备屏幕坐标 D 满足：

    P = (D - offset) * scale

逆变换（模型/OCR 返回的局部坐标 → 设备坐标，点击前必须换算）：

    device_x = local_x / scale + offset_x
    device_y = local_y / scale + offset_y

不变式：
- 任何返回 ScreenImage 的操作（crop/resize）都同时更新 offset 与 scale，
  禁止只返回图而丢掉几何信息；
- capture() 必须复用 TestCase._screencap_bytes()（继承 ensure_awake 锁屏
  防护与 serial 绑定），禁止旁路裸拼 adb screencap；
- SoM 定位路径（vision_tap._som_tap）不压缩（scale=1.0），网格画在送模
  前最后一张图上；归一化坐标路径允许压缩（输出是比例而非像素）。
"""
import base64
import io
from dataclasses import dataclass

from PIL import Image


@dataclass
class ScreenImage:
    image: Image.Image              # PIL 图（可能已被裁剪/压缩）
    png_bytes: bytes                # 与 image 同步的 PNG 编码
    original_size: tuple[int, int]  # 全屏原始尺寸 (w, h)
    offset: tuple[float, float]     # 裁剪左上角相对全屏的偏移（未裁剪为 (0, 0)）
    scale: float                    # image 相对裁剪区原始像素的缩放（未压缩为 1.0）

    @classmethod
    def from_bytes(cls, raw: bytes) -> "ScreenImage":
        """从 screencap PNG 字节构造（未裁剪、未压缩基准态）。"""
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return cls(image=img, png_bytes=bytes(raw),
                   original_size=img.size, offset=(0.0, 0.0), scale=1.0)

    def reencode(self) -> None:
        """把当前 image 重新编码为 png_bytes（crop/resize 后调用）。"""
        buf = io.BytesIO()
        self.image.save(buf, format="PNG")
        self.png_bytes = buf.getvalue()

    def to_device(self, local_x: float, local_y: float) -> tuple[int, int]:
        """局部（当前 image）像素坐标 → 设备屏幕坐标。"""
        return (int(round(local_x / self.scale + self.offset[0])),
                int(round(local_y / self.scale + self.offset[1])))


def capture(device) -> ScreenImage:
    """截取全屏。device 为 TestCase 实例（鸭子类型：有 _screencap_bytes()），
    复用其截屏统一入口以继承 ensure_awake() 锁屏防护。"""
    return ScreenImage.from_bytes(device._screencap_bytes())


def crop_bounds(src: ScreenImage, bounds: tuple, padding: int = 20) -> ScreenImage:
    """按 bounds（设备屏幕坐标 (x1,y1,x2,y2)）外扩 padding 裁剪。

    padding 单位为当前 image 像素（capture 后 scale=1 时即设备像素，与旧
    _vision_crop_bytes 的 20px 行为一致）。offset 自动累加；bounds 非法
    （越界后为空）时原样返回 src，不抛异常——裁剪是优化不是前提。
    """
    w, h = src.image.size
    ox, oy = src.offset
    s = src.scale
    x1, y1, x2, y2 = bounds
    # 设备坐标 → 当前 image 像素坐标
    px1 = int(round((x1 - ox) / s)) - padding
    py1 = int(round((y1 - oy) / s)) - padding
    px2 = int(round((x2 - ox) / s)) + padding
    py2 = int(round((y2 - oy) / s)) + padding
    px1, py1 = max(0, px1), max(0, py1)
    px2, py2 = min(w, px2), min(h, py2)
    if px2 - px1 < 1 or py2 - py1 < 1:
        return src
    img = src.image.crop((px1, py1, px2, py2))
    # 新 offset = 裁剪点对应的设备坐标：P' = (D - offset') * scale
    out = ScreenImage(image=img, png_bytes=b"",
                      original_size=src.original_size,
                      offset=(px1 / s + ox, py1 / s + oy), scale=s)
    out.reencode()
    return out


def resize_for_vision(src: ScreenImage, max_side: int = 1280) -> ScreenImage:
    """等比压缩（长边 ≤ max_side）。scale 自动累乘；无需压缩时原样返回。

    SoM 定位路径禁止调用本函数（格子必须等分原始像素，见模块头不变式）；
    归一化坐标路径与普通视觉问答可用来控制 token。
    """
    w, h = src.image.size
    m = max(w, h)
    if m <= max_side or m == 0:
        return src
    ratio = max_side / m
    img = src.image.resize((max(1, int(round(w * ratio))),
                            max(1, int(round(h * ratio)))), Image.LANCZOS)
    out = ScreenImage(image=img, png_bytes=b"",
                      original_size=src.original_size,
                      offset=src.offset, scale=src.scale * ratio)
    out.reencode()
    return out


def encode_base64(image) -> str:
    """统一编码为 data URI。接受 ScreenImage / PIL Image / PNG bytes / 文件路径。

    返回值可直接作为 vision.Vision.ask(prompt, image=...) 的 image 参数
    （vision._encode_image 已支持 data: 前缀短路）。
    """
    if isinstance(image, ScreenImage):
        raw = image.png_bytes
    elif isinstance(image, Image.Image):
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        raw = buf.getvalue()
    elif isinstance(image, (bytes, bytearray)):
        raw = bytes(image)
    elif isinstance(image, str):
        with open(image, "rb") as f:
            raw = f.read()
    else:
        raise TypeError(f"不支持的图像类型: {type(image)!r}")
    return "data:image/png;base64," + base64.b64encode(raw).decode()
