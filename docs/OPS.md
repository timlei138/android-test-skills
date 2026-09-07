# OPS 运维手册（维护者向）

> 本文件从 SKILL.md 拆出，**不随每次会话进 Agent 上下文**（省 token）。
> 受众：维护者/运维。Agent 只在用户提出「Web UI 为什么不对 / 重建报告 /
> 回填包名 / 配置视觉模型 / 查测试记录」等运维问题时检索本文件。
> 执行测试所需的行为契约全在 SKILL.md；本文件是同一约定的完整版与论证
> （SKILL.md 只留结论与指针，个别表格两边各有一份，改动时同步）。

## Windows 脚本参数与细节

Windows 脚本参数：
- `setup.ps1 [-Workspace <路径>] [-Python <解释器>] [-SkipDeviceCheck] [-Recreate] [-WithAgent]`
- `run_case.ps1 -Case <用例> [-List] [-Workspace <路径>]`
- `webui.ps1 <start|stop|status|restart> [-Port 8900]`

Windows 脚本为 **PowerShell 5.1 兼容 + UTF-8 with BOM**；已内置处理两项 Windows 特有问题：
原生命令 stderr 日志不触发 `NativeCommandError` 中断、强制 `PYTHONUTF8=1` 避免 GBK 编码 emoji 崩溃。
默认工作区 `~/dsh-android-test`（Windows 下为 `C:\Users\<你>\dsh-android-test`）。

## 测试记录查询（SQLite）

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

## 代码与数据分开放（skill 包 / 工作区）

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

## Web UI 排障

### Web UI 改了却没生效 —— 按顺序查这两条

1. **PAGE 缓存**：`webui.py` 把 HTML/JS/CSS 缓存在内存里，同端口上的旧进程不杀掉，就永远返回旧文件。  
   → 症状："文件明明改了，浏览器刷新还是老样子"。**先杀进程再重启**，别只刷新页面。
2. **进程跑在另一份副本上**：机器上可能同时存在多个 skill 包副本，进程命令行指向的那个才是真正生效的目录，改别的副本一律白改。  
   → 症状："重启了还是不对"。排查：查出监听端口的 PID，再看它的命令行指向哪个 skill 包路径（PID → 命令行 → 目录）。  
   → 实测踩过：进程跑在旧副本上，而我一直改活副本，浪费一轮排查。确认后统一到活副本，旧副本要么删除要么反向同步。

> 排查端口占用 / 进程命令行用系统自带命令即可（`netstat -ano | findstr :<port>` 拿 PID，再用 PID 查命令行）。  
> 注意：`wmic.exe` 在本机被安全策略屏蔽，**不要**用它，改用 PowerShell 的 CIM 查询。

### Web UI 删除失败（SHFileOperationW 0x2 / Failed to fetch）

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

## 报告重建

### 报告文件丢了 / 内容对不上 —— 可以直接重建

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

### 被测 App（包名）入库

报告头部的「被测 App」曾**只写进文件**，报告一丢就永久没了 —— 所以它现在进 `cases.package` 列：

- `finish()` 的包名取值优先级：**脚本路径目录名**（`cases/<包名>/xx.py`，目录名长得像包名才认）
  > 收尾前台包 —— 用例常停在 PhotoPicker 等系统页收尾，前台包可能根本不是被测 App
  （168 实测曾把 com.zui.calendar 报成 com.android.providers.media.module）。
  取不到就留空，**不覆盖已有值**
- 重建报告时渲染顺序：`package` → `script_path` → `unknown`
- 老记录（加列之前跑的）用 `db.backfill_package()` 回填：

```python
db.backfill_package(dry_run=True)   # 先看能补哪些
db.backfill_package()               # 正式写库（幂等，已有值的不动）
```

回填只认 `cases/<包名>/<脚本>.py` 这种结构、且目录名长得像包名（纯 ASCII、含点、无空白）。
历史旧路径（如 `cases/联想日历_168.py`——早期用例直接放 cases/ 根下用中文名，现磁盘已迁入
`cases/com.zui.calendar/`）提取不出包名，**一律跳过，不猜**。

## 视觉模型配置

- 入口：Web UI 左侧 **「视觉模型」**（`/#view-vision`）
- 可配 `Base URL` / `Model` / `API Key`，「测试连接」会发最小 chat 请求自检
- 保存位置：**工作区** `storage/vision.json`（权限 600）。**不进 skill 包**，`sync_skill.ps1` 不同步 `storage/`，所以分享 skill 包不会泄露密钥
- API Key 只回显掩码（前 4 后 4，中间打码），GET 接口不返回明文；**保存时留空 = 保留原值**（防止只想改 model 却误清空密钥）
- 运行时优先级：`DEEPSEEK_API_KEY` 环境变量 > 本页配置 > `~/.dsh/.credentials.yaml`
- 跨平台：路径统一由 `db.default_test_dir()` 解析（Windows `~/dsh-android-test`、Linux/macOS 同逻辑），无平台分支代码
