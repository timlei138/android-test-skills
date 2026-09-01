---
name: android-gui-testing
description: Android 设备黑盒 GUI 功能测试。用户给测试用例（步骤+预期），按本 skill 驱动已连接设备执行并输出带证据的测试报告。内置 uiautomator2 框架、Canvas OCR 读取、点按滚轮控制器、置灰像素断言、知识卡（按前台包名注入 App 操作经验）、PASS/FAIL/BLOCKED 结果分类。支持你给用例我出报告。
---

# Android 设备 GUI 测试（黑盒）

以"真人测试员"的方式在已连接的 Android 设备上执行用户给出的功能测试用例，输出结构化 Markdown 测试报告。

## 环境（先检查，缺啥跑 setup.sh）

```bash
adb devices -l                      # 设备在线且已授权
# 框架位于本 skill 目录:
#   framework/test_framework.py  测试框架
#   framework/run_case.py        用例执行器
#   framework/knowledge/*.yaml   知识卡（按前台包名自动注入 App 操作经验）
#   cases/*.py                   示例用例
# 一键安装: bash setup.sh [--with-agent]
```

依赖：`uiautomator2` + `rapidocr_onnxruntime` + `pyyaml`（Python 3.9+ venv）。

## 核心原则

1. **以用例（Case）为准**：用例的步骤与预期就是验收标准（spec）。执行后对比实际行为，**凡与 case 不符就是"不准"**——报告 FAIL 并说明差别（case 期望什么 / 实际是什么 / 差在哪），绝不反过来改 case 迁就实际行为。case 表述不清或客观上无法执行时才与用户确认。
2. **每步留证**：关键步骤截图，报告附证据路径；状态类断言（enabled/selected/checked/clickable）要同时记录实际状态值，不能只写 PASS/FAIL。
3. **元素优先定位**：resource-id > text > content-desc > 坐标（仅 Canvas/浮层等无元素场景）。坐标是最后手段，且必须从元素 bounds 推导（`el_bounds`），绝不写死。
4. **确定性优先**：UI 树能读到的用元素定位，绝不盲猜坐标；Canvas 盲区用 OCR；颜色/布局等像素与 UI 树都不可靠的场景用视觉模型（`vision_ask` / `assert_button_state_visual`）。
5. **每步验证**：操作后必须重读界面确认状态变化；断言用代码，不靠模型自述。
6. **如实分类**：与 case 不符=FAIL（说明差别）；环境/前置不满足=BLOCKED（⛔）；case 与实际都成立但值得记录=WARN/INFO。

## 工作流

1. **解析用例**：从用户消息提取 前置条件/操作步骤/预期结果。缺信息先问，不猜。
2. **写用例脚本**（参考 `cases/联想日历_174.py`）：用框架 API 表达步骤与断言。**必须把用户原始口述写进脚本顶部的 `USER_INPUT = """..."""` 常量**（run_case.py 提取后随执行结果一起入库，实现 需求→脚本→结果 全链路追溯）。
3. **执行**：`python run_case.py <用例>.py`
4. **出报告**：框架自动生成 `screenshots/reports/<用例>_报告.md`，把结论汇报给用户。

## 框架 API 速查

```python
from test_framework import TestCase
t = TestCase("用例名")

t.step("步骤名")                 # 开启一个步骤
t.record("PASS|FAIL|WARN|INFO|BLOCKED", "说明")   # 记录结果
t.blocked("原因")                # 环境/前置阻塞
t.tap_el(rid=..., text=..., desc=...)  # 元素定位点击（id/text/desc）
t.tap_rid("resource-id") / t.tap_text("文字") / t.tap_desc("content-desc")
t.el_bounds(rid/text/desc) -> (x1,y1,x2,y2)  # 元素取 bounds（坐标从元素推导）
t.top_bar_icons() -> [(cx,cy,desc,class),...]  # 工具栏图标（元素化，排除返回箭头）
t.open_more_menu()               # 打开"更多"菜单（定位+验证+重试）
t.input_text("rid", "中文文本")   # 中文经 ADBKeyboard 输入
t.clear_text("rid")
t.read_rid("rid") -> {text, checked, enabled, selected, clickable, bounds}
t.assert_text(rid, expect) / t.assert_switch(rid, "true|false")
t.assert_length_le(rid, 20)      # 输入长度上限
t.vision_ask(prompt, rid=)       # 视觉模型问答（颜色/布局/OCR 盲区）
t.assert_button_state_visual(rid, "grayed"|"clickable")  # 视觉按钮状态断言
t.assert_visual(prompt, expect)  # 视觉断言：回答命中关键词
t.contrast_of(rid) / t.assert_grayed(rid, ref, ratio=0.6)  # 置灰像素断言（旧，视觉优先）
t.screenshot("名称")              # 截图留证
t.ocr(y_min, y_max) -> [(x,y,conf,text)]   # Canvas 内容读取
t.ocr_find(keyword, y_min, y_max) / t.first_clickable(y_min, y_max)
t.current_package() / t.current_activity()  # 前台包名/完整 Activity
t.finish() -> 报告路径
```

## 系统级操作与通用前置条件

跨 App 通用的设备操作（用例前置条件常用）：

| 场景 | 命令/方法 |
|---|---|
| **首次使用/重置状态** | `t.pm_clear("包名")` = `adb shell pm clear 包名`（清数据回首次引导） |
| 强制停止 App | `t.force_stop(pkg)` |
| 读系统属性（判断模式） | `t.getprop("ro.build.type")` / `t.getprop("persist.sys.xxx")` |
| 读写系统设置 | `t.settings_get("global", key)` / `t.settings_put("secure", key, value)` |
| 判断设备网络 | `t.has_network()`（解析需要联网时先检查，无网则 BLOCKED） |
| 授予运行时权限 | `t.grant_permission(pkg, "android.permission.X")` |
| 前台包/Activity | `t.current_package()` |

**用例前置处理套路**：
1. 用例要求"首次使用"状态 → `t.pm_clear(包名)` 再启动
2. 用例依赖网络/系统模式 → 先 `t.has_network()` / `t.getprop(...)` 判断，不满足标 BLOCKED（环境原因），不硬跑
3. App 特定前置（如"已导入图片并解析"）→ 写在用例脚本开头按步骤执行

## 关键技术（会话验证过的）

### 弹窗看门狗（权限/引导弹窗自动处理）
- `t.start_watchdog(policy="allow|deny")`：启用弹窗自动点击（u2 原生 watcher + 主流程驱动，单连接、零并发 dump）
- `t.watchdog_policy("deny")` 动态切换策略；`t.watchdog_pause()/resume()`；`t.stop_watchdog()`
- 规则：**Android 运行时权限弹窗约 6-8 秒自动消失**，必须检测即点
- Android 14+ 照片权限按钮是 **"选择照片"/"全部允许"**；相机是"仅在使用时允许/仅本次使用时允许/拒绝"
- **两层处理**：
  1. **词表快路径**（毫秒级）：`DIALOG_GUIDE_WORDS`/`DIALOG_ALLOW_WORDS`/`DIALOG_DENY_WORDS` 命中 → u2 watcher 点击
  2. **AI 慢路径**（秒级，兜底未知弹窗）：词表未命中但 UI 树有可点击文本 → 视觉模型识别弹窗（标题/按钮/动作建议/置信度）→ 按建议点击。保护：节流 8s + 每动作窗口最多 3 次 + 置信度 ≥0.7 + 动作与策略一致（allow 策略不点拒绝类）；破坏性操作 AI 倾向点"取消"（已实测：系统"删除应用数据"弹窗 AI 正确点"取消"）
- 词表外的新弹窗：AI 处理过一次后自动学习（按钮文字并入 `framework/dialog_words.json` 对应词表，重复自动去重），下次走快路径；也可手动编辑该文件

## 测试记录（SQLite）

- 每次执行自动入库 `~/dsh-android-test/test_records.db`：用例（cases）/步骤（steps）/断言结果（results，含状态快照与证据路径）
- 命令行查看：`framework/db.py`（`python -c "import sys; sys.path.insert(0,'framework'); from db import get_db; print(get_db().list_cases())"`）

## Web 前端（查看记录 + 编辑知识库，随 skill 打包分发）

- **位置**：本 skill 包内（`webui.sh` 在包根目录，`framework/webui.py` + `framework/webui.html`）
- **启动**：`./webui.sh` → 打开 http://127.0.0.1:8900（`stop`/`status`/指定端口）
- **数据定位**（显式，不依赖脚本所在目录）：
  - 环境变量 `DSH_ANDROID_TEST_DIR` 指定测试工作区（其下 `test_records.db` + `framework/knowledge/`）
  - 默认 `~/dsh-android-test/`；知识库另可用 `DSH_KNOWLEDGE_DIR` 覆盖
  - 打包给别人：对方装好 skill 后跑 `setup.sh` 建工作区，直接 `./webui.sh` 即可
- **测试记录**页：用例列表（通过/失败徽章、搜索、筛选）→ 详情含 用户输入/脚本/状态/证据
- **知识库**页：查看/编辑/新建 `knowledge/*.yaml`，文件名白名单防路径穿越

### 横竖屏不稳定
- 设备可能随机旋转，**禁止写死坐标**；工具栏图标用 `top_bar_icons()`（元素化）
- 工具栏带用绝对像素 [100,450]（工具栏 dp 固定，日历网格 465 起，天然分开）

### Canvas 控件（UI 树读不到）
- **读**：`t.ocr()` / `t.ocr_find()` —— RapidOCR 读画布文字（滚轮数字、选项）
- **操作滚轮**：点按优先于滑动！
  - 点按上方行 = 选中值 -1；点按下方行 = +1；目标可见时直接点按目标数字
  - 滑动有惯性会过冲，点按零惯性（参考 `framework/set_time_tap.py`）
- **验证**：操作后重读 OCR 值，逐步收敛，不一次滑到底

### 置灰按钮断言（视觉模型优先）
- **首选** `assert_button_state_visual(rid, "grayed")`：视觉模型直接看按钮颜色/状态，适合"整体变淡"等像素差值测不出的样式
- 旧方案 `contrast_of(rid)` + `assert_grayed(rid, ref)`：像素对比度法，仅测文字-背景亮度差，对"整块一起变淡"的置灰样式会误报
- 注意：置灰是视觉样式，UI 树里 enabled/clickable 可能不变；**以 case 预期为准**，case 要求置灰而实际未置灰 = FAIL 并说明差别（附截图 + enabled/selected 状态）

### 系统弹窗/Toast
- 弹窗：`screen_text()` 判断；Toast：logcat 或点击后 OCR 捕捉

### 输入
- 中文：`input_text`（u2 send_keys 自动切 ADBKeyboard）
- 清空：`clear_text`

### 结果分类
| 类型 | 含义 |
|---|---|
| ✅ PASS | 符合预期 |
| ❌ FAIL | 不符合预期（产品缺陷或环境，注明原因） |
| ⚠️ WARN | 需人工确认/非致命异常 |
| ⛔ BLOCKED | 环境/前置不满足，无法执行 |
**权限测试注意**：拒绝后权限变 USER_FIXED，系统不再弹窗 —— 每个权限行为（允许/拒绝）拆成独立用例、各自 pm_clear，避免级联失败
| ℹ️ INFO | 记录性信息 |

## 知识卡（App 操作经验）

- `framework/knowledge/<包名>.yaml`：每个 App 的界面结构/高效操作/已知坑/验证要点
- 测试前先查目标 App 的知识卡；用户口述的 App 知识要**记录进知识卡**（如："课程表入口 = 主页→更多→课程表"）
- 未知包名时用 `_system.yaml`（系统弹窗/权限）

## 使用者如何添加知识/用例（两种方式）

**方式 A：自然语言（零门槛，推荐）**
使用者直接口述，AI 负责结构化落盘：
- "记一下：课程表入口是 主页→更多→课程表" → 写入知识卡 `导航入口`
- "测试前要 pm clear 这个包" → 写入知识卡 `前置条件` 或系统工具箱
- "跑这个用例：<步骤+预期>" → 按 `cases/_template.py` 生成用例脚本

**方式 B：模板自行编辑（技术用户）**
- 新 App 知识：复制 `framework/knowledge/_template.yaml` 为 `<包名>.yaml` 填写
- 新用例：复制 `cases/_template.py` 填写步骤与断言
- 新系统级操作：追加到本文件"系统级操作与通用前置条件"表格，或在知识卡里记

**AI 侧的职责**：凡使用者口述了新的 App 知识/系统操作，主动写入对应知识卡/文档，让知识沉淀可复用。

## 注意事项

- **每步操作后统一等待 ≥1s 再截图或 dump UI**：任何点击/输入/启动/返回等操作后，先 `time.sleep(ACTION_DELAY)`（=1s）等界面动画/转场稳定，再截图或 dump_hierarchy；否则会因界面未渲染完而误判（如首启权限页还没出现就 dump，误认为"无弹窗"）。AI 写用例脚本时必须遵守，禁止"操作后立即 dump/截图"。
- 屏幕可能锁屏：框架已自动唤醒解锁
- `d.info` 在 Android 15+ 可能崩溃：用 `app_current`/`dump_hierarchy` 替代
- 坐标以 u2 dump bounds 为准；选择器等系统 UI 布局可能变化 → 用 `first_clickable`/OCR 动态定位，不用写死坐标
- 无法操作时（设备离线/无网络等）：如实标注 BLOCKED 并说明原因，不编造结果
- 测试设备数据可被修改：告知用户副作用，不擅自恢复
