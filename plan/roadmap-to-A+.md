# android-test-skills 质量进阶计划：A- → A+（实现版）

> 来源：2026-09-09 全量代码评审（三轮核销后基线 = A-，全部严重/中等问题已清零）。
> 本文档含实现细节与验收标准，可直接拆成任务执行。
> **变更管控**：涉及 `framework/`、`SKILL.md`、`docs/` 的改动，实施前先出
> 「原文 vs 修改后」提案经人确认；本文档本身只是计划，不构成变更授权。
> 工作量标注：S < 半天，M = 1-2 天，L > 2 天。

---

## 现状基线

- 单用例执行器 + 知识工程已达 A-：分层边界严格执行、结果语义机器可消费、
  105 单测全绿、证据链完整、多副本漂移可检测、adb 调用统一超时。
- 核心缺口是三块体系能力：
  1. 从「用例」到「套件」（runner + 质量信号）
  2. 从「代码测试」到「Agent 行为测试」（eval 回路）
  3. 从「能装上」到「可交付」（版本钉 + CI + smoke）

---

## 阶段一：质量信号层（数据全在 SQLite 里，纯增量）

### 1.1 Flakiness 聚合视图 【M】

**实现细节**

1. `framework/db.py` 新增查询方法（零 schema 变更，`script_path` 已入库）：
   ```python
   def case_history(self, script_path, limit=20):
       """同一用例脚本的近期执行历史（新→旧）。"""
       # SELECT id, final_status, started_at, finished_at, report_path,
       #   ROUND((julianday(finished_at)-julianday(started_at))*86400,1) AS dur
       # FROM cases WHERE script_path=? AND final_status IS NOT NULL
       # ORDER BY id DESC LIMIT ?

   def flaky_stats(self, min_runs=5):
       """全量用例的通过率统计。返回 [{script_path, package, runs, pass_rate, flaky}]
       flaky 判定：runs >= min_runs 且 0.2 < pass_rate < 0.8
       通过口径（定案）：final_status IN ('PASS','WARN') 计通过——
       WARN 退出码 0、不阻断 CI，不算 flaky 信号；
       FAIL / BLOCKED / ERROR 计未通过。"""
       # SELECT script_path, package, COUNT(*) n,
       #   SUM(CASE WHEN final_status IN ('PASS','WARN') THEN 1 ELSE 0 END)*1.0/COUNT(*) rate
       # FROM cases WHERE script_path IS NOT NULL GROUP BY script_path
   ```
2. `framework/webui.py` 新增 API：`GET /api/flaky` → `flaky_stats()` 结果；
   `GET /api/cases/<id>/history` → 同 script_path 的历史序列。
3. 前端：记录列表加「通过率」列（flaky 用例打 🔀 标记）；详情页加历史时间线
   （近 20 次 final_status 色块 + 耗时折线，纯 JS 无依赖）。

**验收标准**

- [ ] 单测：构造 10 条记录（6 PASS / 4 FAIL 同 script_path）→ `case_history`
      返回 10 条且顺序正确；`flaky_stats` 返回 pass_rate=0.6、flaky=True。
- [ ] 单测：4 条记录（< min_runs）→ flaky=False（样本不足不误报）。
- [ ] 手测：Web UI 列表页能看到通过率列，flaky 用例有标记。

### 1.2 套件级 runner 【L】

**实现细节**

1. 新增 `framework/run_suite.py`（复用 `run_case.CASE_DIRS` / `_iter_case_files`）：
   ```python
   python run_suite.py [--package com.zui.calendar] [--jobs 1] [--device SERIAL]
   ```
   - **隔离模型：子进程**。每个用例 = `[sys.executable, run_case.py, <相对路径>]`
     独立进程，天然隔离 LAST_CASE 全局态/设备连接/异常爆炸半径；
     退出码直接复用现有语义（0/1/2/3）。
   - 单用例失败**不中断**套件；实时打印进度条式行输出。
   - **设备断连熔断**：子进程返回 3（ERROR）时，runner 先 `adb devices`
     检查绑定设备是否仍在线——
     - 不在线（USB 松动/掉线）→ **立即终止套件**，聚合报告标注
       「设备断连，剩余 N 个用例未执行」，退出码 3。
       不浪费时间跑注定全部超时的后续用例（30 分钟全 ERROR 的场景）；
     - 在线 → 判定为用例自身 ERROR，记录后继续下一个。
   - 聚合报告：`storage/reports/suite_<时间戳>_报告.md`
     （每用例一行：结论/耗时/报告路径/退出码）。
   - 套件退出码：任一 FAIL→1；无 FAIL 有 ERROR→3；仅 BLOCKED→2；全 PASS/WARN→0
     （与单用例语义同构，CI 可直接消费）。
2. DB（可选，走 `_MIGRATIONS` 追加 ALTER 的既有模式）：
   ```sql
   CREATE TABLE IF NOT EXISTS suites (
     id INTEGER PRIMARY KEY AUTOINCREMENT,
     started_at TEXT, finished_at TEXT, filter TEXT,
     total INT, pass_n INT, fail_n INT, blocked_n INT, error_n INT, warn_n INT,
     report_path TEXT);
   ALTER TABLE cases ADD COLUMN suite_id INTEGER;
   ```
   run_suite 启动时插入 suites 行，子进程经环境变量 `DSH_SUITE_ID` 传入，
   `TestCase.__init__` 读并入 cases 行（无该环境变量 = 单跑，行为不变）。
3. 并行（`--jobs N`，P2 可延后）：设备池 = `adb devices` 已授权列表；
   worker i 绑 serial i，经新增的 `run_case.py --device SERIAL` 参数透传到
   `TestCase(device_id=...)`（框架已支持，run_case 目前未暴露）。
   **注意**：并行时截图目录/报告按 case_<时间戳> 天然隔离，无共享冲突；
   DB 已有 WAL + 锁，子进程并发写安全。

**验收标准**

- [ ] 单测（mock subprocess）：3 个假用例退出码 0/1/2 → 套件退出码 1，
      报告含三行且结论正确；FAIL 用例后的用例仍被执行。
- [ ] 单测（设备断连）：mock 子进程返回 3 + `adb devices` 无设备 →
      套件立即终止、后续用例不执行、报告含「剩余 N 个未执行」；
      mock 设备在线 → 套件继续。
- [ ] 单测：未知/空套件 → 退出码 3 且提示无匹配用例。
- [ ] 真机手测：`run_suite.py --package com.zui.calendar` 全量跑通，
      聚合报告生成，DB suites 行与各 cases.suite_id 正确关联。
- [ ] `--jobs 2` 双设备手测（有双设备条件时）：两设备并行不串台
      （抽查两份报告的设备栏 serial 不同且各自一致）。

### 1.3 环境漂移检测 【M】

**实现细节**

1. `framework/states.py` 新增（跨 App 通用，归属正确层）：
   ```python
   @state("env_snapshot", "设备环境快照（漂移检测基线）")
   def env_snapshot(self):
       """返回 dict：accelerometer_rotation / user_rotation /
       stay_on_while_plugged_in / zen_mode / 前台包。"""
       # settings get system accelerometer_rotation 等，复用 self.settings_get

   def env_diff(self, before: dict) -> dict:
       """与基线比对，返回 {键: (前, 后)} 的差异子集。"""
   ```
2. `framework/test_framework.py`：
   - `TestCase.__init__`：`self._env_baseline = self.states.env_snapshot()`
     （states 初始化失败时跳过，不阻断）；
   - `finish()` 报告生成前：`diff = self.states.env_diff(self._env_baseline)`，
     非空 → 自动 `record("WARN", f"用例污染设备环境: {diff}")`——
     结论从 PASS 升 WARN，污染可见但不误杀；
   - 豁免口：`TestCase(env_ignore=("user_rotation",))`，旋屏用例
     （restore_rotation 成对使用的）显式豁免。
3. **快照时机（定案，评审结论①）**：`lock_portrait()` 在 `run()` 里调用、
   会改 rotation 两键——基线若只在 `__init__` 取一次，每个用例都误报漂移。
   定案为**两次取基线**：
   - `TestCase.__init__` 取初始基线；
   - **`lock_portrait()` 方法内执行完后自动刷新基线**（框架方法内部刷新，
     不侵入用例 run() 流程）。
   由此 rotation 两键**纳入默认监控**（锁竖屏后基线已含锁定态，
   用例中途意外转屏也能被捕获），监控键全量 =
   `accelerometer_rotation, user_rotation, stay_on_while_plugged_in,
   zen_mode, 前台包`。转屏用例用 `env_ignore` 显式豁免。

**验收标准**

- [ ] 单测：fake adb 让 `stay_on_while_plugged_in` 前后值不同 → finish 报告
      含「污染」WARN 且 final_status=WARN。
- [ ] 单测：env_ignore 命中的键被豁免。
- [ ] 真机手测：跑 172.py（无泄漏）→ 无 WARN；跑一个故意 `svc power stayon false`
      的探针 → 有 WARN。

---

## 阶段二：Agent 行为评测回路

### 2.1 生成质量 eval 集 【L】

**实现细节**

分两层，确定性优先：

1. **静态 lint 层（可 CI，必做）**——新增 `evals/lint_case.py`，
   对生成的用例脚本做硬规则检查（ast 解析，不执行）：
   | 规则 | 检查方式 |
   |---|---|
   | 必须含 `USER_INPUT` 字符串常量 | 复用 `run_case.extract_user_input_from_source` |
   | 禁止裸坐标 `tap_xy` | ast 找 Call：**位置参数与关键字参数（x=/y=）全部为常量字面量**才违规——`tap_xy(100,200)` 与 `tap_xy(x=100,y=200)` 同一判定；参数含变量/表达式（`cx+5`、`el.center`、BinOp/Name/Attribute）说明坐标从元素 bounds 推导，合法放行 |
   | 禁止无注释裸 `time.sleep` | ast 找 sleep Call，同行/前一行无注释即违规 |
   | App 自有控件禁止 text 定位 | `tap_text("非系统弹窗词")` 出现在非弹窗上下文 → 提示级 |
   | 链路关键步骤用 `require_*` | 提示级（启发：tap 后无 if 判返回值） |
   - 退出码：违规 → 1（附行号）；仅提示 → 0。
   - 存量用例做基线：`172.py`/`175.py`/`179.py` 必须全绿（违规先修或加白名单注释）。
2. **动态事实守门层（可 CI，必做）**——`evals/check_facts.py`：
   **守门范围收窄到 toast 类动态文案**（toast 文案是真正的动态事实，
   全量 record detail 守门误报率太高，评审结论⑤）：
   - 只检查 `capture_toast` 结果下游的断言匹配词
     （`any("..." in s for s in texts)` 形态里的字符串字面量）；
   - 字符串 < 4 字跳过（短串 grep 噪声太大，"确定" 会命中不相关内容）；
   - 前缀白名单自动豁免：「步骤 / 完成 / 成功 / 已 / 阻塞」开头的
     record detail 是过程描述，不是事实引用，不参与守门。
   在 `storage/probes/` + `storage/traces/` 语料 grep 不到出处的，
   标记「疑似编造文案」→ 回探索补采。
3. **Agent 行为层（半自动，选做）**——`evals/prompts/` 存 3-5 组标准输入
   （口述用例 + 期望的行为 checklist：先读知识卡？复用了 _flow？），
   由人/宿主 Agent 跑生成后按 rubric 打分。这层不进 CI，作为版本发布前的人工门禁。

**验收标准**

- [ ] 单测：fixture 坏用例（含 `tap_xy(100,200)`、无 USER_INPUT、裸 sleep）
      → lint 退出码 1 且报对行号；fixture 好用例 → 退出码 0。
- [ ] 存量基线：`lint_case.py cases/com.zui.calendar/172.py` 等全绿。
- [ ] CI 接入后，提交一个带违规的新用例 → 流水线红。

### 2.2 上下文预算门禁 【S】

**实现细节**

1. 新增 `scripts/check_context_budget.py`：
   ```python
   BUDGETS = {  # (文件glob, 行数上限, 超多少报警)
       "SKILL.md": 280,   # 当前 212 行 = 76%，留足注释余量（260 会踩 80% 警告线）
       "knowledge/_system.md": 200,
       "knowledge/*.md": 400,        # 每张 App 卡
       "knowledge/scenarios/*.md": 80,
   }
   ```
   - 行数为主指标（稳定、可读）；token 估算 = 行内中文字符 ×1 + 英文词 ×1.3，
     仅作参考输出不卡线。
   - 超预算 → exit 1 并打印文件名/当前值/预算；达 80% → 警告不红。
2. 挂 CI（见 3.2）+ 文档注明「卡写不下时先 GC（整卡重写）再扩预算，
   扩预算需在 PR 里说明理由」。

**验收标准**

- [ ] 单测：临时目录造超限文件 → exit 1 且输出指名该文件。
- [ ] 当前仓库全量检查通过（若有已超限的卡，先做一次 GC 或校准预算值）。

### 2.3 知识卡新鲜度追踪 【S-M】

**实现细节**

1. 数据源零改动：`cases.package` 已入库，知识卡文件名 = `<package>.md`，天然可 join。
2. `framework/db.py` 新增：
   ```python
   def card_freshness(self):
       """每包的最近验证时间。返回 {package: {last_pass_at, last_run_at, case_id}}
       验证定义：final_status='PASS' 的执行（WARN 不算验证通过）。"""
       # SELECT package, MAX(started_at) FROM cases
       #  WHERE final_status='PASS' AND package IS NOT NULL GROUP BY package
   ```
3. `framework/webui.py` 知识库页 API 返回卡列表时合并 freshness：
   每张卡显示「最后验证： 2026-09-09 (case #105)」/「从未验证」/「>30 天未验证 🔴」。
4. 命令行：`python framework/db.py --stale-cards 30` 列出过期卡（OPS.md 补一节）。

**验收标准**

- [ ] 单测：插入 package=com.x 的 PASS 记录 → `card_freshness()` 返回该包日期；
      WARN 记录不计入。
- [ ] 手测：Web UI 知识库页显示验证时间；无记录卡显示「从未验证」。

---

## 阶段三：交付工程化

### 3.1 依赖钉版本 【S】

**实现细节**

1. 从当前工作区 venv 取基线：`pip freeze | grep -iE "uiautomator2|rapidocr|pillow|numpy|onnxruntime"`
   生成 `requirements.txt`（精确 `==` 钉主依赖，传递依赖注释说明）。
2. `setup.sh:37` / `setup.ps1` 对应行改为：
   `pip install -q -r "$HERE/requirements.txt"`（保留 import 自检）。
3. README「环境要求」节补一句：升级依赖 = 改 requirements + 真机回归一个用例。

**验收标准**

- [ ] 干净机器/干净 venv 执行 setup → `pip freeze` 与 requirements 一致，
      172.py 真机跑通。
- [ ] CI 加一步 `pip install -r requirements.txt` 验证可解析。

### 3.2 版本标识 + CI 【S-M】

**实现细节**

1. `framework/VERSION`（纯文本，如 `1.0.0`）；
   `run_case.py` 启动 `[路径]` 输出块加一行 `[版本] 1.0.0`；
   `warn_if_framework_drift` 把 VERSION 纳入 `_DRIFT_KEY_FILES`——
   版本不一致直接暴露副本新旧。
2. `.github/workflows/test.yml`：
   ```yaml
   name: test
   on: [push, pull_request]
   jobs:
     unittest:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: "3.11" }
         - run: pip install -r requirements.txt
         - run: python -m unittest discover -s tests -v
         - run: python scripts/check_context_budget.py      # 2.2 落地后启用
         - run: python evals/lint_case.py cases/ --baseline # 2.1 落地后启用
   ```
3. `export.sh` 打包时把 VERSION 写进归档名：
   `android-test-skills-1.0.0.tar.gz`。
4. 语义化版本约定写进 OPS.md：框架行为变更 = minor，规则/契约变更 = major，
   文档/知识卡 = patch。
5. **CI 设备约束（显式声明，评审结论⑥）**：CI runner 无 Android 设备，
   `adb devices` 恒空。因此 CI 只跑**无设备层**：单测（tests/ 本就无需设备）、
   lint、预算门禁。**所有真机验收（1.2 suite 手测、1.3 漂移手测、3.3 smoke）
   一律在本地开发机执行**，验收标准里的「真机手测」项不构成 CI 步骤。
   workflow 中不引入 `with_device` matrix——本阶段没有云上设备池，
   写了也是死代码；将来有设备农场时再扩。

**验收标准**

- [ ] push 后 CI 绿；故意改坏一个单测 → CI 红。
- [ ] `python run_case.py 172` 输出含版本行；不同版本的两份副本触发漂移告警。

### 3.3 安装后 smoke 用例 【S】

**实现细节**

1. 新增 `framework/smoke.py`（放 framework 而非 cases/，避免被用例发现机制收录）：
   ```python
   t = TestCase("PROBE_smoke")   # PROBE_ 前缀 → 不入库 + 产物自动清理（既有机制）
   t.step("设备连通性")
   ok = bool(t.screen_text() is not None)
   t.record("PASS" if ok else "FAIL", "dump UI 树成功")
   t.step("截图链路")   # screen_text 只验证 dump，截图是独立链路（评审结论⑨）
   shot = t.screenshot("smoke")
   t.record("PASS" if (os.path.isfile(shot) and os.path.getsize(shot) > 1024)
            else "FAIL", f"截图落盘成功: {shot}")
   t.finish()   # 报告生成后被探针清理逻辑删除，零残留
   ```
   覆盖链路：adb 连接 → u2 dump → 截图 → 报告 → （可选）视觉配置存在性提示。
2. `setup.sh` 末尾提示（不自动跑，设备可能未授权）：
   `echo "  建议执行: $WORKSPACE/.venv/bin/python $HERE/framework/smoke.py"`；
   `setup.ps1` 加 `-Smoke` 开关自动执行。
3. 退出码沿用框架语义：0=全链路通；3=设备/adb 问题（输出引导：
   `adb devices` 检查授权）。

**验收标准**

- [ ] 全新环境 setup 后 smoke 退出 0，且 DB 无残留记录、storage 无残留截图。
- [ ] 拔掉设备跑 smoke → 退出 3 + 可读引导文案。

### 3.4 Web UI 绑定地址防护 【S】

**实现细节**

1. `framework/webui.py` argparse 增加 `--host`（默认 `127.0.0.1`）；
   启动时：
   ```python
   if args.host not in ("127.0.0.1", "localhost", "::1"):
       print("⚠️" * 8 + "\n⚠️  Web UI 绑定到非回环地址，局域网内任何人可：\n"
             "⚠️  删除测试记录 / 查看并修改知识卡 / 保存视觉模型 API Key。\n"
             "⚠️  确认这是你的意图。无认证机制，切勿暴露到不可信网络。\n" + "⚠️" * 8)
   ```
2. `webui.ps1` / `webui.sh` 同步支持 `-Host` 透传（默认不变）。

**验收标准**

- [ ] `--host 0.0.0.0` 启动 stderr 出现警告块；默认启动无警告。
- [ ] 单测：告警判定函数对 `0.0.0.0`/内网 IP 返回 True，对回环返回 False。

---

## 阶段四：遗留小项（随做随清）

| 项 | 实现要点 | 验收 |
|---|---|---|
| `already_finished` 分支单测 | mock `importlib` 让 `mod.run()` 在 finish 后抛异常；断言 SystemExit code = `exit_code_for("PASS")` 而非 3 | 测试红→绿，覆盖 run_case.py:269-271 |
| 报告证据相对路径 | `finish()`/`_auto_screenshot` 入库路径改写 `os.path.relpath(path, storage根)`；`is_artifact_path` 与 Web UI 读取处同步 join；老数据绝对路径兼容（isabs 判断分流） | 报告迁移目录后证据仍可打开；旧记录不回归 |
| record() 结果类型枚举 | `test_framework.py` 顶部 `RESULT_TYPES = ("PASS","FAIL","WARN","INFO","BLOCKED")`；`record()` 未知值抛 `ValueError`（比 ❓ 兜底更早暴露拼写错误）。**实施前置（评审结论⑩）**：先 grep 存量审计——已执行（2026-09-09）：cases/ 下 207 处调用全部为标准值或 `"PASS" if x else "FAIL"` 条件表达式，**无兼容风险**；后续新增用例若引入非标值会在开发期即抛错 | 单测：`record("PASSS",...)` 抛 ValueError；存量用例 lint 全绿 |
| 结构化日志 | `run_case.py` main 里加 `logging.FileHandler(storage/logs/run_<ts>.log)`，print 保留控制台 | 每次执行 storage/logs 有完整日志 |
| VISION_CONF_FILE 惰性求值 | `vision.py:36` 改为函数内 `_vision_conf_path()`（webui.py:59 已有同款可抄） | 单测：import 后改环境变量仍生效 |
| 学习词表读取缓存 | `_dialog_words` 加 `(mtime, words)` 缓存，mtime 没变不重读 | 单测：改文件后缓存失效 |
| 报告文件名清洗 | `finish()` 对 `self.name` 套用 `_auto_screenshot` 同款 `re.sub(r'[\\/:*?"<>|]',"_",...)` | 单测：含 `:` 的用例名能正常出报告 |
| Windows 单测乱码 | README/文档单测命令前加 `$env:PYTHONUTF8="1"` 提示 | 文档命中即完成 |

---

## 实施顺序与依赖

```
阶段一：1.2 套件 runner (L) → 1.1 flakiness (M) → 1.3 环境漂移 (M)
   1.2 先行（评审结论④）：flakiness 视图的价值依赖批量执行数据，
   suite runner 跑几轮自然积累数据，1.1 做好即有东西可看；
   历史手动执行数据让 1.1 提前做也有意义，但作为面向未来的数据源，
   runner 是地基。纯增量，不动现有契约；
   1.2 依赖 run_case 暴露 --device（小改，需提案）
        ↓
阶段三：3.1 依赖钉版 (S) → 3.2 VERSION+CI (S-M) → 3.3 smoke (S) → 3.4 host 防护 (S)
   互相独立，可并行；3.2 的 CI 是 2.1/2.2 的承载体，先于阶段二完成
        ↓
阶段二：2.2 预算门禁 (S) → 2.3 新鲜度 (S-M) → 2.1 eval 集 (L)
   2.1 最重放最后；2.1/2.2 落地即挂进 3.2 的 CI
        ↓
阶段四：穿插在各阶段顺手清
```

**里程碑建议**
- M1（1.2 + 1.1 完成）：`run_suite.py` 跑过 ≥2 轮全量，Web UI 通过率视图有真实数据
- M2（阶段三完成）：CI 绿 + requirements 钉版 + smoke 进 setup
- M3（阶段二完成）：lint/预算门禁挂 CI = A+ 达成

## 不做清单（明确排除，防范围蔓延）

- 自研录制回放工具（probe/trace 机制已覆盖该价值）
- 跨平台（iOS）扩展（架构预留了分层，不在本期目标）
- 视觉模型自训练/微调（路由层已支持切换，模型选型是运维问题）
- Web UI 完整认证体系（回环绑定 + 警告已覆盖单机场景的风险；
  真有多人协作需求时另立项）

---

## 评审修订记录

- **2026-09-09 计划评审（10 条全部采纳）**：
  ① 1.3 快照时机定案为「__init__ + lock_portrait 内刷新」两次取基线，
  rotation 纳入默认监控（原方案 B 的改良版）；
  ② 2.1 lint 的 tap_xy AST 检查覆盖关键字参数、表达式参数放行；
  ③ 1.2 runner 增加设备断连熔断（子进程 ERROR + adb devices 复查）；
  ④ 阶段一顺序调整为 1.2 → 1.1（先有数据源再有视图）；
  ⑤ 动态事实守门收窄到 toast 断言 + <4 字跳过 + 描述前缀白名单；
  ⑥ CI 无设备约束显式声明，真机验收全部本地执行；
  ⑦ flaky 通过口径定案 = PASS+WARN；
  ⑧ SKILL.md 预算 260 → 280（当前 212 行会踩 80% 警告线）；
  ⑨ smoke 补截图链路验证；
  ⑩ record() 枚举前置存量审计——已执行 grep（207 处全为标准值/条件表达式，无兼容风险）。
