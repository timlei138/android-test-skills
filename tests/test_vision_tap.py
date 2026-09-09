#!/usr/bin/env python3
"""视觉定位（screenshot / vision_provider / vision_tap / tap_vision）单测。

纯逻辑测试：不连设备、不调真实视觉 API（Fake 注入固定响应）。
覆盖视觉定位坐标换算的 P0 单测清单：
- SoM 换算：格子引用 → 设备绝对坐标（margin 不参与公式）
- crop → resize 链：offset / scale 累乘正确（coordinate 策略与 OCR 用）
- resolve_strategy：显式 / 非法回退 / auto 启发 / 无模型名回落 som
- VisionProvider 路由：用户配置优先、Agent 次之、都没有时抛异常
- VisionProvider 识别本地 vLLM：仅 base_url 即判定用户配置可用
- tap_vision 契约：返回 bool；未配置时 WARN 不 ERROR；SoM 换算传导到 tap_xy
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
FW_DIR = os.path.join(os.path.dirname(TESTS_DIR), "framework")
if FW_DIR not in sys.path:
    sys.path.insert(0, FW_DIR)

# 测试隔离：工作区指向临时目录（vision.json 不存在 → 用户未配置态）。
# 必须在 import vision 之前设置——VISION_CONF_FILE 是模块级常量。
_WORKSPACE = tempfile.mkdtemp(prefix="dsh_vision_test_")
os.environ["DSH_ANDROID_TEST_DIR"] = _WORKSPACE

try:
    from PIL import Image
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    import vision as vision_mod
    import vision_provider as vp_mod
    VISION_MODULES_OK = True
except ImportError:
    vision_mod = vp_mod = None
    VISION_MODULES_OK = False

if PIL_OK:
    from screenshot import ScreenImage, crop_bounds, encode_base64, resize_for_vision
    from vision_tap import (SOMGridMeta, _coordinate_tap, _draw_som_grid,
                            _parse_som_response, _som_tap, find_dialog_bounds,
                            resolve_strategy)

try:
    import test_framework as tf
    TF_OK = True
except Exception:
    tf = None
    TF_OK = False


def _png_bytes(w, h, color=(200, 180, 160)):
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeVision:
    """VisionProvider 兼容的假视觉模型：返回预设结果，记录调用参数。"""

    def __init__(self, json_result=None, ask_ret="ask-ok", model="fake-model"):
        self.json_result = json_result
        self.ask_ret = ask_ret
        self.model = model
        self.calls = []

    def ask(self, prompt, image, temperature=None, timeout=None, **kw):
        self.calls.append({"prompt": prompt, "temperature": temperature,
                           "timeout": timeout})
        return self.ask_ret

    def ask_json(self, prompt, image, fields, temperature=None, timeout=None, **kw):
        self.calls.append({"prompt": prompt, "fields": fields,
                           "temperature": temperature, "timeout": timeout})
        if isinstance(self.json_result, Exception):
            raise self.json_result
        return self.json_result


class FakeProvider:
    """tap_vision 测试用的假 VisionProvider：直接注入 t._vision，绕开真实路由。

    results 为结果序列（ask_json 每次消费一个，只剩最后一个时重复返回），
    元素是 dict（正常返回）或 Exception（模拟调用失败）。
    """

    def __init__(self, results, available=True):
        self.results = [results] if not isinstance(results, list) else list(results)
        self.available_flag = available
        self.calls = []

    def available(self):
        return self.available_flag

    def model_name(self):
        return "deepseek-v4-flash-vision-exp"

    def tap_strategy(self):
        return ""

    def ask_json(self, prompt, image, fields, temperature=0.0, timeout=None, **kw):
        self.calls.append({"prompt": prompt, "fields": fields,
                           "temperature": temperature, "timeout": timeout})
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


# ── SoM 网格绘制与解析 ─────────────────────────────────────────────


class TestSOMGrid(unittest.TestCase):

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_grid_meta_and_canvas_size(self):
        img = Image.new("RGB", (200, 480), (255, 255, 255))
        canvas, meta = _draw_som_grid(img, margin=30)
        # 200//60=3 → clamp 4 列；480//60=8 → 8 行（行列自适应常量）
        self.assertEqual((meta.cols, meta.rows), (4, 8))
        self.assertAlmostEqual(meta.cell_w, 50.0)
        self.assertAlmostEqual(meta.cell_h, 60.0)
        self.assertEqual(meta.margin, 30)
        # 画布 = 原图外扩 2*margin（标签画在 margin 区，不遮挡原图内容）
        self.assertEqual(canvas.size, (200 + 60, 480 + 60))
        # 网格画在送模前最后一张图上：格子中心未被线覆盖处仍为原图像素
        self.assertEqual(canvas.getpixel((30 + 25, 30 + 30)), (255, 255, 255))


class TestParseSomResponse(unittest.TestCase):

    def setUp(self):
        # 裁剪图 200x480 → cols=4, rows=8, cell_w=50, cell_h=60
        self.meta = SOMGridMeta(cols=4, rows=8, margin=30, cell_w=50.0, cell_h=60.0)

    def test_col_row_letter_number(self):
        # B=索引1，行3→_row_index=int(3)-1=2 → x=int(1.5*50)=75，y=int(2.5*60)=150
        self.assertEqual(_parse_som_response({"col": "B", "row": 3}, self.meta), (75, 150))

    def test_cell_reference(self):
        self.assertEqual(_parse_som_response({"cell": "B3"}, self.meta), (75, 150))

    def test_numeric_col(self):
        # 数字列引用按 1 基（与 cell 引用语义一致）
        self.assertEqual(_parse_som_response({"col": 2, "row": "3"}, self.meta), (75, 150))

    def test_out_of_range_returns_none(self):
        self.assertIsNone(_parse_som_response({"col": "E", "row": 3}, self.meta))  # cols=4
        self.assertIsNone(_parse_som_response({"col": "B", "row": 9}, self.meta))  # rows=8
        self.assertIsNone(_parse_som_response({"cell": "Z12"}, self.meta))

    def test_pixel_xy_not_supported(self):
        # 只接受格子引用：像素/归一化坐标（x/y 键）视为解析失败，无 fallback
        self.assertIsNone(_parse_som_response({"x": 100, "y": 200}, self.meta))
        self.assertIsNone(_parse_som_response({}, self.meta))
        self.assertIsNone(_parse_som_response("junk", self.meta))


class TestSomTapConversion(unittest.TestCase):
    """P0 单测清单核心项：模拟 {"col":"B","row":3}，验证最终坐标为
    x_img = int((col_idx+0.5)*cell_w) + offset_x，margin 不参与公式。"""

    def _screen(self, w, h, offset):
        img = Image.new("RGB", (w, h), (120, 120, 200))
        return ScreenImage(image=img, png_bytes=b"", original_size=(400, 800),
                           offset=offset, scale=1.0)

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_fullscreen_no_offset(self):
        # 全屏 200x480 → cols=4/rows=8 → B3: col idx=1, row idx=2 → (75, 150)
        v = FakeVision({"col": "B", "row": 3, "reason": "目标在 B3"})
        x, y, reason = _som_tap(v, self._screen(200, 480, (0.0, 0.0)), "紫色色块",
                                timeout=30)
        self.assertEqual((x, y), (75, 150))
        self.assertEqual(reason, "目标在 B3")
        # temperature=0.0 确定性采样 + per-call timeout 传导
        self.assertEqual(v.calls[0]["temperature"], 0.0)
        self.assertEqual(v.calls[0]["timeout"], 30)

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_crop_offset_added(self):
        # 裁剪图 140x280（offset=(30,40)）→ cols=4, rows=4, cell_w=35, cell_h=70
        # B3: col idx=1, row idx=2 → 局部 (52, 175) → 设备 (82, 215)
        v = FakeVision({"col": "B", "row": 3})
        x, y, _ = _som_tap(v, self._screen(140, 280, (30.0, 40.0)), "图标")
        self.assertEqual((x, y), (82, 215))

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_parse_failure_raises(self):
        v = FakeVision({"x": 100, "y": 200})   # 非格子引用
        with self.assertRaises(ValueError):
            _som_tap(v, self._screen(200, 480, (0.0, 0.0)), "目标")


class TestCoordinateTap(unittest.TestCase):

    def _screen(self, w, h, offset, scale):
        img = Image.new("RGB", (w, h), (90, 140, 90))
        return ScreenImage(image=img, png_bytes=b"", original_size=(400, 800),
                           offset=offset, scale=scale)

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_json_normalized(self):
        # x=500/1000*200/1+100=200，y=500/1000*400/1+200=400
        s = self._screen(200, 400, (100.0, 200.0), 1.0)
        v = FakeVision({"x": 500, "y": 500, "reason": "中心"})
        self.assertEqual(_coordinate_tap(v, s, "目标")[:2], (200, 400))

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_compressed_scale(self):
        # 压缩 scale=0.5：x=250/1000*100/0.5+100=150，y=250/1000*200/0.5+200=300
        s = self._screen(100, 200, (100.0, 200.0), 0.5)
        v = FakeVision({"x": 250, "y": 250})
        self.assertEqual(_coordinate_tap(v, s, "目标")[:2], (150, 300))

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_autoglm_action_syntax(self):
        # Open-AutoGLM 动作语法容错：do(action="Tap", element=[500, 250])
        s = self._screen(200, 400, (0.0, 0.0), 1.0)
        v = FakeVision({"reply": 'do(action="Tap", element=[500, 250])'})
        self.assertEqual(_coordinate_tap(v, s, "目标")[:2], (100, 100))

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_unparseable_raises(self):
        s = self._screen(200, 400, (0.0, 0.0), 1.0)
        v = FakeVision({"nothing": "useful"})
        with self.assertRaises(ValueError):
            _coordinate_tap(v, s, "目标")


# ── 公共截图管线：offset / scale 累乘 ───────────────────────────────


class TestCropResizeChain(unittest.TestCase):

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_crop_then_resize(self):
        src = ScreenImage.from_bytes(_png_bytes(400, 800))
        self.assertEqual(src.offset, (0.0, 0.0))
        self.assertEqual(src.scale, 1.0)
        # crop (100,200,300,600) 无 padding：offset=(100,200)，图 200x400
        c = crop_bounds(src, (100, 200, 300, 600), padding=0)
        self.assertEqual(c.offset, (100.0, 200.0))
        self.assertEqual(c.scale, 1.0)
        self.assertEqual(c.image.size, (200, 400))
        # 连续 crop：offset 累加
        c2 = crop_bounds(c, (150, 250, 250, 450), padding=0)
        self.assertEqual(c2.offset, (150.0, 250.0))
        # resize 长边 400 → max_side=100：ratio=100/400=0.25，scale 累乘、offset 不变
        r = resize_for_vision(c, max_side=100)
        self.assertAlmostEqual(r.scale, 0.25)
        self.assertEqual(r.offset, (100.0, 200.0))
        self.assertEqual(r.image.size, (50, 100))
        # 换算：local (12.5, 50) → device (12.5/0.25+100, 50/0.25+200) = (150, 400)
        self.assertEqual(r.to_device(12.5, 50), (150, 400))

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_crop_padding_and_clamp(self):
        src = ScreenImage.from_bytes(_png_bytes(200, 480))
        # bounds 贴左上 + padding 20：左上 clamp 到 0，右下外扩
        c = crop_bounds(src, (0, 0, 100, 100), padding=20)
        self.assertEqual(c.offset, (0.0, 0.0))
        self.assertEqual(c.image.size, (120, 120))
        # 非法 bounds（越界后为空）→ 原样返回，不抛异常
        bad = crop_bounds(src, (500, 500, 600, 600))
        self.assertIs(bad, src)

    @unittest.skipUnless(PIL_OK, "需要 Pillow")
    def test_encode_base64_forms(self):
        raw = _png_bytes(10, 10)
        self.assertTrue(encode_base64(raw).startswith("data:image/png;base64,"))
        s = ScreenImage.from_bytes(raw)
        self.assertEqual(encode_base64(s), encode_base64(raw))
        self.assertTrue(encode_base64(Image.new("RGB", (5, 5))).startswith("data:"))
        # vision._encode_image 的 data: 短路（Vision.ask 可直接收 encode_base64 输出）
        uri = encode_base64(raw)
        self.assertEqual(vision_mod._encode_image(uri), uri)


class TestFindDialogBounds(unittest.TestCase):

    def test_panel_node_found(self):
        xml = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][200,480]"/>'
               '<node class="com.app.ParentPanel" bounds="[50,60][150,300]"/></hierarchy>')
        self.assertEqual(find_dialog_bounds(xml, (200, 480)), (50, 60, 150, 300))

    def test_fullscreen_panel_ignored(self):
        # 全屏 Panel（面积 ≥ 90% 屏）不算弹窗 → 回退 None（调用方全屏搜索）
        xml = '<node class="android.widget.Panel" bounds="[0,0][200,480]"/>'
        self.assertIsNone(find_dialog_bounds(xml, (200, 480)))

    def test_largest_panel_wins(self):
        xml = ('<node class="x.CustomPanel" bounds="[10,10][60,60]"/>'
               '<node class="y.ParentPanel" bounds="[40,40][140,200]"/>')
        self.assertEqual(find_dialog_bounds(xml, (200, 480)), (40, 40, 140, 200))

    def test_no_panel_returns_none(self):
        self.assertIsNone(find_dialog_bounds("<hierarchy/>", (200, 480)))
        self.assertIsNone(find_dialog_bounds("", (200, 480)))


# ── 策略选择 ───────────────────────────────────────────────────────


class TestResolveStrategy(unittest.TestCase):

    def test_explicit_wins(self):
        self.assertEqual(resolve_strategy("gpt-4o", explicit="coordinate"), "coordinate")
        self.assertEqual(resolve_strategy("autoglm-phone", explicit="som"), "som")

    def test_invalid_falls_back_with_warn(self):
        # 非法值（手改 JSON）→ 记 WARN 回退默认 som
        with mock.patch("builtins.print"):
            self.assertEqual(resolve_strategy("gpt-4o", explicit="bogus"), "som")

    def test_empty_explicit_means_auto(self):
        # 空字符串 / None 表示未配置，按 auto 启发（不记 WARN）
        self.assertEqual(resolve_strategy("deepseek-v4", explicit=""), "som")
        self.assertEqual(resolve_strategy("autoglm-phone", explicit=None), "coordinate")

    def test_auto_heuristic(self):
        self.assertEqual(resolve_strategy("AutogLM-Phone-9B"), "coordinate")
        self.assertEqual(resolve_strategy("Qwen2-VL-7B"), "coordinate")
        self.assertEqual(resolve_strategy("deepseek-v4-flash-vision-exp"), "som")
        self.assertEqual(resolve_strategy("gpt-4o-2024"), "som")

    def test_no_model_name_falls_back_som(self):
        # Agent 注入路径无模型名可猜 → 回落 som
        self.assertEqual(resolve_strategy("", None), "som")
        self.assertEqual(resolve_strategy(None, ""), "som")
        self.assertEqual(resolve_strategy("unknown-model-xyz"), "som")


# ── VisionProvider 三级路由 ─────────────────────────────────────────


@unittest.skipUnless(VISION_MODULES_OK, "需要 vision / vision_provider 模块")
class TestVisionProvider(unittest.TestCase):

    def _conf(self, **kw):
        base = {"base_url": "", "model": "", "api_key": "", "tap_strategy": ""}
        base.update(kw)
        return base

    def test_user_config_priority(self):
        fake_user = FakeVision(ask_ret="user-answer")
        agent = FakeVision(ask_ret="agent-answer")
        with mock.patch.object(vision_mod, "_load_vision_conf",
                               return_value=self._conf(api_key="sk-x", model="m1")), \
             mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}), \
             mock.patch.object(vision_mod, "Vision", lambda **kw: fake_user):
            vp = vp_mod.VisionProvider(agent_vision=agent)
            self.assertTrue(vp.available())
            self.assertEqual(vp.ask("p", b"img"), "user-answer")   # 用户优先于 Agent
            self.assertEqual(vp.model_name(), "m1")

    def test_env_key_counts_as_configured(self):
        fake_user = FakeVision(ask_ret="env-user")
        with mock.patch.object(vision_mod, "_load_vision_conf",
                               return_value=self._conf()), \
             mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}), \
             mock.patch.object(vision_mod, "Vision", lambda **kw: fake_user):
            vp = vp_mod.VisionProvider()
            self.assertTrue(vp.available())
            self.assertEqual(vp.ask("p", b"i"), "env-user")

    def test_local_vllm_base_url_only(self):
        # 本地 vLLM：只有 base_url、无 api_key → 用户配置可用
        with mock.patch.object(vision_mod, "_load_vision_conf",
                               return_value=self._conf(base_url="http://127.0.0.1:8000/v1")), \
             mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            vp = vp_mod.VisionProvider()
            self.assertTrue(vp.available())
            self.assertTrue(vp._user_configured)

    def test_agent_fallback_when_unconfigured(self):
        agent = FakeVision(ask_ret="agent-answer")
        with mock.patch.object(vision_mod, "_load_vision_conf",
                               return_value=self._conf()), \
             mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            vp = vp_mod.VisionProvider(agent_vision=agent)
            self.assertTrue(vp.available())
            self.assertEqual(vp.ask("p", b"img"), "agent-answer")
            # 签名兼容：temperature/timeout 透传给 Agent 对象
            self.assertEqual(agent.calls[0]["temperature"], 0.0)
            self.assertEqual(agent.calls[0]["timeout"], vp.timeout)
            self.assertEqual(vp.ask("p", b"img", timeout=5), "agent-answer")
            self.assertEqual(agent.calls[1]["timeout"], 5)

    def test_old_signature_agent_compat(self):
        class OldAgent:
            def ask(self, prompt, image):
                return "old-ok"
        with mock.patch.object(vision_mod, "_load_vision_conf",
                               return_value=self._conf()), \
             mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            vp = vp_mod.VisionProvider(agent_vision=OldAgent())
            self.assertEqual(vp.ask("p", b"i"), "old-ok")   # 不透传不支持的形参

    def test_unconfigured_raises(self):
        with mock.patch.object(vision_mod, "_load_vision_conf",
                               return_value=self._conf()), \
             mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            vp = vp_mod.VisionProvider()
            self.assertFalse(vp.available())
            with self.assertRaises(RuntimeError):
                vp.ask("p", b"i")
            with self.assertRaises(RuntimeError):
                vp.ask_json("p", b"i", ["a"])


# ── TestCase.tap_vision 契约 ────────────────────────────────────────


@unittest.skipUnless(TF_OK and PIL_OK, "需要 uiautomator2 与 Pillow")
class TestTapVisionContract(unittest.TestCase):
    """tap_vision 契约：bool 返回 / 失败 WARN 不 ERROR / SoM 换算传导到 tap_xy。"""

    def _case(self, provider):
        t = object.__new__(tf.TestCase)
        t._vision = provider
        t._agent_vision = None
        t._cur_step = {"name": "s", "results": [], "evidences": []}
        t._db = None
        t._db_step_id = None
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp(prefix="dsh_tapvision_")
        t._auto_screenshot = lambda *a, **k: None
        t.records = []
        t.record = lambda result, detail, **kw: t.records.append((result, detail))
        t._log_action = lambda *a, **k: 0
        t.tap_xy_calls = []

        def _tap_xy(x, y, observe=True):
            t.tap_xy_calls.append((x, y, observe))
            return True
        t.tap_xy = _tap_xy
        t._screencap_bytes = lambda: _png_bytes(200, 480)
        t._dump = lambda: "<hierarchy/>"   # 无 Panel 节点 → 弹窗裁剪回退全屏
        return t

    def test_success_som_conversion_to_tap_xy(self):
        # 全屏 200x480：cols=4/rows=8 → B3: col idx=1, row idx=2 → (75,150)；无弹窗不裁剪 → tap_xy(75,150)
        v = FakeProvider({"col": "B", "row": 3, "reason": "ok"})
        t = self._case(v)
        with contextlib.redirect_stdout(io.StringIO()):
            ok = t.tap_vision("紫色色块")
        self.assertTrue(ok)
        self.assertEqual(t.tap_xy_calls, [(75, 150, True)])
        self.assertEqual(v.calls[0]["temperature"], 0.0)
        self.assertEqual(v.calls[0]["timeout"], 30.0)   # timeout 传导链
        self.assertEqual(t.records, [])                 # 成功路径不记 WARN

    def test_dialog_crop_offset_conversion(self):
        # 弹窗 bounds (50,60)-(150,300) + padding 20 → 裁剪区 (30,40)-(170,320)
        # 裁剪图 140x280 → cols=4/rows=4, cell_w=35/cell_h=70 → B3 局部 (52,175) → 设备 (82,215)
        xml = ('<hierarchy><node class="android.widget.ParentPanel" '
               'bounds="[50,60][150,300]"/></hierarchy>')
        v = FakeProvider({"col": "B", "row": 3})
        t = self._case(v)
        t._dump = lambda: xml
        with contextlib.redirect_stdout(io.StringIO()):
            ok = t.tap_vision("弹窗里的图标")
        self.assertTrue(ok)
        self.assertEqual(t.tap_xy_calls, [(82, 215, True)])

    def test_explicit_bounds_crop(self):
        # 显式 bounds 优先于弹窗识别（UI 树无 Panel 也裁剪）
        v = FakeProvider({"col": "B", "row": 3})
        t = self._case(v)
        with contextlib.redirect_stdout(io.StringIO()):
            t.tap_vision("图标", bounds=(50, 60, 150, 300))
        self.assertEqual(t.tap_xy_calls, [(82, 215, True)])

    def test_coordinate_strategy_route(self):
        # tap_strategy=coordinate：走归一化坐标路径（压缩 + 比例换算）
        v = FakeProvider({"x": 500, "y": 500, "reason": "r"})
        v.tap_strategy = lambda: "coordinate"
        t = self._case(v)
        with contextlib.redirect_stdout(io.StringIO()):
            ok = t.tap_vision("目标")
        self.assertTrue(ok)
        # 480 < 1280 → 未压缩，x=500/1000*200=100，y=500/1000*480=240
        self.assertEqual(t.tap_xy_calls, [(100, 240, True)])

    def test_unavailable_warn_not_error(self):
        v = FakeProvider(None, available=False)
        t = self._case(v)
        with contextlib.redirect_stdout(io.StringIO()):
            ok = t.tap_vision("紫色色块")
        self.assertFalse(ok)
        self.assertEqual(len(t.records), 1)
        self.assertEqual(t.records[0][0], "WARN")
        self.assertNotIn("ERROR", [r[0] for r in t.records])   # 降级不升级

    def test_parse_failure_warn_returns_false(self):
        v = FakeProvider({"x": 100, "y": 200})   # SoM 只收格子引用 → 解析失败
        t = self._case(v)
        with contextlib.redirect_stdout(io.StringIO()):
            ok = t.tap_vision("紫色色块")
        self.assertFalse(ok)
        self.assertEqual(t.records[0][0], "WARN")

    def test_silent_suppresses_warn(self):
        v = FakeProvider(None, available=False)
        t = self._case(v)
        with contextlib.redirect_stdout(io.StringIO()):
            ok = t.tap_vision("紫色色块", silent=True)
        self.assertFalse(ok)
        self.assertEqual(t.records, [])          # 调用方有自己的 FAIL 分支

    def test_repeat_taps_same_coord(self):
        v = FakeProvider({"col": "B", "row": 3})
        t = self._case(v)
        with mock.patch.object(tf, "ACTION_DELAY", 0), \
             contextlib.redirect_stdout(io.StringIO()):
            ok = t.tap_vision("色块", repeat=3, repeat_interval=0)
        self.assertTrue(ok)
        self.assertEqual(t.tap_xy_calls, [(75, 150, True)] * 3)

    def test_verify_pass_records_info(self):
        v = FakeProvider([{"col": "B", "row": 3}, {"answer": True, "reason": "已选中"}])
        t = self._case(v)
        with contextlib.redirect_stdout(io.StringIO()):
            ok = t.tap_vision("图标", verify="目标图标是否已被选中")
        self.assertTrue(ok)
        self.assertEqual(len(v.calls), 2)        # som 定位 + verify 问证
        self.assertEqual([r[0] for r in t.records], ["INFO"])

    def test_verify_fail_returns_false(self):
        v = FakeProvider([{"col": "B", "row": 3}, {"answer": False, "reason": "未选中"}])
        t = self._case(v)
        with contextlib.redirect_stdout(io.StringIO()):
            ok = t.tap_vision("图标", verify="目标图标是否已被选中")
        self.assertFalse(ok)
        self.assertEqual([r[0] for r in t.records], ["WARN"])


if __name__ == "__main__":
    unittest.main()
