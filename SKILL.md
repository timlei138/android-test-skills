---
name: android-gui-testing
description: Android 设备黑盒 GUI 功能测试。用户给测试用例（步骤+预期），按本 skill 驱动已连接设备执行并输出带证据的测试报告。内置 uiautomator2 框架、Canvas OCR 读取、点按滚轮控制器、置灰像素断言、知识卡（按前台包名注入 App 操作经验）、PASS/FAIL/BLOCKED 结果分类。支持你给用例我出报告。
---

# Android 设备 GUI 测试（黑盒）

以"真人测试员"的方式在已连接的 Android 设备上执行用户给出的功能测试用例，输出结构化 Markdown 测试报告。

## 环境（先检查，缺啥跑安装脚本）

```bash
adb devices -l                      # 设备在线且已授权
# 框架位于本 skill 目录:
#   framework/test_framework.py  测试框架
#   framework/run_case.py        用例执行器
#   knowledge/*.md              知识卡（按前台包名自动注入 App 操作经验）
#   cases/<包名>/*.py           用例（按被测 App 包名分目录，随版本同步，团队共享）
```

### 安装（Windows / macOS / Linux）

| 系统 | 安装 | 跑用例 | Web 测试台 |
|---|---|---|---|
| **Windows** | `pwsh -File setup.ps1` | `pwsh -File run_case.ps1 -Case "com.zui.calendar/172.py"` | `pwsh -File webui.ps1 start` |
| macOS/Linux | `bash setup.sh` | `.venv/bin/python run_case.py com.zui.calendar/172.py` | `./webui.sh` |

Windows 脚本参数：
- `setup.ps1 [-Workspace <路径>] [-Python <解释器>] [-SkipDeviceCheck] [-Recreate] [-WithAgent]`
- `run_case.ps1 -Case <用例> [-List] [-Workspace <路径>]`
- `webui.ps1 <start|stop|status|restart> [-Port 8900]`

Windows 脚本为 **PowerShell 5.1 兼容 + UTF-8 with BOM**；已内置处理两项 Windows 特有问题：
原生命令 stderr 日志不触发 `NativeCommandError` 中断、强制 `PYTHONUTF8=1` 避免 GBK 编码 emoji 崩溃。
默认工作区 `~/dsh-android-test`（Windows 下为 `C:\Users\<你>\dsh-android-test`）。

依赖：`uiautomator2` + `rapidocr_onnxruntime`（Python 3.10+ venv，`str | None` 标注语法要求）。

框架纯逻辑有单测（不需要设备）：`python -m unittest discover -s tests -v`。
覆盖 XML 节点解析、场景卡注册、用例名解析、USER_INPUT 提取、路径安全、工作区副本比对。
改 framework/ 后先跑一遍（**改的就是 skill 包里那份，不用再同步到工作区**）。

**工作区 framework 副本**：setup.sh 会往工作区复制一份 framework/，那是历史遗留的备份，
**不参与运行**——`run_case.ps1` 的搜索顺序是 skill 包在前、工作区兜底，正常永远命中 skill 包。
run_case.py 启动时仍会比对两份的哈希并告警，此时告警只表示「备份副本过期」，
不会再导致跑错代码；想清掉就跑 `scripts/sync_skill.ps1`。

## 核心原则

1. **以用例（Case）为准**：用例的步骤与预期就是验收标准（spec）。执行后对比实际行为，**凡与 case 不符就是"不准"**——报告 FAIL 并说明差别（case 期望什么 / 实际是什么 / 差在哪），绝不反过来改 case 迁就实际行为。case 表述不清或客观上无法执行时才与用户确认。
2. **每步操作与验证点必须截图留证**：框架已自动为每个 `step()` 开始、`tap_*`/`input_*`/`clear_*` 等操作、`record()` 验证点截图。报告需附证据路径；状态类断言要同时记录实际状态值，不能只写 PASS/FAIL。
3. **元素优先定位**：resource-id > text > content-desc > 坐标（仅 Canvas/浮层等无元素场景）。坐标是最后手段，且必须从元素 bounds 推导（`el_bounds`），绝不写死。
4. **确定性优先**：UI 树能读到的用元素定位，绝不盲猜坐标；Canvas 盲区用 OCR；颜色/布局等像素与 UI 树都不可靠的场景用视觉模型（`vision_ask` / `assert_button_state_visual`）。
5. **每步验证**：操作后必须重读界面确认状态变化；断言用代码，不靠模型自述。
6. **如实分类**：与 case 不符=FAIL（说明差别）；环境/前置不满足=BLOCKED（⛔）；case 与实际都成立但值得记录=WARN/INFO。
7. **默认允许数据清理**：用例以 `pm clear` 等重置 App 数据为前置时，视为测试流程的一部分，AI 不再逐次询问用户。执行前已确认测试设备上的 App 数据可被清理。
8. **信任脚本证据**：脚本执行成功、报告已生成、截图/日志已落盘，即为有效证据。AI 不另起一轮"独立复核"重复验证；若对结果有怀疑，应查看报告和截图，而非再跑一遍用例。
9. **动态事实必须有真机记录，禁止猜**：断言/定位引用的动态内容（toast 文案、弹窗文字、操作后的状态反馈）必须来自探索期的真机记录；记录里没有的，回探索补采触发一次，绝不凭用例文本或想象编造。用例文本描述的是"预期"，真机记录才是"事实"——断言以事实为准。

## 工作流

1. **解析用例**：从用户消息提取 前置条件/操作步骤/预期结果。缺信息先问，不猜。
2. **先查复用，再动手探查**（首次生成慢的主因就是从零试探，务必按此顺序）：
   1. 读 `knowledge/<前台包名>.md` 的 **「标准链路」** 节点 —— 命中则直接照步骤写，不要重新探路；
      再按**用例涉及的控件/交互类型**对全卡多组关键词检索（滚轮/时间选择器/弹窗/长按/拖动/键盘…）——
      操作经验常写在「标准链路」之外的小节，只搜页面名/入口词必漏
      （179 教训：滚轮点按法已写在卡内，因只搜入口词没读到，走了半天 swipe 弯路）；
   2. 查 `cases/<前台包名>/_flow.py` 有没有现成流程函数 —— 有就 `from _flow import ...`，不要复制粘贴；
   3. 查 `storage/probes/<包名>/<label>/` 缓存（可直接 grep/re 检索 `dump.xml`、`ocr.json`、`meta.json`）—— 有就不连设备；
   4. 都没有，才用 `t.probe_page(label)` 批量探查一次性拿全，仍不要"点一步看一步"。
3. **写用例脚本**（参考 `cases/com.zui.calendar/172.py`）：新建到 `cases/<被测包名>/` 目录下（目录名 = 包名，与知识卡/探查缓存同键），用框架 API 表达步骤与断言。**必须把用户原始口述写进脚本顶部的 `USER_INPUT = """..."""` 常量**（run_case.py 提取后随执行结果一起入库，实现 需求→脚本→结果 全链路追溯）。前置链路优先复用同目录 `_flow.py`，本文件只留断言逻辑。
4. **执行**：`python run_case.py <用例>.py`
5. **出报告**：框架自动生成 `storage/reports/<用例>_报告.md`，把结论汇报给用户。
6. **沉淀**：新探明的链路/坑，回写 `knowledge/<包名>.md` 的「标准链路」等对应小节；≥2 个用例共享的流程函数补进 `cases/<包名>/_flow.py`（一次性链路留在用例文件里，不提取）。

## 分层边界（硬规则）

- **`framework/`（TestCase/states）**：与任何 App 无关的原子能力——定位点击、输入、OCR、视觉断言、弹窗看门狗、记录报告。**禁止出现**具体 App 的入口/控件/业务校验（如某 App 的菜单项文字、resource-id、页面特征）。测任何新 App 框架零改动。
- **`knowledge/<包名>.md`**：App 知识——入口在哪、按钮叫什么、resource-id、标准链路、坑。
- **`cases/<包名>/`**：该 App 的用例与 `_flow.py`（可复用流程函数：导航、前置链路；用例只写断言）。目录名就是包名——与 `knowledge/<包名>.md`、`storage/probes/<包名>/` 同键，一个键三处复用，Agent 拿到前台包名即可机械拼出全部路径。`_` 开头文件（`_flow.py`/`_set_time_tap.py`/`_template.py`）是共享模块不是用例，run_case 与 Web UI 都会排除。
- **`framework/states.py`**：只留跨 App 通用的状态判定；App/机型级状态写到 `knowledge/scenarios/*.md` 场景卡（写「判定命令」即自动注册，不改框架代码）。

## 框架 API 速查

```python
from test_framework import TestCase
t = TestCase("用例名")

t.step("步骤名")                 # 开启一个步骤
t.record("PASS|FAIL|WARN|INFO|BLOCKED", "说明")   # 记录结果
t.blocked("原因")                # 环境/前置阻塞
t.tap_el(rid=..., text=..., desc=..., observe=)  # 元素定位点击；observe=False 跳过弹窗检查链
t.tap_rid("resource-id") / t.tap_text("文字") / t.tap_desc("content-desc")
                                     # observe=False 用于"触发后立即抓 toast"的动作
t.tap_xy(x, y, observe=)          # 坐标点击（坐标须从 el_bounds 推导）
t.el_bounds(rid/text/desc) -> (x1,y1,x2,y2)  # 元素取 bounds（坐标从元素推导）
t.wait_rid(rid, timeout=10) / t.wait_text(text, timeout=10)  # 条件等待元素/文字出现
t.wait_activity(substr, timeout=10) -> str   # 等前台 Activity，超时返回 ""
t.require_tap_text/rid/el(...)               # 必需操作：等不到记 FAIL 并抛 CaseAbort 中止用例
                                             # （tap_* 失败只记 WARN，链路关键步骤用 require_* 防假通过）
t.input_text("rid", "中文文本")   # 中文经 ADBKeyboard 输入
t.clear_text("rid")
t.read_rid("rid") -> {text, checked, enabled, selected, clickable, bounds}
t.assert_text(rid, expect) / t.assert_switch(rid, "true|false")
t.assert_length_le(rid, 20)      # 输入长度上限
t.vision_ask(prompt, rid=)       # 视觉模型问答（颜色/布局/OCR 盲区）
t.assert_button_state_visual(rid, "grayed"|"clickable")  # 视觉按钮状态断言
t.assert_visual(prompt, expect)  # 视觉断言：回答命中关键词
t.contrast_of(rid) / t.assert_grayed(rid, ref, ratio=0.6)  # 置灰像素断言（旧，视觉优先）
# ── 探查缓存（生成用例阶段用，避免重复 dump/OCR 拖慢首次生成）──
t.probe_page(label, ocr=False, ttl=None, refresh=False)
    # 批量探查当前页，一次返回 {package, texts, rids, nodes[rid/text/bounds/clickable]}
    # 同时落盘 storage/probes/<包名>/<label>/{dump.xml,ocr.json,meta.json}
t.cached_dump(label, ttl=None, refresh=False)   # 取 UI 树，优先读缓存
t.cached_ocr(label, y_min, y_max, refresh=False)  # 取 OCR 结果，优先读缓存
t.find_nodes(label=None, rid_re=, text_re=, cls_re=, clickable=)
    # 按正则筛节点；传 label 走缓存（不连设备），不传则实时 dump
t.screenshot("名称")              # 截图留证
t.capture_toast(wait=1.0, y_min=) -> (texts, shot)  # 动作后立即调：截屏定格→OCR 读 toast
                                     # 返回 (全屏文本列表, 截图路径)；读不准配 vision_ask 兜底
t.ocr(y_min, y_max) -> [(x,y,conf,text)]   # Canvas 内容读取
t.ocr_find(keyword, y_min, y_max) / t.first_clickable(y_min, y_max)
t.current_package() / t.current_activity()  # 前台包名/完整 Activity
t.finish() -> 报告路径
```

## 新页面探查与用例生成（SOP，2026-09-03 会话验证）

新 App / 新页面首条用例**不要"写完直接真机跑"**（30 分钟起步的老路：探查往返 + 写错断言整链路返工）。
按下面四步走，真机只占 1-3 分钟，断言错误在真机前就被本地缓存拦截。

### ① 真机采集（导航骨架 + 业务闭环）
复制 `cases/_lib/_collect_template.py` 当骨架：
- 已知导航直接复用 `cases/<包名>/_flow.py` 的 goto_*；全新 App 无 _flow 时用
  `t.d.app_start("包名")` 冷启动 + 首页 `probe_page` 起步（模板已含）
- 未知下钻"一轮一跳"：`t.probe_page("页面标签")` 落盘 → 看打印摘要定下一跳 → 脚本追加一跳再跑
- 脚本开头 `t.set_trace()`：全程 dump 快照 + events 落盘 `storage/traces/<用例>/<会话>/`
  （events 定位"卡在哪个动作"，index/dump 快照看"当时页面状态"，probes 缺料可回头补解析）
- 每轮真机 ≈2-5s；探 5 页 ≈1-3 分钟

**探索的终点是业务闭环，不是页面结构。** 用例每个操作步骤对应的真实用户流程，
必须在探索期完整走一遍到终点：改完设置 → 点"确定" → 处理二次确认弹窗 → 点"完成" →
看产品反馈，一路到底。探到"打开弹窗"就停 = 把动态事实留给用例期靠猜（179 教训：
toast 文案探索期没见过，用例期猜 → 整跑 → FAIL → 改 → 再跑，40 分钟全耗在这）。
- **每个关键动作后立即观察反馈并落盘**：Toast 窗口短（~2-3.5s）且不会一直在屏，
  动作后必须马上 `texts, shot = t.capture_toast()`（截屏 OCR，截图自动留证）；
  弹窗/页面变化/颜色变化同理——动作后的反馈与页面结构同等重要，都是采集产物。
- **冲突/边界/异常分支也要真实触发一次**：预期结果说"点击完成 toast 提示时间冲突"，
  探索期就要真把时间改冲突、点完成、记录产品实际反馈。预期文案 ≠ 真机文案，
  以真机记录为准。

### ② 离线生成页面库存（零真机）
```
python cases/_lib/inventory.py <包名> inventory [label…]
```
把探查缓存摊开成"这一页有什么料"：可交互项 / 可断言 rid（带当前值）/ 结构文本 / 是否含 OCR 缓存。
**写断言前先看库存，别猜节点结构。**

### ③ 逐句离线预检（写断言前必做）
```
python cases/_lib/inventory.py <包名> verify <label> --rids tv_x --texts "按钮文案" --re '正则'
```
每句断言/定位先在缓存上查命中；0 命中即拦截（exit 2）——"写错正则/文案，真机白跑一轮 FAIL"从此不可能。
典型坑：节次"第1节"与时段"08:00-08:30"是**两个独立节点**（rid=tv_slot_number / tv_time_range），
同节点匹配"第…节"与"-"永远失败（175 曾因此误报 FAIL）。

**动态文案守门（toast/提示语）**：预期结果里的动态文案，探索记录（`capture_toast`
落盘的文本/截图、trace 快照）里必须出现过；没出现过 = 回 ① 补采触发一次，
**禁止凭用例文本猜文案**。静态节点查库存缓存，动态反馈查探索记录——两条守门线都过了才写断言。

### ④ 真机终验一次
动态行为（跳转/动画/权限时序/Canvas 内容）缓存里没有，合成用例后真机跑一遍收尾。
**禁止用整跑用例来"发现事实"**：合成后拿不准的小断言（一句 toast、一个状态），
写 10 秒级一次性最小探针（只做 触发动作 → 观察落盘，不跑全链路）单独验证；
整跑只做终验这一次。用例跑 FAIL 后先看报告与截图找根因，再决定改断言还是补探查——
不要"改一点整跑一轮"地循环（179 轮 4 次整跑 40 分钟的教训）。
跑通用例失败重跑可让 goto 链跳过前置（见 _flow 的 skip_if_ready）。结束回写 knowledge/<包名>.md，删采集脚本。

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
- 普通动作（点击/输入）后的弹窗检查窗口默认 3 轮（~1.5s）；**首启/授权/安装/弹窗级联等高风险动作后必须显式调 `t.observe_dialogs(rounds=10)` 开长窗口**——窗口期漏掉的弹窗仍会被后续 wait_* 轮询兜底，但高风险场景不要依赖兜底
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
  - 环境变量 `DSH_ANDROID_TEST_DIR` 指定测试工作区（其下 `test_records.db` + 截图/报告）
- 用例与知识卡单一数据源在 **skill 包** `cases/` 与 `knowledge/`，同步 skill 即获得全部；可用 `DSH_ANDROID_TEST_CASES` / `DSH_KNOWLEDGE_DIR` 覆盖
  - 默认 `~/dsh-android-test/`；知识库另可用 `DSH_KNOWLEDGE_DIR` 覆盖
  - 打包给别人：对方装好 skill 后跑 `setup.sh` 建工作区，直接 `./webui.sh` 即可
- **测试记录**页：用例列表（通过/失败徽章、搜索、筛选）→ 详情含 用户输入/脚本/状态/证据
- **知识库**页：CodeMirror 编辑器查看/编辑 `knowledge/*.md`（高亮、行号、括号匹配），文件名白名单防路径穿越
  - `_template.md` **只读**（新建卡的样式源）；`_system.md` **禁删但可编辑补充**
  - 新建知识卡自动套用 `_template.md` 全文；MD 无语法校验，保存只拦空文件

### 代码与数据分开放（skill 包 / 工作区）

**代码改 skill 包，数据留工作区** —— 两边各一份、各司其职，中间不再需要同步：

| | 放哪 | 为什么 |
|---|---|---|
| **代码** `framework/` + 根目录脚本 + `SKILL.md` | skill 包（唯一） | Agent 加载的就是这里，改完直接生效 |
| **资产** `cases/` + `knowledge/` | skill 包（唯一），git 兜底 | 版本历史比本地副本有用 |
| **数据** `storage/` + `test_records.db` + `.venv/` | 工作区（唯一） | 跨 Agent 共享、重装 skill 不会被清空 |

**为什么数据不跟着进 skill 包**（看着更"单副本"，实际三个坑）：

1. **跨 Agent 会分裂**。机器上可能同时存在多个 Agent 的 skill 目录（`~/.workbuddy/skills/`、
   `~/.agents/skills/` …），数据放进去就是每个 Agent 一份数据库，报告对不上。
   工作区按 `~/dsh-android-test` 定位，所有 Agent 打开的是同一个。
2. **重装会连数据一起没**。分开放时重装 skill 只丢代码（git 里有），数据能活下来。
3. **不污染 git**。skill 包是要入库的仓库，截图动辄上百 MB，进去了仓库就废了。

**代码唯一权威是 skill 包**：`run_case.ps1` 的搜索顺序是 skill 包在前、工作区兜底。
反过来排会导致「改了 skill 包、跑用例却命中工作区旧副本」的静默失效，**别改回去**。
（连带效应：`run_case.py` 把自身所在目录插到 `sys.path[0]`，选中哪份 `run_case.py`，
整套框架和 `cases/` 都跟着那份走，不是只影响一个文件。）

`scripts/sync_skill.ps1` 现在的用途只剩一个：补/刷新工作区那份备份副本。
**日常改代码不需要跑它。** 脚本会跳过内容相同的文件，并校验所有 `.ps1` 带 UTF-8 BOM
（PowerShell 5.1 缺 BOM 会按 GBK 解析报错）。

#### Web UI 改了却没生效 —— 按顺序查这两条

1. **PAGE 缓存**：`webui.py` 把 HTML/JS/CSS 缓存在内存里，同端口上的旧进程不杀掉，就永远返回旧文件。  
   → 症状："文件明明改了，浏览器刷新还是老样子"。**先杀进程再重启**，别只刷新页面。
2. **进程跑在另一份副本上**：机器上可能同时存在多个 skill 包副本，进程命令行指向的那个才是真正生效的目录，改别的副本一律白改。  
   → 症状："重启了还是不对"。排查：查出监听端口的 PID，再看它的命令行指向哪个 skill 包路径（PID → 命令行 → 目录）。  
   → 实测踩过：进程跑在旧副本上，而我一直改活副本，浪费一轮排查。确认后统一到活副本，旧副本要么删除要么反向同步。

> 排查端口占用 / 进程命令行用系统自带命令即可（`netstat -ano | findstr :<port>` 拿 PID，再用 PID 查命令行）。  
> 注意：`wmic.exe` 在本机被安全策略屏蔽，**不要**用它，改用 PowerShell 的 CIM 查询。

#### Web UI 删除失败（SHFileOperationW 0x2 / Failed to fetch）

**不要在 AI 沙箱里的 shell 启动 Web UI。** 沙箱会给 Python 注入「安全删除」shim（通过 `PYTHONPATH` 指向 `cli/vendor/shim/sitecustomize.py`），
它把 `os.remove` / `shutil.rmtree` 改走系统回收站。因为工作区在用户目录下，护栏按「个人文件」处理，于是两种误报：

1. `SAFE_DELETE_FAIL_CLOSED` → 界面报 `{"error": "SHFileOperationW 失败: 0x2"}`。  
   **0x2 是误报**：文件其实已经进回收站了，shim 二次确认时找不到文件才抛异常。别去改代码找 bug。
2. `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（默认阈值 50 次/轮）→ 护栏直接掐断，连接被关闭，浏览器只看到一句 `Failed to fetch`。  
   **此时数据库记录往往已经删掉了**，只是产物没清干净 —— 别重复删。

正确处理：

- **启动方式**：用普通终端跑 `webui.ps1`（这是用户自己的程序，删的是自己生成的产物，不该被护栏管）。  
  必须在 AI 侧拉起时，清掉注入再启动：`env -u PYTHONPATH python webui.py --port 8900`。
- **代码侧已加固**（不依赖运行环境）：`db.safe_remove()` / `safe_rmtree()` 以「文件还在不在」判定成败，不信异常；
  `delete_case()` 的产物清理整体包了 try/except，产物删不掉也绝不回滚数据库、绝不往外抛；
  `Handler` 的四个 HTTP 动词都套了 `_guarded`，任何异常都转成 JSON 500，**永不掐断连接**。

#### 报告文件丢了 / 内容对不上 —— 可以直接重建

报告只是 `steps` / `results` / `step_evidences` 的 Markdown 视图，**数据全在库里**，
文件没了随时能重算，不用重跑用例。

```bash
python framework/report_rebuild.py 105          # 重建指定记录
python framework/report_rebuild.py --all        # 重建所有"对不上"的
python framework/report_rebuild.py --force-all  # 强制重建全部（报告没坏也重建）
```

`--force-all` 用于**报告字段变更后刷新历史文件内容**：`--all` 只修"对不上"的，
字段改了但文件没坏的情况它不管。典型场景是 `package` 入库后，要把老报告里
「被测 App：C:\...\169.py」（其实是脚本路径）刷成真正的包名。

Web UI 里：记录详情右上角 **♻️ 重建报告**。

自动识别三种"对不上"：

1. **文件缺失** —— 跨机器迁移、路径变更、清理误伤
2. **多条记录共用一个报告文件** —— `<name>_报告.md` 是 finish() 的既定语义（重跑覆盖），
   历史记录都指向它，结果除最新一条外看到的都是别人的报告
3. **报告内容不是本条的** —— 落款时间既不等于 started_at 也不等于 finished_at（容差 60s）

修复策略：共用报告时**自动另存独立文件** `<name>_<运行时间>_报告.md`，绝不覆盖别人的报告 ——
否则修完这条、又弄坏那条，来回打转。最新一条继续持有原路径。

> 幂等：跑完再跑一次应返回「成功 0 / 失败 0」。

#### 被测 App（包名）入库

报告头部的「被测 App」曾**只写进文件**，报告一丢就永久没了 —— 所以它现在进 `cases.package` 列：

- `finish()` 收尾时从 `d.app_current()` 取包名入库（取不到就留空，**不覆盖已有值**）
- 重建报告时渲染顺序：`package` → `script_path` → `unknown`
- 老记录（加列之前跑的）用 `db.backfill_package()` 回填：

```python
db.backfill_package(dry_run=True)   # 先看能补哪些
db.backfill_package()               # 正式写库（幂等，已有值的不动）
```

回填只认 `cases/<包名>/<脚本>.py` 这种结构、且目录名长得像包名（纯 ASCII、含点、无空白）。
像 `cases/联想日历_168.py` 这种目录名不是包名的**一律跳过，不猜**。

### 横竖屏不稳定
- 设备可能随机旋转，**禁止写死坐标**；优先元素定位（`tap_rid`/`tap_text`/`tap_desc`），坐标必须从元素 bounds 推导

### Canvas 控件（UI 树读不到）
- **读**：`t.ocr()` / `t.ocr_find()` —— RapidOCR 读画布文字（滚轮数字、选项）
- **操作滚轮**：点按优先于滑动！
  - 点按上方行 = 选中值 -1；点按下方行 = +1；目标可见时直接点按目标数字
  - 滑动有惯性会过冲，点按零惯性（参考 `cases/com.zui.calendar/_set_time_tap.py`，日历滚轮示例）
- **验证**：操作后重读 OCR 值，逐步收敛，不一次滑到底

### 置灰按钮断言（视觉模型优先）
- **首选** `assert_button_state_visual(rid, "grayed")`：视觉模型直接看按钮颜色/状态，适合"整体变淡"等像素差值测不出的样式
- 旧方案 `contrast_of(rid)` + `assert_grayed(rid, ref)`：像素对比度法，仅测文字-背景亮度差，对"整块一起变淡"的置灰样式会误报
- 注意：置灰是视觉样式，UI 树里 enabled/clickable 可能不变；**以 case 预期为准**，case 要求置灰而实际未置灰 = FAIL 并说明差别（附截图 + enabled/selected 状态）
- **未配置凭据时视觉断言会降级为 WARN**（不是 FAIL）。需要真实判断时，到 Web UI 左侧**「视觉模型」**页配置，或设 `DEEPSEEK_API_KEY`

### 视觉模型配置
- 入口：Web UI 左侧 **「视觉模型」**（`/#view-vision`）
- 可配 `Base URL` / `Model` / `API Key`，「测试连接」会发最小 chat 请求自检
- 保存位置：**工作区** `storage/vision.json`（权限 600）。**不进 skill 包**，`sync_skill.ps1` 不同步 `storage/`，所以分享 skill 包不会泄露密钥
- API Key 只回显掩码（前 4 后 4，中间打码），GET 接口不返回明文；**保存时留空 = 保留原值**（防止只想改 model 却误清空密钥）
- 运行时优先级：`DEEPSEEK_API_KEY` 环境变量 > 本页配置 > `~/.dsh/.credentials.yaml`
- 跨平台：路径统一由 `db.default_test_dir()` 解析（Windows `~/dsh-android-test`、Linux/macOS 同逻辑），无平台分支代码

### 系统弹窗/Toast
- 弹窗：`screen_text()` 判断
- Toast：**截屏 + OCR，禁止用 logcat**（Toast 缓冲在多数 ROM 上不打印或格式不一，不可靠）。
  Toast 是视觉元素、显示窗口短（~2-3.5s）、不会一直在屏——**动作后立即**调
  `texts, shot = t.capture_toast()`（内部等 1s 让 toast 渲染 → 截图落盘留证 → OCR 返回文本列表），
  断言 `any("目标文案" in s for s in texts)`；错过窗口 toast 就没了，只能重做触发动作
- **触发动作用 `observe=False`**：`tap_xy/tap_text/tap_rid(..., observe=False)` 跳过点击后的
  弹窗检查窗口(~1.5s)+自动截图——那条链会占满 toast 显示窗口，capture_toast 必抓空
  （179 的 AB 隔离实验实锤）。普通动作保持默认 observe=True（弹窗保护不丢失）
- 半透明 toast OCR 混背景读不准时，视觉模型兜底：`t.vision_ask("屏幕上是否有 toast？内容是什么")`
- 探索期（采集脚本）每个关键动作后就地 capture_toast 落盘，见 SOP ①——
  断言里的 toast 文案必须来自这些记录，不是用例期现抓

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

**用例最终结论（final_status）与进程退出码**：框架显式计算最终结论（规则确定：
FAIL > BLOCKED > WARN > PASS），写入报告头部/汇总、SQLite `cases.final_status` 列
（不再从摘要文本推断）和进程退出码：

| 退出码 | 含义 |
|---|---|
| 0 | PASS / WARN（WARN 需人工看，但不阻断 CI） |
| 1 | FAIL（含 require_* 必需操作失败中止） |
| 2 | BLOCKED（环境/前置不满足） |
| 3 | ERROR（脚本/框架/设备异常，含用例未调 finish()） |

只有 BLOCKED 的用例最终结论是 BLOCKED，**不会**显示为 PASS。

## 知识卡（App 操作经验）

知识分两类，**先判断属于哪类，再决定放哪**：

| 类型 | 放哪 | 判断标准 |
|---|---|---|
| **可执行判断** | `framework/states.py` | 能用一条命令确定性地测出 True/False |
| **经验知识** | `knowledge/*.md` | 需要人传授的操作路径、坑、注意事项 |

**"怎么检测某个状态"一律写进 states.py，不要写成知识卡。**
写成知识卡 = 每次要进 AI 上下文（占 token）还可能被猜错；
写成代码 = 上下文成本为零、结果确定、能直接当断言用。

```python
t.states.is_vision_mode()                              # 是否处于无限工作台
t.assert_true(t.states.is_vision_mode(), "应处于无限工作台")
python framework/states.py --list                      # 列出全部可用检测
```

### 新增状态检测：只改一处

**在场景卡里写「判定命令」就会自动注册同名方法，不必再改 states.py。**

```markdown
# knowledge/scenarios/sys.勿扰模式.md
场景: 勿扰模式
触发词:
- 勿扰模式
判定方法: states.is_dnd_mode()          # ← 方法名由此推导
判定命令: settings get global zen_mode  # ← 自动注册 is_dnd_mode() / raw_dnd_mode()
判定说明: 0=关闭，非0=开启
```

保存后 `t.states.is_dnd_mode()` 直接可用，`--list` 里也会出现。
（`adb shell` 前缀可写可不写，两种写法都能解析。）

生成过程（全自动，无需干预）：

1. 模块导入时 `load_scenarios()` 扫 `knowledge/scenarios/*.md`
2. 从「判定方法」正则抽出方法名 → `is_dnd_mode`
3. 把「判定命令」拆成参数，吃掉 `adb shell` 前缀 → `["settings","get","global","zen_mode"]`
4. 往 `States` 类挂两个方法：`is_dnd_mode()`（判定）+ `raw_dnd_mode()`（原始值，排查用）

注：写「判定命令」前先确认这条命令**真的能反映状态**。
例如勿扰不能用 `settings put global zen_mode` 来切换（会被系统覆盖，恒为 0），
得用 `cmd notification set_dnd on|off`；但**读** `zen_mode` 是准的（0 / 2）。

只在**判定逻辑特殊**（要组合多个条件、要额外解释含义）时才手写方法：
在 `states.py` 用 `@state(...)` 装饰（带 desc / triggers / vendor），
手写方法优先级更高，不会被场景卡覆盖。

为什么默认走场景卡：两处都要改迟早会漏，漏了就是 `AttributeError`
让用例直接崩、而不是报 FAIL。自动注册把这个失败模式消掉了。

忘了实现时报错会直接告诉你怎么补（列出可用方法 + 两种补法），
不会只丢一个方法名。

### 知识卡分两种

- **App 卡** `knowledge/<包名>.md`：界面结构/高效操作/已知坑/验证要点
  - 测试前先查目标 App 的知识卡；用户口述的 App 知识要**记录进知识卡**
- **场景卡** `knowledge/scenarios/sys.<场景>.md`：跨 App 的操作场景（进入/退出/注意事项）
  - 头部键值区（场景/触发词/判定命令）是机器解析区，保持朴素「键: 值 / - 列表」写法
- 未知包名时用 `_system.md`（系统弹窗/权限，常驻，保持精简）

### 场景卡懒加载（重要）

`knowledge/scenarios/` 会越攒越多，**不要一次性读全部**。先 grep 定位，再只读命中的那一个：

```bash
grep -rl "<触发词>" knowledge/scenarios/     # 定位场景卡
```

场景卡自带 `触发词:` 字段，grep 命中即读；未命中说明还没有该场景的知识（可新建）。
根因：全量读取会让上下文随场景数线性膨胀，而单次用例通常只涉及 1-2 个场景。

## 使用者如何添加知识/用例（两种方式）

**方式 A：自然语言（零门槛，推荐）**
使用者直接口述，AI 负责结构化落盘：
- "记一下：课程表入口是 主页→更多→课程表" → 写入知识卡 `导航入口`
- "测试前要 pm clear 这个包" → 写入知识卡 `前置条件` 或系统工具箱
- "跑这个用例：<步骤+预期>" → 按 `cases/_template.py` 生成用例脚本

**方式 B：模板自行编辑（技术用户）**
- 新 App 知识：复制 `knowledge/_template.md` 为 `<包名>.md` 填写
- 新场景知识：复制 `knowledge/scenarios/sys.无限工作台.md` 为 `sys.<场景>.md`
- 新状态检测：在场景卡里写「判定命令」即自动注册（只有逻辑特殊时才手写
  `framework/states.py` 的 `@state` 方法）
- 新用例：复制 `cases/_template.py` 填写步骤与断言
- 新系统级操作：追加到本文件"系统级操作与通用前置条件"表格，或在知识卡里记

**AI 侧的职责**：凡使用者口述了新的 App 知识/系统操作，主动写入对应知识卡/文档，让知识沉淀可复用。

## 注意事项

- **禁止裸 `time.sleep` 等界面**：等页面/元素出现一律用条件等待 `t.wait_rid` / `t.wait_text` / `t.wait_activity`（轮询期间顺带驱动看门狗）。`sleep` 只允许用于非 UI 的 settle（进程退出、服务启动等），且必须写注释说明在等什么。连续两个裸 sleep 是明确的坏味道。
- **每步操作后统一等待 ≥1s 再截图或 dump UI**：任何点击/输入/启动/返回等操作后，先 `time.sleep(ACTION_DELAY)`（=1s）等界面动画/转场稳定，再截图或 dump_hierarchy；否则会因界面未渲染完而误判（如首启权限页还没出现就 dump，误认为"无弹窗"）。AI 写用例脚本时必须遵守，禁止"操作后立即 dump/截图"。
- **多设备**：`TestCase(device_id=None)` 时要求恰好一台已授权设备；零台/多台会在启动时直接报错。多台时必须 `TestCase("名", device_id="serial")` 显式指定——框架内所有 adb/u2 操作都绑定同一 serial，操作、断言、截图证据不会跨设备分家。报告与 SQLite 记录 serial/型号/Android 版本/屏幕尺寸。
- 屏幕可能锁屏：框架已自动唤醒解锁
- `d.info` 在 Android 15+ 可能崩溃：用 `app_current`/`dump_hierarchy` 替代
- 坐标以 u2 dump bounds 为准；选择器等系统 UI 布局可能变化 → 用 `first_clickable`/OCR 动态定位，不用写死坐标
- 无法操作时（设备离线/无网络等）：如实标注 BLOCKED 并说明原因，不编造结果
- 测试设备数据可被修改：告知用户副作用，不擅自恢复
