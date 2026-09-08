---
name: android-gui-testing
description: Android 设备黑盒 GUI 功能测试。用户给测试用例（步骤+预期），按本 skill 驱动已连接设备执行并输出带证据的测试报告。内置 uiautomator2 框架、Canvas OCR 读取、点按滚轮控制器、置灰像素断言、知识卡（按前台包名检索 App 操作经验）、PASS/FAIL/BLOCKED/ERROR 结果分类。
---

# Android 设备 GUI 测试（黑盒）

以"真人测试员"的方式在已连接的 Android 设备上执行用户给出的功能测试用例，输出结构化 Markdown 测试报告。

## 环境（先检查，缺啥跑安装脚本）

```bash
adb devices -l                      # 设备在线且已授权
# 框架位于本 skill 目录:
#   framework/test_framework.py  测试框架
#   framework/run_case.py        用例执行器
#   knowledge/*.md              知识卡（按前台包名检索加载 App 操作经验）
#   cases/<包名>/*.py           用例（按被测 App 包名分目录，随版本同步，团队共享）
```

### 安装与运行

- 安装：`bash setup.sh`（Windows：`pwsh -File setup.ps1`）；依赖 `uiautomator2` + `rapidocr_onnxruntime`，
  Python 3.10+（`str | None` 语法要求）。默认工作区 `~/dsh-android-test`
- 跑用例：`.venv/bin/python run_case.py com.zui.calendar/172.py`
  （Windows：`pwsh -File run_case.ps1 -Case "com.zui.calendar/172.py"`）
- 单测（改 framework/ 后必跑，无需设备）：`python -m unittest discover -s tests -v`
  ——改的就是 skill 包里那份，不用再同步到工作区
- **多副本漂移检测**：run_case 启动时比对 正在运行副本 / 工作区备份 / skill 包安装副本
  三方关键文件哈希，不一致会告警并列出各副本路径（本机常有开发仓 + Agent 安装副本两份以上，
  "改了没生效"多半是跑在另一份上——告警里标了「正在运行」的是哪份，本次执行的代码就是它）
- 安装细节/脚本参数/运维排障 → `docs/OPS.md`（人类快速开始 → README）

## 核心原则

1. **以用例（Case）为准**：用例的步骤与预期就是验收标准（spec）。执行后对比实际行为，**凡与 case 不符就是"不准"**——报告 FAIL 并说明差别（case 期望什么 / 实际是什么 / 差在哪），绝不反过来改 case 迁就实际行为。case 表述不清或客观上无法执行时才与用户确认。
2. **每步操作与验证点必须截图留证**：框架已自动为每个 `step()` 开始、`tap_*`/`input_*`/`clear_*` 等操作、`record()` 验证点截图。报告需附证据路径；状态类断言要同时记录实际状态值，不能只写 PASS/FAIL。
3. **元素优先定位（硬规则）**：resource-id > text > content-desc > 坐标。**App 自有控件已知 resource-id 时，禁止改用 text/content-desc 定位**——文案随版本/语言/灰度变化，rid 跨版本稳定；text 定位仅限系统页/第三方页拿不到 rid 的场景，或断言显示内容本身。坐标是最后手段（仅 Canvas/浮层等无元素场景），且必须从元素 bounds 推导（`el_bounds`），绝不写死。
4. **确定性优先**：UI 树能读到的用元素定位，绝不盲猜坐标；Canvas 盲区用 OCR；颜色/布局等像素与 UI 树都不可靠的场景用视觉模型（`vision_ask` / `assert_button_state_visual`）。
5. **每步验证**：操作后必须重读界面确认状态变化；断言用代码，不靠模型自述。
6. **如实分类**：与 case 不符=FAIL（说明差别）；环境/前置不满足=BLOCKED（⛔）；case 与实际都成立但值得记录=WARN/INFO。
7. **默认允许数据清理**：用例以 `pm clear` 等重置 App 数据为前置时，视为测试流程的一部分，AI 不再逐次询问用户。执行前已确认测试设备上的 App 数据可被清理。
8. **信任脚本证据**：脚本执行成功、报告已生成、截图/日志已落盘，即为有效证据。AI 不另起一轮"独立复核"重复验证；若对结果有怀疑，应查看报告和截图，而非再跑一遍用例。
9. **动态事实必须有真机记录，禁止猜**：断言/定位引用的动态内容（toast 文案、弹窗文字、操作后的状态反馈）必须来自探索期的真机记录；记录里没有的，回探索补采触发一次，绝不凭用例文本或想象编造。用例文本描述的是"预期"，真机记录才是"事实"——断言以事实为准。
10. **目标资产必须可验证地选择，禁止盲点第一个**：从列表/网格中选"某个东西"（如图库里的课程表图）时，不能默认 dump 第一个匹配元素就是目标——共享状态（媒体库等）随时可能改变排序。正确姿势：视觉模型按**通用结构特征**排序候选（素材特征写进提示词反而是干扰）→ 逐候选预检 → 利用 App 自带失败信号（失败弹窗文案记入知识卡）做**双态等待**；确定性失败必须准确归因（如"未找到课程表图片"），**禁止把确定性失败归因为超时/网络**。范式见「关键技术 → 图库/列表选资产」。

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
6. **沉淀**：新探明的链路/坑，回写 `knowledge/<包名>.md` 的对应小节——**更新既有小节，勿追加带日期/用例号的新小节**；写卡前过沉淀三问（会搜到吗/防错吗/能下沉吗，约定见 `knowledge/_template.md` 头部注释）；大版本真机回归通过后**整卡重写**（GC：删过时条目、合并按会话堆出来的段落、刷新「最近验证」，卡体积收敛而非只增不减）。≥2 个用例共享的流程函数补进 `cases/<包名>/_flow.py`（一次性链路留在用例文件里，不提取）。

## 变更管控（硬规则，优先级高于本文件其他一切规则）

**所有对框架（`framework/`）与 SKILL.md 内部的修改，必须先经人确认，Agent 不得自行变更。**

- **无例外**：新增功能、修 bug、重构、参数调整、甚至一行的改动，一律先向人呈现「原文 vs 修改后」对比 + 改动理由，**明确确认后**才能落盘。禁止"顺手修了"、"趁跑用例间隙改了"。
- **禁止静默添加功能**：Agent 认为框架缺能力时，只能以**提案**形式提出（现状/问题/建议改法/影响面），由人决定做不做。即使 Agent 100% 确定是 bug，也只有"报告权"，没有"修复权"。
- **目的**：防止架构被随意演进导致 SKILL 前后行为不一致——框架的每一处变化都必须在人可知、可追溯的前提下发生。
- **变更记录**：每次被确认的框架/SKILL.md 修改，随执行在报告或对话中注明「改了什么、为什么、经谁确认」，保证事后可回溯。
- **同步约束**：修改同步到专家包副本属于既定流程的机械复制，不算新修改；但**只允许同步已被确认过的内容**，不得借同步夹带未确认改动。
- **管控范围**：需人确认的 = `framework/` 全部 + 本文件（SKILL.md）。**自由迭代**（2026-09-08 人已确认）= `cases/<包名>/` 用例与 `_flow.py`（执行层）、`knowledge/*.md` 知识卡（探查产出，写错会由用例失败自然暴露并修正）。灰区归属有争议时停下来问人，不自行推断。

## 分层边界（硬规则）

- **`framework/`（TestCase/states）**：与任何 App 无关的原子能力——定位点击、输入、OCR、视觉断言、弹窗看门狗、记录报告。**禁止出现**具体 App 的入口/控件/业务校验（如某 App 的菜单项文字、resource-id、页面特征）。测任何新 App 框架零改动。
- **`knowledge/<包名>.md`**：App 知识——入口在哪、按钮叫什么、resource-id、标准链路、坑。
- **`cases/<包名>/`**：该 App 的用例与 `_flow.py`（可复用流程函数：导航、前置链路；用例只写断言）。目录名就是包名——与 `knowledge/<包名>.md`、`storage/probes/<包名>/` 同键，一个键三处复用，Agent 拿到前台包名即可机械拼出全部路径。`_` 开头文件（`_flow.py`/`_set_time_tap.py`/`_template.py`）是共享模块不是用例，run_case 与 Web UI 都会排除。
- **`framework/states.py`**：只留跨 App 通用的状态判定；App/机型级状态写到 `knowledge/scenarios/*.md` 场景卡（写「判定命令」即自动注册，不改框架代码）。

### 改动归属检查（每次改代码前先过这一问）

**主框架只做通用修改，禁止针对特定场景/App 定制**。写任何新能力前先问：这段代码离开日历（或当前被测 App）还成立吗？

- **成立（通用机制）** → 可进 `framework/`，但 App 特定的部分必须**参数化**，由调用方传入：
  - 提示词（视觉判定问什么）、成功/失败信号文本（弹窗文案）、resource-id、入口路径——一律作为参数，框架内不许出现任何具体 App 的文案/rid/包名常量。
  - 例：`vision_ask(prompt, bounds=...)`、`wait_text(text)` 是通用 API；"等『图片内容不是课程表』弹窗"不是——它只能出现在调用方代码里。
- **不成立（App 特有产经）** → 归宿只有两处：
  - **知识卡 `knowledge/<包名>.md`**：App 事实类——入口、控件 rid、弹窗/Toast 文案、页面特征、坑、解析时长等参数默认值；
  - **`cases/<包名>/_flow.py`**：App 流程类——导航链路、前置步骤、带 App 特定提示词的选图/预检/双态等待组合逻辑。
- **升级路径**：先在某个 App 的 `_flow.py` 里跑通；当第二个 App 需要同一机制时，把通用骨架上提到框架（App 特定部分变成它的参数），而不是复制粘贴到第二个 `_flow.py`。只有一份实现，App 差异全部走参数与知识卡。
- **自检方法**：`grep -n "zui|课表|联想|<某App关键词>" framework/*.py` 应只命中注释/docstring 示例，命中任何逻辑代码即为违规。

## 框架 API 速查

```python
from test_framework import TestCase
t = TestCase("用例名")

t.step("步骤名")                 # 开启一个步骤
t.record("PASS|FAIL|WARN|INFO|BLOCKED", "说明")   # 记录结果
t.blocked("原因")                # 环境/前置阻塞
# ── 动作基元（tap_* / input_text / clear_text 统一契约）──
#   轮询定位（wait=5s）→ 执行 → 弹窗检查/截图留证。返回 bool：True=已执行，
#   False=超时未找到（默认记 WARN 兜底）。调用方有自己的 FAIL/BLOCKED 守卫
#   分支时传 silent=True，避免"守卫记录+WARN 兜底"双重记录
t.tap_el(rid=..., text=..., desc=..., observe=)  # tap_rid/tap_text/tap_desc 的统一入口
t.tap_rid("resource-id") / t.tap_text("文字") / t.tap_desc("content-desc")
                                     # observe=False 用于"触发后立即抓 toast"的动作
t.tap_text_re(r"正则", clickable=None)  # 文本正则点击（同样轮询定位+执行确认）
t.tap_xy(x, y, observe=)          # 坐标点击（坐标须从 el_bounds 推导）
t.el_bounds(rid/text/desc) -> (x1,y1,x2,y2)  # 元素取 bounds（坐标从元素推导）
t.wait_rid(rid, timeout=10) / t.wait_text(text, timeout=10)  # 条件等待元素/文字出现
t.wait_activity(substr, timeout=10) -> str   # 等前台 Activity，超时返回 ""
t.require_tap_text/rid/el(...)               # 必需操作：等不到记 FAIL 并抛 CaseAbort 中止用例
                                             # （轮询命中即在窗口内点击，无"先 wait 后 tap"竞态；
                                             #  tap_* 失败只记 WARN，链路关键步骤用 require_* 防假通过）
t.input_text("rid", "中文文本")   # 中文经 ADBKeyboard 输入；返回 bool（找不到记 WARN）
t.clear_text("rid")               # 同契约，返回 bool
t.read_rid("rid") -> {text, checked, enabled, selected, clickable, bounds}
t.assert_text(rid, expect) / t.assert_switch(rid, "true|false")
t.assert_length_le(rid, 20)      # 输入长度上限
t.vision_ask(prompt, rid=)       # 视觉模型问答（颜色/布局/OCR 盲区）
t.assert_button_state_visual(rid, "grayed"|"clickable")  # 视觉按钮状态断言
t.assert_visual(prompt, expect)  # 视觉断言：回答命中关键词
t.contrast_of(rid) / t.assert_grayed(rid, ref, ratio=0.6)  # 置灰像素断言（旧，视觉优先）
# ── 探查缓存（生成用例阶段用，避免重复 dump/OCR 拖慢首次生成）──
t.probe_page(label, ocr=False, ttl=None, refresh=False, pkg=None)
    # 批量探查当前页，一次返回 {package, texts, rids, nodes[rid/text/bounds/clickable]}
    # 同时落盘 storage/probes/<包名>/<label>/{dump.xml,ocr.json,meta.json}
    # pkg= 缓存归属包名（缺省取前台包名）；探系统页（权限选择器等 com.android.*）
    # 时必须传被测 App 包名，避免缓存散到系统包名下
t.cached_dump(label, ttl=None, refresh=False, pkg=None)   # 取 UI 树，优先读缓存
t.cached_ocr(label, y_min, y_max, refresh=False, pkg=None)  # 取 OCR 结果，优先读缓存
t.find_nodes(label=None, rid_re=, text_re=, cls_re=, clickable=, pkg=)
    # 按正则筛节点；传 label 走缓存（不连设备），不传则实时 dump
t.screenshot("名称")              # 截图留证
t.capture_toast(wait=1.0, y_min=) -> (texts, shot)  # 动作后立即调：截屏定格→OCR 读 toast
                                     # 返回 (全屏文本列表, 截图路径)；读不准配 vision_ask 兜底
t.ocr(y_min, y_max) -> [(x,y,conf,text)]   # Canvas 内容读取
t.ocr_find(keyword, y_min, y_max) / t.first_clickable(y_min, y_max)
t.adb_shell("cmd", "args")        # adb shell 直通（绑定本用例 serial；cases 层禁止裸 subprocess adb）
t.current_package() / t.current_activity()  # 前台包名/完整 Activity
t.finish() -> 报告路径
```

## 新页面探查与用例生成（SOP）

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
看产品反馈，一路到底。探到"打开弹窗"就停 = 把动态事实留给用例期靠猜
（179 教训：toast 文案没见过只能猜，整跑循环 4 轮 40 分钟全耗在这）。
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
同节点同时匹配"第…"与"-"永远失败（175 误报 FAIL 实例）。

**动态文案守门（toast/提示语）**：预期结果里的动态文案，探索记录（`capture_toast`
落盘的文本/截图、trace 快照）里必须出现过；没出现过 = 回 ① 补采触发一次，
**禁止凭用例文本猜文案**。静态节点查库存缓存，动态反馈查探索记录——两条守门线都过了才写断言。

### ④ 真机终验一次
动态行为（跳转/动画/权限时序/Canvas 内容）缓存里没有，合成用例后真机跑一遍收尾。
**禁止用整跑用例来"发现事实"**：合成后拿不准的小断言（一句 toast、一个状态），
写 10 秒级一次性最小探针（只做 触发动作 → 观察落盘，不跑全链路）单独验证；
整跑只做终验这一次。跑 FAIL 后先看报告与截图找根因再改，不要"改一点整跑一轮"
（179 循环 4 轮 40 分钟的教训）。
跑通用例失败重跑可让 goto 链跳过前置（见 _flow 的 skip_if_ready）。结束回写 knowledge/<包名>.md，删采集脚本。

## 系统级操作与通用前置条件

跨 App 通用的设备操作（用例前置条件常用）：

| 场景 | 命令/方法 |
|---|---|
| **首次使用/重置状态** | `t.pm_clear("包名")` = `adb shell pm clear 包名`（清数据回首次引导） |
| 强制停止 App | `t.force_stop(pkg)` |
| 启动 App | `t.launch_app(pkg)`（monkey LAUNCHER 入口，绑定本用例 serial） |
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

## 测试记录（SQLite）与 Web 前端

- 每次执行自动入库 `~/dsh-android-test/storage/test_records.db`（用例/步骤/断言结果，含状态快照与证据路径）
- Web 测试台：`./webui.sh`（Windows：`pwsh -File webui.ps1 start`）→ http://127.0.0.1:8900
  - **测试记录**页：用例列表 → 详情含 用户输入/脚本/状态/证据
  - **知识库**页：编辑 `knowledge/*.md`（`_template.md` 只读；`_system.md` 禁删可编辑）
  - **视觉模型**页：配置凭据（未配置时视觉断言降级 WARN，见下）
- 记录查询命令行 / Web UI 排障 / 报告重建 / 包名回填 → `docs/OPS.md`

### 代码与数据分开放（skill 包 / 工作区）

**代码改 skill 包，数据留工作区** —— 两边各一份、各司其职：

| | 放哪 | 为什么 |
|---|---|---|
| **代码** `framework/` + 根目录脚本 + `SKILL.md` | skill 包（唯一） | Agent 加载的就是这里，改完直接生效 |
| **资产** `cases/` + `knowledge/` | skill 包（唯一） | 版本历史（git）比本地副本有用 |
| **数据** `storage/`（截图/报告/探查缓存/测试库）+ `.venv/` | 工作区（唯一） | 跨 Agent 共享、重装 skill 不会被清空 |

**代码唯一权威是 skill 包**：`run_case.ps1` 的搜索顺序是 skill 包在前、工作区备份在后。
反过来排会导致「改了 skill 包、跑用例却命中工作区旧副本」的静默失效，**别改回去**
（论证与 sync 脚本说明 → `docs/OPS.md`）。

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

### 坐标/方向类事实一律运行时标定
- 知识卡/探查缓存里的坐标、行距、列 x 都是**探查线索**（卡内标注机型+版本），禁止跨运行直接复用：
  方向（横竖屏）、分辨率、App 版本一变就全错（179 竖屏写死坐标在横屏 3040×1904 真机上全偏的教训）
- 正确姿势（179 `calibrate_picker` 范式）：运行时 OCR/dump 现场标定——列按 x 聚类、行按 y 聚类
  去重（同一行 OCR 出多个相邻 y 会让 step 中位数塌成 1）、step 取相邻行距中位数；
  标定结果只在本次运行内有效，不回写卡里当"事实"
- 视觉模型给出的位置 = **假设**：落点前必须与当前 UI dump 的元素 bounds 核对，对不上就重标定
- 用例开始先读一次当次方向/分辨率（`wm size` / `t.d.window_size()`），不做任何方向假设；
  滑动/区域 OCR 的坐标从当次窗口尺寸推导（175 `_swipe_up` 范式），不写死像素

### 图库/列表选资产（视觉排序 + 预检 + 双态等待）
从网格/列表选"内容匹配的目标"（图库里的课程表图、文件选择器里的素材等）时的标准范式
（实现在 `cases/com.zui.calendar/_flow.py → _rank_thumbs/_ensure_grid`，172/175 验证）：
1. **枚举候选**：`find_nodes(rid_re=...)` 拿全部可见条目——不要 `_tap_rid_raw` 盲点第一个
   （同 rid 节点的顺序 = 界面排序 = 共享状态，随时会变）
2. **视觉排序**：逐条目裁剪送 `vision_ask`，提示词只写**通用结构特征**（表格网格/表头/
   单元格文字），不写素材颜色标题等具体特征（遇到其他风格的同类资产会误杀）
3. **大图预检（便宜闸门）**：点入详情/裁剪页后图片变大，先 `vision_ask` 确认再触发下一步
   （解析/上传）——不是目标就返回换候选，不消耗下游轮次，也绕开失败后的不确定状态导航
4. **双态等待**：等"成功信号"或"App 失败信号"（失败弹窗文案必须记入知识卡），两者出现
   任一立即终止本轮；弹窗可能被看门狗抢先点掉，所以"失败弹窗文本出现"和"已退回列表"
   都算命中失败分支（0.5s 级轮询抗竞争）
5. **候选上限**：最多试 3 张，用尽 BLOCKED 并**准确归因**（"未找到课程表图片"），
   禁止归因为超时/网络——归因错误会让下次排查方向全错（172/175 教训）
- 返回列表时**先查是否已回列表，再按 BACK**：失败弹窗点掉后可能已自动回列表，盲按
  BACK 会直接退出选择器

### 视觉模型配置
入口：Web UI 左侧「视觉模型」页（或设 `DEEPSEEK_API_KEY` 环境变量）。
配置保存位置/掩码/优先级等细节 → `docs/OPS.md`；密钥只存工作区，不进 skill 包。

### 系统弹窗/Toast
- **模态弹窗 ≠ Toast，处理方式完全不同**：弹窗有按钮、不点不散，用 `screen_text()` 判断后
  点按钮关闭；toast 无按钮、自动消失（窗口 4s/7s 档——AOSP `ToastPresenter`
  `SHORT_DURATION_TIMEOUT=4000 / LONG_DURATION_TIMEOUT=7000`），只能截屏定格抓取。
  别把「图片内容不是课程表」这类弹窗当 toast 找（找不到按钮、也不会自动消失）
- Toast：**截屏 + OCR，禁止用 logcat**（Toast 缓冲在多数 ROM 上不打印或格式不一，不可靠）。
  Toast 是视觉元素、显示窗口短、不会一直在屏——**动作后立即**调
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
| ℹ️ INFO | 记录性信息 |

**权限测试注意**：拒绝后权限变 USER_FIXED，系统不再弹窗 —— 每个权限行为（允许/拒绝）拆成独立用例、各自 pm_clear，避免级联失败

**用例最终结论（final_status）与进程退出码**：框架显式计算最终结论（规则确定：
FAIL > BLOCKED > WARN > PASS），写入报告头部/汇总、SQLite `cases.final_status` 列
（不再从摘要文本推断）和进程退出码：

| 退出码 | 含义 |
|---|---|
| 0 | PASS / WARN（WARN 需人工看，但不阻断 CI） |
| 1 | FAIL（含 require_* 必需操作失败中止，即 CaseAbort） |
| 2 | BLOCKED（环境/前置不满足） |
| 3 | ERROR（脚本/框架/设备异常） |

只有 BLOCKED 的用例最终结论是 BLOCKED，**不会**显示为 PASS。

**异常路径也有报告**：脚本中途抛异常时 run_case.py 会捕获、设置 `_fatal_error` 并补调
`finish()`——结论压成 ERROR（报告头部附异常信息）照常入库出报告，不会"崩了但 DB 悬挂"。
CaseAbort 例外：它是 require_* 已记 FAIL 的正常中止，按 FAIL 走退出码 1。

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

注：写「判定命令」前先确认这条命令**真的能反映状态**。
例如勿扰不能用 `settings put global zen_mode` 来切换（会被系统覆盖，恒为 0），
得用 `cmd notification set_dnd on|off`；但**读** `zen_mode` 是准的（0 / 2）。

只在**判定逻辑特殊**（要组合多个条件、要额外解释含义）时才手写方法：
在 `states.py` 用 `@state(...)` 装饰（带 desc / triggers / vendor），
手写方法优先级更高，不会被场景卡覆盖。
（自动注册的生成机制与容错细节见 `framework/states.py` 模块 docstring）

### 知识卡分两种

- **App 卡** `knowledge/<包名>.md`：检索索引/标准链路/页面控件/已知坑/验证要点
  （结构约定见 `knowledge/_template.md` 头部注释）
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

### 场景卡目录查找顺序

`states.py` 自动注册场景卡时按以下顺序找 `knowledge/scenarios/`，先命中先用
（同名卡以先扫到的为准，因此工作区的卡可覆盖 skill 包的同名卡）：

1. `DSH_SCENARIOS_DIR` —— 显式指定的场景卡目录（自测用）
2. `$DSH_ANDROID_TEST_DIR/knowledge/scenarios/` —— 测试工作区（本地改的卡先生效）
3. `$DSH_SKILL_DIR/knowledge/scenarios/` —— 环境变量指定的 skill 包位置
   （`run_case.ps1` / `webui.ps1` 启动时自动设置，也支持自定义安装位置）
4. `~/.agents/skills/android-gui-testing/knowledge/scenarios/` —— 默认安装位置
5. states.py 自身所在包（即本 skill 包）的 `knowledge/scenarios/`

## 使用者如何添加知识/用例

使用者口述（"记一下：课程表入口是 主页→更多→课程表"、"跑这个用例：<步骤+预期>"）→
AI 结构化落盘：知识写入 `knowledge/<包名>.md` 对应小节（更新而非追加），
用例按 `cases/_template.py` 生成。技术用户可复制模板自行编辑
（`knowledge/_template.md` / `knowledge/scenarios/sys.无限工作台.md` / `cases/_template.py`，
详细指引 → README）。

**AI 侧的职责**：凡使用者口述了新的 App 知识/系统操作，主动写入对应知识卡/文档，让知识沉淀可复用。

## 注意事项

- **禁止裸 `time.sleep` 等界面**：等页面/元素出现一律用条件等待 `t.wait_rid` / `t.wait_text` / `t.wait_activity`（轮询期间顺带驱动看门狗）。`sleep` 只允许用于非 UI 的 settle（进程退出、服务启动等），且必须写注释说明在等什么。连续两个裸 sleep 是明确的坏味道。
- **既有链路 sleep 属 settle 型可保留**：`cases/<包名>/_flow.py` 等既有链路里的固定 sleep（如冷启动 4s、页面转场 3s）是无元素信号的 settle 等待，数值来自真机实测调优（PhotoPicker 首次冷启动、图片解析等场景没有可条件等待的 rid）。复用这些链路时保持原值；改数值需真机回归。新写代码仍遵守上一条：优先条件等待，sleep 必须带注释。
- **每步操作后统一等待 ≥1s 再截图或 dump UI**：任何点击/输入/启动/返回等操作后，先 `time.sleep(ACTION_DELAY)`（=1s）等界面动画/转场稳定，再截图或 dump_hierarchy；否则会因界面未渲染完而误判（如首启权限页还没出现就 dump，误认为"无弹窗"）。AI 写用例脚本时必须遵守，禁止"操作后立即 dump/截图"。
- **多设备**：`TestCase(device_id=None)` 时要求恰好一台已授权设备；零台/多台会在启动时直接报错。多台时必须 `TestCase("名", device_id="serial")` 显式指定——框架内所有 adb/u2 操作都绑定同一 serial，操作、断言、截图证据不会跨设备分家。报告与 SQLite 记录 serial/型号/Android 版本/屏幕尺寸。
- 屏幕可能锁屏：框架已自动唤醒解锁
- `d.info` 在 Android 15+ 可能崩溃：用 `app_current`/`dump_hierarchy` 替代
- 坐标以 u2 dump bounds 为准；选择器等系统 UI 布局可能变化 → 用 `first_clickable`/OCR 动态定位，不用写死坐标
- 无法操作时（设备离线/无网络等）：如实标注 BLOCKED 并说明原因，不编造结果
- 测试设备数据可被修改：告知用户副作用，不擅自恢复
