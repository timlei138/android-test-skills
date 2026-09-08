# 视觉定位（Vision Tap）增强方案

## 1. 目标

把 `perceive_tools.py` 的 **SoM 网格定位** 与 Open-AutoGLM 的 **专用 GUI 模型 + 归一化坐标** 两种思路，按 `vision.json` 中显式配置的 `tap_strategy` 选择；未显式配置时按模型名称启发式选择，接入到 `android-gui-testing` SKILLS 中。

最终效果：
- Canvas、自定义滚轮、色盘、OEM 私有控件等 **view tree 无法访问** 的元素，也能被稳定点击。
- 通用多模态模型（GPT-4o、deepseek-v4-flash-vision-exp 等）走 **SoM 网格**，降低坐标估计难度。
- 专用 GUI/grounding 模型（autoglm-phone-9b、Qwen2-VL、ShowUI 等）走 **归一化坐标**，避免网格量化误差。
- 同时把 OCR 与视觉模型做轻量融合，提升小字/艺术字/半透明 toast 的读取准确率。

---

## 2. 参考实现分析

### 2.1 `AiAgentTest/tools/perceive_tools.py` 的 `vision_tap`

核心链路：

1. **智能裁剪**：先从 UI tree 找 `parentPanel` / `customPanel` 弹窗 bounds，把截图裁到弹窗区（+30px padding），减少无关视觉噪声。
2. **SoM 网格**：行列数按裁剪后图片尺寸自适应（`cols = clamp(img_w // 60, 4, 8)`，`rows = clamp(img_h // 60, 4, 12)`），在截图外侧扩展 30px 画布画灰色网格线 + 红色行列标签，让模型只需报格子引用。
3. **坐标映射**：纯索引换算，**margin 不参与公式**。
   - 格子中心在裁剪图坐标系下：`x_img = int((col_idx + 0.5) * cell_w)`，`y_img = int((row_idx + 0.5) * cell_h)`
   - 设备绝对坐标：`device = crop_offset + (x_img, y_img)`
   - 全屏无裁剪模式：`device = (x_img, y_img) * (device_size / snapshot_size)`（处理压缩快照）
4. **容错解析**：优先解析 `{"col":"G","row":7}`，其次 `{"cell":"G7"}`，再次从 reason 里正则提取；最后还留了一个 `x/y` fallback。
5. **闭环验证**：点击后可再用视觉模型验证结果是否变化，连续失败 2 次提示回退其他定位范式（本计划改为回退 OCR 标定或人工介入，因为 `tap_vision` 正是在 UI-tree 不可用时启用）。

优点：对通用 VLM 非常友好，不依赖模型训练；裁剪进一步缩小搜索空间。  
缺点：格子的中心点仍有量化误差（最差半格），对很小/很密集的按钮仍可能点偏。

### 2.2 Open-AutoGLM

核心链路：

1. **专用模型**：默认 `autoglm-phone-9b`，走 OpenAI-compatible 本地 serving（vLLM）。
2. **动作语法**：模型直接输出 Python 式函数调用，例如：
   ```text
   do(action="Tap", element=[512, 384])
   do(action="Swipe", start=[200, 600], end=[800, 600])
   finish(message="任务完成")
   ```
3. **归一化坐标**：`element` 是 0-1000 范围的 `[x, y]`，执行层映射：
   ```python
   x = int(element[0] / 1000 * screen_width)
   y = int(element[1] / 1000 * screen_height)
   ```

优点：坐标连续、无网格量化误差；专用 GUI 模型在 mobile UI 上定位精度更高。  
缺点：需要本地部署 `autoglm-phone-9b`（约 9B，需要 GPU）；通用 VLMs 如果没有 grounding 训练，直接出坐标会不准。

---

## 3. 建议架构

新增两个独立模块，并把截图/视觉调用抽象成**公共管线**：

```text
framework/
  screenshot.py        # 新增：公共截图采集、裁剪、压缩、编码
  vision_provider.py   # 新增：视觉模型路由（用户配置 → Agent → 报错）
  vision.py            # 已有：用户配置视觉模型的默认实现
  vision_tap.py        # 新增：视觉定位点击
  ocr_screen.py        # 已有：RapidOCR 读文字+坐标
  test_framework.py    # 已有：TestCase 入口
```

设计原则：
- **截图模块完全公共化**：只处理图像，不依赖任何视觉模型，供 `vision_ask`、`vision_tap`、OCR、外部 Agent 共用。
- **视觉模型可插拔**：用户显式配置时优先使用；未配置时自动 fallback 到导入 SKILLS 的 Agent 提供的视觉能力。
- **坐标换算显式化**：任何裁剪/压缩操作都返回 `offset` 与 `scale`，确保 OCR 或视觉模型返回的局部坐标能正确映射回设备屏幕坐标。

### 3.1 视觉模型路由（VisionProvider）

由 `vision_provider.py` 统一决定视觉调用走哪条通路，并在初始化时一次性确定可用模型，避免每次调用都读盘：

```python
# framework/vision_provider.py
class VisionProvider:
    """视觉调用统一入口。优先级：用户配置 > Agent 提供。"""

    def __init__(self, agent_vision=None, timeout=90):
        self.agent_vision = agent_vision   # Agent 提供的视觉模型对象
        self.timeout = timeout
        self._user_configured = self._check_user_config()
        self._user_vision = None

    def _check_user_config(self) -> bool:
        """构造时一次性判断用户是否配置了视觉模型。
        本地 vLLM serving 可能只有 base_url 而无 api_key，因此 base_url 有效也算配置。"""
        conf = _load_vision_conf()
        has_key = bool(conf.get("api_key") or os.environ.get("DEEPSEEK_API_KEY"))
        has_endpoint = bool(conf.get("base_url"))
        return has_key or has_endpoint

    def available(self) -> bool:
        """当前是否有可用的视觉模型。"""
        return self._user_configured or self.agent_vision is not None

    def ask(self, prompt: str, image, temperature=0.0, timeout=None, **kwargs) -> str:
        to = timeout if timeout is not None else self.timeout
        if self._user_configured:
            if self._user_vision is None:
                from vision import Vision
                self._user_vision = Vision(timeout=to)
            return self._user_vision.ask(prompt, image, temperature=temperature, timeout=to, **kwargs)
        if self.agent_vision is not None:
            return self.agent_vision.ask(prompt, image, temperature=temperature, timeout=to, **kwargs)
        raise RuntimeError("未配置视觉模型，且 Agent 未提供视觉能力")

    def ask_json(self, prompt: str, image, fields: list[str], temperature=0.0, timeout=None, **kwargs) -> dict:
        ...
```

**重要语境**：`vision=` 参数只在**生成期/调试期**由 Agent 注入；`run_case` 独立执行时没有 Agent 在环，此时只能依赖用户配置。因此 `tap_vision` 内部必须 catch `VisionProvider` 异常并降级为 `WARN` + 返回 `False`，避免用例在缺少视觉配置时变成 `ERROR`。

Agent 接入示例：

```python
class MyAgentVision:
    def ask(self, prompt, image, temperature=0.0, timeout=None, **kwargs):
        return my_model.chat(prompt, image, temperature=temperature, timeout=timeout)
    def ask_json(self, prompt, image, fields, temperature=0.0, timeout=None, **kwargs):
        return my_model.chat_json(prompt, image, fields, temperature=temperature, timeout=timeout)


case = TestCase(vision=MyAgentVision())
case.tap_vision("紫色色块")
```

### 3.2 策略选择器

**显式配置优先，启发式仅作为 `auto` 兜底**。`vision.json` 中的 `tap_strategy` 字段是主路径：

```json
{
  "tap_strategy": "auto"
}
```

可选值：
- `"som"`：强制 SoM 网格
- `"coordinate"`：强制归一化坐标
- `"auto"`：未显式配置时，根据模型名启发式选择

> 注意：策略选择只决定**如何解析模型输出**（SoM 网格 vs 归一化坐标），真正的视觉模型仍由 `VisionProvider` 按优先级选择。

```python
# framework/vision_tap.py
VISION_STRATEGIES = {
    # 专用 GUI / grounding 模型 → 归一化坐标
    "coordinate": {
        "match": ["autoglm", "qwen2-vl", "showui", "oscopilot", "cogagent"],
        "prompt": "...",
        "output_parser": "normalized_xy",
    },
    # 通用多模态模型 → SoM 网格（auto 默认）
    "som": {
        "match": ["gpt-4o", "deepseek", "claude", "gemini", "yi-vl", "llava"],
        "prompt": "...",
        "output_parser": "som_cell",
    },
}

_DEFAULT_STRATEGY = "som"


def resolve_strategy(model_name: str, explicit: str | None = None) -> str:
    if explicit in ("som", "coordinate"):
        return explicit
    if explicit is not None and explicit != "auto":
        # 用户手改 JSON 等非法值，记 WARN 提示配置错误后回退默认
        print(f"[WARN] tap_strategy={explicit!r} 非法，回退到 {_DEFAULT_STRATEGY}")
        return _DEFAULT_STRATEGY
    # explicit == "auto" or None
    name = model_name.lower()
    for strategy, cfg in VISION_STRATEGIES.items():
        if any(keyword in name for keyword in cfg["match"]):
            return strategy
    return _DEFAULT_STRATEGY
```

说明：
- `tap_strategy` 显式配置时直接生效，不再猜测模型名。
- `"auto"` 或缺省时，根据模型名关键字启发式选择。
- 若 `vision.json` 没有 `model` 字段（例如 Agent 注入路径），`resolve_strategy` 无法匹配关键字，直接回落 `_DEFAULT_STRATEGY = "som"`。
- 关键字表只放已验证的专用模型，避免 `"gui"` 这类过宽词导致通用 VLM 被误切到坐标策略。
- Web UI「视觉模型」页新增 `tap_strategy` 下拉框（som / coordinate / auto），存入 `vision.json`。

### 3.3 策略 A：SoM 网格（复刻并改进 AiAgentTest）

与 `perceive_tools.py` 基本一致，但做几点适配：

1. **复用公共截图管线**：统一调用 `screenshot.capture()` + `screenshot.crop_bounds()`，不要自己再维护一套 base64 逻辑。
2. **裁剪与 `vision_ask` 对齐**：沿用公共裁剪能力（`rid` / `bounds` / 弹窗自动识别），缺省则全屏。
3. **网格参数代码内自适应**：行列数由裁剪后图片尺寸按固定规则计算（`cols = clamp(img_w // 60, 4, 8)`，`rows = clamp(img_h // 60, 4, 12)`），不暴露给用户配置，避免配置面扩大导致误配。
4. **Prompt 加两句防误读**：必须在 prompt 中写明“网格线和标签不是界面内容，仅用于定位”以及“若目标跨格子，报中心所在格子”。
5. **SoM 只接受格子引用**：解析链为 `col/row` → `cell`；模型若直接返回像素/归一化坐标，视为解析失败，不再维护 x/y fallback，避免在 margin 语义上开例外。
6. **输出统一为设备绝对坐标 (x, y)**，由 `tap_vision` 调用 `TestCase.tap_xy(x, y)` 执行。

核心函数签名：

```python
@dataclass
class SOMGridMeta:
    cols: int
    rows: int
    margin: int          # 外扩画布像素，仅用于绘图，不参与坐标换算
    cell_w: float
    cell_h: float


def _draw_som_grid(pil_img: Image.Image, margin: int = 30) -> tuple[Image.Image, SOMGridMeta]:
    """在外扩画布上画网格，返回（带网格的图, 元数据）。"""


def _parse_som_response(data: dict, meta: SOMGridMeta) -> tuple[int, int] | None:
    """
    解析 col/row/cell，返回裁剪图坐标系下的 (x_img, y_img)；失败返回 None。
    换算：x_img = int((col_idx + 0.5) * cell_w)，margin 不参与。
    """


def _som_tap(
    vision: Vision,
    screen_image: ScreenImage,
    description: str,
) -> tuple[int, int, str]:
    """返回设备绝对坐标 (x, y) 和 reason。"""
```

### 3.4 策略 B：归一化坐标（参考 Open-AutoGLM）

1. **输入**：原始/压缩截图 + prompt。
2. **Prompt 模板**：
   ```text
   你要在手机上完成点击操作。截图尺寸为 {width}x{height}（裁剪后的区域尺寸）。
   请返回目标「{description}」的中心点坐标。
   坐标必须是 0-1000 的归一化整数，左上角为 [0,0]，右下角为 [1000,1000]。
   只返回 JSON: {"x": 0-1000, "y": 0-1000, "reason": "..."}
   ```
3. **解析与映射**：
   ```python
   x = int(data["x"] / 1000 * screen_width)
   y = int(data["y"] / 1000 * screen_height)
   ```
4. **兼容 Open-AutoGLM 动作语法**：如果模型输出 `do(action="Tap", element=[512,384])`，用 AST/literal_eval 解析后提取 `element`。

核心函数：

```python
def _coordinate_tap(
    vision: Vision,
    image: Image.Image,
    description: str,
    screen_size: tuple[int, int],  # 每次调用前现取 device 尺寸，禁止缓存
) -> tuple[int, int, str]:
    """返回设备绝对坐标 (x, y) 和 reason。"""
```

### 3.5 策略 C：OCR 辅助定位（轻量，零模型调用）

对于**文案可见但无 rid/text** 的元素（例如某些 WebView、图片按钮上的文字），先让 OCR 找文字中心点，再点击。

```python
def _ocr_tap(self, text_pattern: str, bounds: tuple | None = None) -> tuple[int, int] | None:
    """调用 ocr_screen.py 或 RapidOCR，返回最佳匹配文字的中心坐标。"""
```

可作为 `tap_vision` 的**前置快速通道**：当 description 看起来像明确文案时，先 OCR；OCR 命中则直接点击；未命中再走视觉模型。

### 3.6 公共截图管线与坐标换算

新增 `framework/screenshot.py`，作为所有视觉/ OCR 操作的图像处理公共层。它不依赖任何视觉模型，只负责：

- 截图采集
- 按 `bounds` / `rid` / 弹窗区域裁剪
- 等比压缩（控制长边，减少 token）
- PNG 编码 / base64 编码
- 返回裁剪偏移 `offset` 与压缩比例 `scale`，供调用方做坐标换算

建议返回结构：

```python
from dataclasses import dataclass
from PIL import Image

@dataclass
class ScreenImage:
    image: Image.Image          # PIL Image（可能已被裁剪/压缩）
    png_bytes: bytes            # PNG 字节
    original_size: tuple[int, int]   # 全屏原始尺寸
    offset: tuple[int, int]     # 裁剪左上角相对全屏的偏移，未裁剪则为 (0, 0)
    scale: float                # 相对原始尺寸的缩放比例（1.0 表示未压缩）
```

核心 API：

```python
# framework/screenshot.py

def capture(device) -> ScreenImage:
    """
    截取全屏，返回 ScreenImage。
    内部必须复用 TestCase._screencap_bytes()，以继承 ensure_awake() 锁屏防护。
    """

def crop_bounds(src: ScreenImage, bounds: tuple, padding: int = 20) -> ScreenImage:
    """按 bounds 外扩 padding 裁剪，offset 自动累加。"""

def resize_for_vision(src: ScreenImage, max_side: int = 1280) -> ScreenImage:
    """等比压缩，scale 自动累乘。"""

def encode_base64(src: ScreenImage | Image.Image | bytes | str) -> str:
    """统一编码为 data URI。"""
```

**与现有截屏体系的关系**：

1. `screenshot.capture()` 必须内部调用 `_screencap_bytes()`，不能直接 `adb shell screencap`，否则会绕过 `ensure_awake()` 锁屏防护。
2. `_handle_unknown_dialog()` 里裸拼 `adb shell screencap` 的地方要收口到统一入口（现有漏网）。
3. `ocr()` 已内置 1600 压缩 + 坐标 ÷scale 还原机制；`screenshot.py` 首期优先服务 **vision 链路**，避免一次性改动 OCR 回归面。P2 再评估是否把 OCR 迁移到 `ScreenImage`。

#### 坐标换算规则

OCR 或视觉模型在裁剪/压缩后的图上返回的坐标都是**局部坐标**，必须换算回设备屏幕坐标后才能点击：

```python
screen_x = int(local_x / scale + offset_x)
screen_y = int(local_y / scale + offset_y)
```

| 场景 | local_x/local_y 含义 | 换算公式 |
|---|---|---|
| 仅裁剪 | 相对裁剪图左上角 | `local_x + offset_x` |
| 仅压缩 | 相对压缩图左上角 | `local_x / scale` |
| 先裁剪再压缩 | 相对最终图左上角 | `local_x / scale + offset_x` |
| SoM 网格 | 裁剪图坐标系下的格子中心 | `x_img = int((col_idx + 0.5) * cell_w)`，然后 `device = (x_img, y_img) + offset` |
| 归一化坐标 | 0-1000 相对裁剪图 | `x / 1000 * crop_w / scale + offset_x` |

**原则**：
- 任何返回 `ScreenImage` 的操作都必须同时更新 `offset` 和 `scale`，禁止只返回图而丢掉几何信息。
- **SoM 路径不变式**：
  1. 网格必须画在“送模型前的最后一张图”上；
  2. SoM 路径**不压缩**（`scale = 1.0`），避免格子在压缩图上不再等分；
  3. 坐标换算采用参考实现的纯索引公式 `x_img = int((col_idx + 0.5) * cell_w)`，`margin` 仅用于绘图，不参与换算。
- **与参考实现的偏离说明**：参考实现允许全屏模式使用压缩快照（`device = (x_img, y_img) * (device_size / snapshot_size)`）。本计划选择更保守的“SoM 路径一律不压缩”，以简化坐标换算并降低出错面；代价是 `bounds=None` 且无弹窗可裁时全尺寸 PNG 直发，token 成本略高。若后续实测成为瓶颈，再引入全屏压缩分支并补全屏公式。
- 归一化坐标策略允许压缩，因为它输出的是比例而非像素格。

---

## 4. `TestCase` API 设计

### 4.1 构造参数

```python
def __init__(self, ..., vision=None):
    """
    vision: Agent 提供的视觉模型对象，建议实现签名：
            ask(prompt, image, temperature=0.0, timeout=None, **kwargs)
            ask_json(prompt, image, fields, temperature=0.0, timeout=None, **kwargs)
            用户未在 vision.json 配置视觉模型时，自动 fallback 到该对象。
    """
```

### 4.2 tap_vision

```python
def tap_vision(
    self,
    description: str,           # 自然语言描述目标
    repeat: int = 1,            # 同坐标连点次数（每次独立校验）
    repeat_interval: float = 0.15, # 连点间隔（秒），避免系统合并连续 tap 事件
    verify: str = "",           # 点击后让 vision 验证的陈述句
    bounds: tuple | None = None, # 限定搜索区域 (x1,y1,x2,y2)
    crop_dialog: bool = True,    # 是否自动裁剪到弹窗区（SoM 有效）
    prefer_ocr: bool = False,    # 文案类描述先尝试 OCR（默认关闭，避免误触发）
    observe: bool = True,        # 点击后是否执行弹窗检查/延迟/截图（与 _tap_unified 同义）
    silent: bool = False,        # 失败时是否不记 WARN（调用方有自己的 FAIL 分支）
    timeout: float = 30.0,       # vision 调用超时
) -> bool:
    """
    视觉定位点击。成功返回 True，失败时 silent=False 记 WARN 并返回 False。
    坐标/策略/模型名/reason 等详情走 _log_action + record detail，不通过返回值携带。
    observe 与 silent 正交，与现有 _tap_unified 语义一致。
    """
```

使用示例：

```python
# 色块（无 rid/text 可点的典型场景）
self.tap_vision("紫色色块")

# 限定区域 + 点击后验证
guard = self.tap_vision("弹窗中右侧第二个圆形图标",
                        bounds=(100, 200, 800, 1000),
                        verify="目标图标是否已被选中")
if not guard:
    return self.record("FAIL", "视觉点击未命中目标")

# 递增场景：推荐用 calibrate + 循环，而非 repeat；若用 repeat，每次都会 observe 复核
# self.tap_vision("当前选中值下方的位置", repeat=3)
```

内部执行顺序：

1. `prefer_ocr and looks_like_exact_text(description)` → OCR 快速通道；多候选时降级视觉，不点第一个。
2. 根据 `vision.json` 的 `tap_strategy` 选择策略：`som` 或 `coordinate`。
3. 截图/裁剪 → 构造 prompt（SoM 路径 temperature=0.0）→ 调视觉模型 → 解析坐标。
4. 坐标经 `ScreenImage.offset/scale` 换算为设备绝对坐标（SoM 路径 `scale=1.0`，用纯索引公式）。
5. `tap_xy(x, y, observe=observe)` 执行点击；`repeat > 1` 时每两次之间 sleep `repeat_interval`，并逐次 observe 复核。
6. 若 `verify` 非空，截新图让 vision 判断，作为断言证据。
7. 视觉模型不可用或解析失败 → catch 后，若 `silent=False` 则记 WARN，返回 False，不抛 ERROR。
8. 连续失败 streak 达到阈值 → 提示回退 OCR 标定范式或人工介入，避免无限视觉重试。

---

## 5. 与现有证据/报告体系的集成

- `tap_vision` 作为新的执行通道，结果按现有 `result` 格式进入 `TestCase.steps[*].results`。
- 点击前截图（带 SoM 网格或原始截图）和点击后验证截图都落盘到现有 `SCREENSHOT_DIR` 约定目录（`storage/screenshots/case_<时间戳>/`），复用 `_auto_screenshot()` 机制，不新开目录结构。
- 坐标、策略、模型名、reason 通过 `_log_action` 写入时间轴与 DB，并写入 result detail，方便事后复盘为什么点到这里。
- 连续失败时，报告里标记 `WARN` 并建议「回退 OCR 标定范式或人工介入」（`tap_vision` 本就在 UI-tree 不可用时才启用，回退 UI-tree 没有意义）。

---

## 6. 实现阶段建议

### P0 —— 公共截图/视觉管线 + SoM 网格（优先级最高，风险最低）

#### P0 Step 0：离线 spike（P0 开工前先做，可能改变策略选型）

- [ ] 从 `storage/screenshots/` 取 3-5 张真机截图（含弹窗、色块、无文字按钮）。
- [ ] 用当前配置的 `deepseek-v4-flash-vision-exp` 离线测试 SoM prompt，量两个指标：
  1. **格式遵从度**：模型是否稳定输出 `{"col":"G","row":7}` 或 `{"cell":"G7"}` 格式；
  2. **格子命中率**：人工核对模型报的格子中心是否落在目标元素上（不是“格式对但格子错”）。
- [ ] 若格式遵从度 < 80% 或格子命中率 < 80%，P0 主策略需重选（例如先上坐标策略 + 强 prompt，或改 OCR 标定）。

**原因**：SoM 对 GPT-4V 系有效是对当前模型生态的假设，对 `deepseek-v4-flash-vision-exp` 是未验证假设。30 分钟 spike 可避免整个 P0 建在流沙上。

#### 基础层

- [ ] 新建 `framework/screenshot.py`：
  - `ScreenImage`（含 `offset`/`scale`/`original_size`）
  - `capture()`：内部调用 `_screencap_bytes()`，继承 `ensure_awake()` 锁屏防护
  - `crop_bounds()`、`resize_for_vision()`、`encode_base64()`
  - 保证 `offset`/`scale` 在裁剪/压缩链上正确累加
- [ ] 收口 `_handle_unknown_dialog()` 里裸拼 `adb shell screencap` 的旁路，统一走截图入口。
- [ ] 新建 `framework/vision_provider.py`：
  - `VisionProvider` 构造时一次性判断用户配置是否可用（含 base_url，支持本地 vLLM）
  - 提供 `available()` 接口
  - 优先级 **用户配置 > Agent 提供**；未配置且无 Agent 时 `ask/ask_json` 抛异常
- [ ] `TestCase.__init__` 增加 `vision=` 参数，并把内部 `_get_vision()` 改为使用 `VisionProvider`。
- [ ] 把 `TestCase._vision_crop_bytes()` 迁移到 `screenshot.crop_bounds()`，`vision_ask` / `assert_visual` 统一走公共截图管线和 `VisionProvider`。
- [ ] 扩展 `framework/vision.py` 的 `_load_vision_conf()` 白名单，支持 `tap_strategy` 新增键。
- [ ] `framework/vision.py` 的 `Vision.ask/ask_json/_chat` 支持传入 `temperature` 并透传给 API，默认 `0.0`。

#### SoM 定位

- [ ] 新建 `framework/vision_tap.py`：
  - `_draw_som_grid()`：行列按裁剪后尺寸自适应（`cols = clamp(img_w // 60, 4, 8)`，`rows = clamp(img_h // 60, 4, 12)`），外扩画布，返回 `SOMGridMeta`（含 `margin`）
  - `_parse_som_response()`：容错解析链 `col/row` → `cell`；x/y 像素 fallback 不再支持
  - `_som_tap()`：输入 `ScreenImage`，SoM 路径 `scale=1.0`，输出设备绝对坐标；坐标换算用纯索引公式 `x_img = int((col_idx + 0.5) * cell_w)`，再 `device = (x_img, y_img) + offset`
- [ ] `TestCase` 新增 `tap_vision()`，返回 `bool`，参数与 `_tap_unified` 同义（`observe=` + `silent=` 正交）。
- [ ] `tap_vision` 内部 catch 视觉模型不可用/解析失败，记 WARN 返回 False，不抛 ERROR。
- [ ] 视觉模型调用传 `temperature=0.0`（定位任务需要确定性采样）。
- [ ] Web UI「视觉模型」页增加 `tap_strategy` 下拉框（som / coordinate / auto），存入 `vision.json`。

#### 单测（沿用 tests/ 现有 mock 模式）

- [ ] `tap_vision` 返回 `bool`：成功返回 True，失败返回 False。
- [ ] 未配置视觉模型且无 Agent 时返回 False 并记 WARN（不是 ERROR）。
- [ ] SoM 换算：模拟 `{"col":"B","row":3}`，验证最终 `tap_xy` 被调用到正确设备坐标（`x_img = int((col_idx+0.5)*cell_w) + offset_x`，margin 不参与公式）。
- [ ] crop → resize 链：`offset` 与 `scale` 累乘正确（用于 coordinate 策略和 OCR）。
- [ ] `VisionProvider` 三级路由：用户配置优先、Agent 次之、都没有时抛异常并被调用方 catch。
- [ ] `VisionProvider` 识别本地 vLLM：base_url 有效即可判定用户配置可用。

#### 真机回归

- [ ] 选 **色块/无文字图形按钮/WebView 控件**类场景（view tree 与 OCR 均不可定位的真正的视觉点击空白）。
- [ ] 滚轮场景留作 `tap_vision` vs `calibrate_picker` 的对照实验，结论回写知识卡，不作为 P0 主战场。

**收益**：立刻解决当前 SKILLS 无法点击 Canvas/色盘/无文字图标的痛点；不依赖新模型；同时让 Agent 能复用截图能力并接入自己的视觉模型。

### P1 —— 归一化坐标（需要模型支持）

- [ ] 在 `vision_tap.py` 实现 `_coordinate_tap` 与 Open-AutoGLM 动作语法解析。
- [ ] 维护 `VISION_STRATEGIES` 模型关键字表（只放已验证的专用 GUI 模型，不过度泛化）。
- [ ] 用户本地部署 `autoglm-phone-9b`（或接入支持 grounding 的 Qwen2-VL API）后，把模型名配进 `vision.json`，`tap_strategy=auto` 时自动切换。
- [ ] 单测：
  - 模拟模型返回 `do(action="Tap", element=[500,500])`，验证映射到屏幕中心。
  - 模拟模型返回 `{"x":500,"y":500}`，验证裁剪模式下 `offset/scale` 换算正确。
- [ ] Web UI 保存配置时校验：若 `tap_strategy=coordinate` 但当前模型名不匹配已验证的专用 GUI/grounding 关键字，给出警告“该模型可能不具备 grounding 能力，坐标精度可能下降”；**运行时不悄悄降级**，尊重用户显式选择。

**收益**：在有专用 GUI 模型时，定位精度高于 SoM；为后续接入自研/开源 GUI 模型铺路。

### P2 —— OCR 增强（可选）

- [ ] `ocr_screen.py` 支持传入 `ScreenImage` 或 `bounds`，先裁剪后识别，减少像素量并提升速度。
- [ ] `ocr_screen.py` 增加预处理选项（灰度、对比度、二值化），提升小字识别率。
- [ ] `tap_vision` 的 OCR 快速通道：description 是精确文案且 `prefer_ocr=True` 时先 OCR；多候选时不点第一个，降级视觉模型。
- [ ] OCR 返回的局部坐标通过 `ScreenImage.offset/scale` 换算为设备坐标后再点击。
- [ ] 视觉模型读 toast：保留现有 `vision_ask` 手动兜底能力；**不**做「OCR 为空时自动 vision 兜底」，避免把「错过 toast 窗口」误判为「读不准」。

---

## 7. 可行性评估

| 维度 | 评估 | 说明 |
|---|---|---|
| **技术可行性** | ✅ 高 | PIL/Pillow 已在项目依赖中；SoM 完全用 Python/PIL 实现，无新增二进制依赖。 |
| **模型可行性** | ⚠️ 分策略 | **SoM 是当前模型生态的假设**，对 `deepseek-v4-flash-vision-exp` 需离线 spike 验证；坐标策略在专用 GUI 模型上可用性高，但需要用户本地部署或接入 grounding API。 |
| **数据/隐私** | ✅ 无额外风险 | 截图仍在本地处理；Agent 自带视觉模型时，图像由 Agent 自己决定如何上传，SKILLS 不介入。 |
| **对现有用例影响** | ✅ 低 | `tap_vision` 是新增方法，不影响 `tap_rid/tap_text` 等现有路径；仅在 view tree 无法命中时启用。 |
| **维护成本** | ⚠️ 中 | `tap_strategy` 显式配置降低了对模型名映射表的依赖；新增策略仍需要独立 prompt/解析逻辑，建议通过单测锁住解析器。 |
| **性能** | ✅ 高 | 裁剪后 base64 通常只有全屏 10%-30%，OCR 和视觉调用都更快；SoM 网格图外扩 margin 带来的增幅可忽略。 |
| **准确性提升** | ✅ 高 | 对 Canvas/色盘/无文字图标类控件，从无解变成可解；裁剪后目标占比更大，SoM 与坐标策略精度都会提升。 |

### 主要风险与缓解

1. **通用 VLM 误用坐标策略会精度暴跌**
   - 缓解：`tap_strategy` 默认 `"auto"`，只有在模型名匹配已验证的专用 GUI/grounding 关键字时才自动切 `coordinate`；Web UI 保存配置时对显式选 `coordinate` 但模型不匹配的情况给出警告；运行时尊重用户显式选择。

2. **SoM 对当前模型输出格式不遵从**
   - 缓解：P0 开工前先做 30 分钟离线 spike，验证 `deepseek-v4-flash-vision-exp` 对 col/row/cell 引用的遵从度；不过关则 P0 主策略重选。

3. **SoM 网格标签遮挡小目标**
   - 缓解：标签画在外扩画布上，不覆盖原图；网格线用半透明灰色；网格密度由代码内 clamp 常量控制，如需更稀疏可调整该常量。

4. **坐标策略模型输出格式不统一**
   - 缓解：同时支持 JSON `{"x":..., "y":...}` 和 Open-AutoGLM 的 `do(action="Tap", element=[...])`；解析失败记 WARN 并返回 False，不跨策略静默降级。

5. **裁剪后坐标换算错误导致点击偏移**
   - 缓解：所有裁剪/压缩操作统一返回 `ScreenImage`（含 `offset` + `scale`）；SoM 坐标换算采用纯索引公式 `x_img = int((col_idx + 0.5) * cell_w)`，`margin` 仅用于绘图、不参与换算；坐标换算由单测覆盖；新增 `tap_vision` 单测验证裁剪模式下最终 `tap_xy` 坐标正确。

6. **裁剪弹窗 bounds 失败导致全屏搜索**
   - 缓解：与现有 `_find_dialog_crop_bounds` 一致，`parentPanel`/`customPanel` 找不到时回退全屏，不会阻塞。

7. **Agent 注入的模型在 run_case 独立执行时不可用**
   - 缓解：文档与实现明确区分「生成期 Agent 注入」和「run_case 独立执行」两个语境；独立执行时只能依赖用户配置，缺失时 `tap_vision` 降级 WARN 而非 ERROR。

---

## 8. 结论与建议

**可以且建议放入 SKILLS**，按 P0 → P1 → P2 顺序落地：

1. **P0 先做三件事**：
   - **离线 spike**：验证当前 `deepseek-v4-flash-vision-exp` 对 SoM 引用的遵从度（30 分钟，可能改变策略选型）。
   - **公共截图/视觉管线**：`screenshot.py` + `vision_provider.py`，对齐现有动作基元契约与锁屏防护。
   - **SoM 网格**：解决色块/无文字图标/WebView 控件等真正的视觉点击空白。
2. **P1 坐标策略**：作为专用 GUI 模型升级路径，等用户部署/接入 `autoglm-phone-9b` 或 Qwen2-VL grounding 模型后通过 `tap_strategy=auto` 自动生效。
3. **P2 OCR 增强作为甜点**：成本最低，但收益相对有限，可排后。

下一步如果确认修订后的计划，我可以直接开始实现 `framework/screenshot.py` + `framework/vision_provider.py` + `framework/vision_tap.py` + `TestCase.tap_vision()` + Web UI 配置项，并补单测与真机回归。
