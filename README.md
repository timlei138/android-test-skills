# android-gui-testing

Android 设备黑盒 GUI 测试技能包：**你给测试用例，它驱动设备执行，输出带证据的测试报告**。

## 5 分钟快速开始

```bash
# 1. 装环境（自动建 venv、装依赖、初始化设备端）
bash setup.sh

# 2. 跑示例用例
cd ~/dsh-android-test/framework
~/.dsh-android-test/.venv/bin/python run_case.py 联想日历_174.py
# → 自动生成 screenshots/reports/联想日历_174_报告.md

# 3. 写自己的用例（参考 cases/ 示例）
#    新建 cases/我的用例.py，用框架 API 表达步骤+断言，然后 run_case.py 执行
```

## 能力

| 能力 | 说明 |
|---|---|
| 元素操作 | 元素优先定位（resource-id/text/content-desc），坐标仅兜底 |
| 弹窗看门狗 | 后台线程检测权限/引导弹窗立即点击（allow/deny 策略可切换） |
| 权限测试 | 相机/图库允许+拒绝路径（拆分隔离避免 USER_FIXED 级联） |
| 断言 | 文本/开关/长度上限/置灰（像素对比度） |
| Canvas 读取 | RapidOCR 读自绘控件（滚轮/画布文字） |
| 滚轮操作 | 点按切换（零惯性，比滑动准） |
| 知识卡 | 按前台包名注入 App 操作经验（界面结构/高效操作/已知坑） |
| 结果分类 | PASS / FAIL / WARN / BLOCKED / INFO |
| 报告 | 自动生成 Markdown，含每步结果+截图证据 |

## 目录结构

```
android-gui-testing/
├── SKILL.md          # 给 AI 的玩法说明书
├── README.md         # 本文件
├── setup.sh          # 一键环境安装
├── framework/
│   ├── test_framework.py   # 测试框架
│   ├── run_case.py         # 用例执行器
│   ├── knowledge/          # App 知识卡（YAML）
│   ├── ocr_screen.py       # Canvas OCR 辅助
│   └── set_time_tap.py     # 滚轮点按控制器
└── cases/            # 示例用例
```

## 写用例模板

```python
from test_framework import TestCase

def run():
    t = TestCase("我的用例")
    t.step("步骤1")
    t.tap_text("开始")
    t.record("PASS", "点击成功")
    t.screenshot("步骤1证据")
    return t.finish()
```

## 添加自己的知识/用例（两种方式）

**方式 A（推荐，会说话就行）**：直接告诉 AI
> "记一下：课程表入口是 主页→更多→课程表"
> "测试前先 pm clear"
> "跑这个用例：……"

AI 会自动写进知识卡 / 生成用例脚本。

**方式 B（技术用户）**：复制模板改
- `framework/knowledge/_template.yaml` → 新 App 知识卡
- `cases/_template.py` → 新用例脚本

## 给 App 积累知识卡

用户口述的 App 操作经验记录到 `framework/knowledge/<包名>.yaml`：
```yaml
app: com.zui.calendar
导航入口:
  课程表: 主页 → 右上角"更多" → 弹窗"课程表"
已知坑:
  - Canvas 滚轮滑动会过冲，点按数字更准
```

## 环境要求

- 电脑：macOS/Linux + Python 3.9+ + Android SDK platform-tools (adb)
- 设备：Android 手机/平板，开启 USB 调试并授权
- 可选：Python 3.13 + AutoGLM 云端模型（`setup.sh --with-agent`）
