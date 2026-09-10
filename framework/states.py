#!/usr/bin/env python3
"""设备/系统状态检测：把"怎么判断处于某种状态"沉淀成方法。

设计原则
────────
这类东西是**可执行的确定性判断**，不是"经验知识"：
- 写成知识卡 → 每次要进 AI 上下文，占 token，还可能被猜错
- 写成代码   → 上下文成本为零，结果确定，还能直接当断言用

所以新增"怎么检测某个状态"时，优先加到这里，而不是堆进 _system.md。

用法
────
    from states import States
    s = States()                        # 不传则内部自建一个轻量 adb 执行器
    if s.is_vision_mode():              # 方法来自场景卡（knowledge/scenarios/*.md）
        ...

    # 用例里（TestCase 已自带 .states 属性）:
    t = TestCase("xxx")
    t.assert_true(t.states.is_vision_mode(), "应处于无限工作台")

    # 列出全部可用检测（自描述，SKILL.md 不必重复维护）
    python states.py --list
    python states.py is_vision_mode

约定
────
- 每个 `is_*/` 方法返回 bool；取不到/不适用返回 False（不抛异常）。
- 原始值读取用 `raw_*` 方法，便于排查（比如打印实际值）。
- 新增方法时用 @state 装饰器登记，触发词用于 AI 按场景检索。
"""
import os
import re
import subprocess
import sys

# ── 状态注册表：装饰器自动登记，供 --list 输出与 AI 检索 ──────────────
_REGISTRY = []


def state(name, desc, triggers=(), vendor=None):
    """登记一个状态检测方法。

    name     方法名
    desc     一句话说明（给人和 AI 看）
    triggers 触发词，AI 用 grep 检索场景时会用到
    vendor   适用厂商/机型；None 表示通用
    """
    def deco(fn):
        fn._state_meta = {
            "name": name, "desc": desc,
            "triggers": list(triggers), "vendor": vendor,
        }
        _REGISTRY.append(fn._state_meta)
        return fn
    return deco


class _Adb:
    """极简 adb 执行器：不依赖 uiautomator2，单条命令即用即走。"""

    def __init__(self, serial=None):
        self.serial = serial

    def shell(self, *args):
        cmd = ["adb"]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += ["shell", *args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return r.stdout.strip()
        except Exception:
            return ""


def _parse_cmd(cmd):
    """把场景卡里的「判定命令」解析成可执行的 adb shell 参数。

    支持两种写法：
      adb shell settings get system zui_ov_desktop_mode
      settings get system zui_ov_desktop_mode
    返回 ["settings","get","system","zui_ov_desktop_mode"] 或 None。
    """
    if not cmd:
        return None
    t = str(cmd).strip()
    if not t:
        return None
    toks = t.split()
    # 去掉前导 adb / adb shell（场景卡里常写成完整命令）
    while toks and toks[0] in ("adb", "shell"):
        toks.pop(0)
    return toks or None


class States:
    """系统/设备状态检测集合。

    所有方法都只做"读取判断"，不修改设备状态（开关类操作请放到
    TestCase 或场景卡里，避免这里产生副作用）。

    两种状态来源（无需二选一，混用即可）：
      1. 手写方法：@state 装饰，适合需要特殊逻辑的判定
      2. 场景卡自动注册：knowledge/scenarios/*.md 里写了「判定命令」的，
         在类创建后由 load_scenarios() 自动注册为同名方法

    第 2 种的意义：新建场景卡时只改 md 就够了，不必再改 states.py —— 
    两处都要改迟早会漏，漏了就是 AttributeError 让用例直接崩。
    """

    def __init__(self, adb=None, serial=None):
        self.adb = adb or _Adb(serial)

    # ── 内部工具 ──────────────────────────────────────────────────
    @staticmethod
    def _truthy(v):
        """settings get 的返回值归一化为 bool。

        '1'/'true'/'on'/'yes' → True；空值 / 'null' / '0' / 'false' → False。
        注意：settings get 查不到时会返回 'null'（字符串），不能当成 True。
        """
        if v is None:
            return False
        s = str(v).strip().lower()
        if s in ("", "null", "none", "0", "false", "off", "no"):
            return False
        return s in ("1", "true", "on", "yes") or s.isdigit() and int(s) > 0

    def settings_get(self, scope, key):
        return self.adb.shell("settings", "get", scope, key)

    def getprop(self, name):
        return self.adb.shell("getprop", name)

    # ── 状态检测 ──────────────────────────────────────────────────
    # App/机型相关的状态一律不在框架手写：在 knowledge/scenarios/*.md
    # 场景卡里写「判定命令」，load_scenarios() 会自动注册成同名方法
    # （例如 sys.无限工作台.md → states.is_vision_mode()）。

    # 只有跨 App 通用的判定才加到这里：
    # @state("is_xxx", "...", triggers=[...])
    # def is_xxx(self): ...

    # ── 环境漂移检测 ────────────────────────────────────────────────
    # 用例执行过程中可能修改设备设置（旋转、勿扰、亮度……），
    # 如果收尾时没还原，下一个用例的基线就被污染 → 连锁 FAIL。
    # env_snapshot() 取基线 → finish() 前 env_diff() 比对 → 非空记 WARN。

    # 漂移检测关注的设置项（key → adb 读取方式）
    # 不包含 foreground_package（前台包变化是测试目的，不是污染）
    # 不包含 screen_brightness（自动亮度机型有波动误报风险）
    _ENV_KEYS = [
        ("accelerometer_rotation", ("settings", "get", "system", "accelerometer_rotation")),
        ("user_rotation",          ("settings", "get", "system", "user_rotation")),
        ("stay_on_while_plugged_in", ("settings", "get", "global", "stay_on_while_plugged_in")),
        ("zen_mode",               ("settings", "get", "global", "zen_mode")),
    ]

    def env_snapshot(self):
        """取设备环境快照（dict），用于漂移检测基线。

        包含 accelerometer_rotation / user_rotation /
        stay_on_while_plugged_in / zen_mode。
        """
        snap = {}
        for key, cmd in self._ENV_KEYS:
            snap[key] = self.adb.shell(*cmd)
        return snap

    def env_diff(self, before, ignore=()):
        """与基线比对，返回 {键: (前, 后)} 的差异子集。

        ignore: 豁免的键集合（如 ('user_rotation',)），不计入差异。
        """
        after = self.env_snapshot()
        diff = {}
        for k in before:
            if k in ignore:
                continue
            if before.get(k) != after.get(k):
                diff[k] = (before.get(k), after.get(k))
        return diff

    # ── 未定义方法的兜底 ──────────────────────────────────────────
    def __getattr__(self, name):
        """调用不存在的方法时，给一条能直接照做的报错，而不是裸 AttributeError。

        场景卡写了 `states.is_xxx()` 但没实现时，默认报错只有一个名字，
        看不出是"拼错了"还是"忘了实现"，也不知道该去哪补。
        """
        raise AttributeError(
            f"States 没有状态方法 {name!r}。\n"
            f"  可用: {', '.join(m['name'] for m in _REGISTRY) or '（无）'}\n"
            f"  补法二选一：\n"
            f"    1) 在 framework/states.py 加 @state 方法\n"
            f"    2) 在 knowledge/scenarios/*.md 写「判定命令」，会自动注册同名方法"
        )


# ── 场景卡自动注册 ────────────────────────────────────────────────────
def _scenarios_dirs():
    """所有可能的 knowledge/scenarios/ 位置，按优先级返回。

    不能只按 states.py 的位置算：工作区里的 states.py 会去找
    <工作区>/knowledge/scenarios/，但 sync_skill.ps1 刻意不同步 knowledge/
    （运行时数据留在工作区），那个目录根本不存在 —— 结果场景卡全部注册不上，
    又退化成 AttributeError 崩用例。

    所以：优先工作区（本地改的卡先生效），回退 skill 包。
    也支持显式环境变量，方便自测。
    """
    env = os.environ.get("DSH_SCENARIOS_DIR")
    if env:
        return [env]
    here = os.path.dirname(os.path.abspath(__file__))
    pkg = os.path.dirname(here)                    # skill 包根（framework 的上一级）

    # 工作区：环境变量是唯一可靠来源。不能从 here 推导 —— 工作区里的 states.py
    # 算出来的"包根"就是工作区自己，两个候选会撞成同一个不存在的目录
    # （sync 不同步 knowledge/，工作区那个目录本来就没有）。
    ws = os.environ.get("DSH_WORKSPACE_DIR")

    # skill 包：同理不能只靠 pkg —— 工作区副本会指到自己。
    # 锚点优先级：显式环境变量 DSH_SKILL_DIR（run_case.ps1/webui.ps1 会设置，
    # 也支持自定义安装位置）> 默认安装位置（~/.agents/skills/android-test-skills）。
    # 环境变量是用户显式声明的意图，必须排在硬编码默认值前面。
    anchors = []
    env_pkg = os.environ.get("DSH_SKILL_DIR")
    if env_pkg:
        anchors.append(os.path.join(env_pkg, "knowledge", "scenarios"))
    home = os.path.expanduser("~")
    if home and home != "~":
        anchors.append(os.path.join(home, ".agents", "skills", "android-test-skills",
                                    "knowledge", "scenarios"))
    anchors.append(os.path.join(pkg, "knowledge", "scenarios"))

    cands = []
    if ws:
        cands.append(os.path.join(ws, "knowledge", "scenarios"))
    cands.extend(anchors)
    # 去重保序
    seen, out = set(), []
    for c in cands:
        k = os.path.normcase(os.path.abspath(c))
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _parse_simple_card(text):
    """极简知识卡解析器：只认「键: 值」与「- 列表项」两种行。

    知识卡已从 YAML 迁移为 MD（2026-09）：md 文件头部的键值区仍用
    这种朴素写法，由本函数解析 —— 不再依赖 pyyaml：
    - pyyaml 的缩进/引号/重复键规则对维护者是纯负担（曾两次炸卡）
    - 场景卡需要的结构只有两层：标量字段、字符串列表

    规则：
    - 「键: 值」          → 标量
    - 「键:」后跟 - 项目  → 列表（连续的 "- " 行）
    - 遇到 MD 标题（#）、HTML 注释、或新的「键:」→ 当前键的解析结束

    返回 dict：标量 → str；列表 → list[str]。
    """
    data = {}
    cur_key = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        # MD 标题 / HTML 注释：键值区结束（兜底卡正文里的标题不该被吸进任何键）
        if stripped.startswith("#") or stripped.startswith("<!--"):
            cur_key = None
            continue
        # 列表项：只归当前键
        if stripped.startswith("- "):
            if cur_key is not None:
                data.setdefault(cur_key, [])
                if isinstance(data[cur_key], list):
                    data[cur_key].append(stripped[2:].strip())
            continue
        # 键: 值
        m = re.match(r"^([^:#]+?)\s*:\s*(.*)$", stripped)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            cur_key = key
            if val:
                data[key] = val
            else:
                data.setdefault(key, [])
                if not isinstance(data[key], list):
                    data[key] = []
        # 其它散行直接忽略（正文散文不参与机器解析）
    return data


def _extract_method(d):
    """从场景卡里取出要注册的方法名。

    优先看「判定方法」（形如 states.is_xxx()）；没写则由场景名推导 is_xxx。
    """
    m = str(d.get("判定方法") or "").strip()
    mm = re.search(r"(?:states\.)?(is_\w+)\s*\(?", m)
    if mm:
        return mm.group(1)
    return None


def load_scenarios():
    """扫描 knowledge/scenarios/*.md，把带「判定命令」的卡自动注册成方法。

    这样新建场景卡只改 md 即可，不必同步改 states.py —— 两处都要改迟早会漏，
    漏了就是 AttributeError 让用例直接崩（而不是报 FAIL）。

    卡片格式（2026-09 起为 MD + 朴素键值头，不再是 YAML）：
      键: 值（单行标量）
      键:（后跟 "- 项目" 列表）
    解析用 _parse_simple_card，零第三方依赖。

    扫描 _scenarios_dirs() 里的每个目录（工作区优先，回退 skill 包），
    同名方法以先扫到的为准，后面目录里的不覆盖（保证工作区能改 skill 包的卡）。

    手写方法优先：同名的 @state 方法不会被覆盖。
    """
    added = []
    dirs = [d for d in _scenarios_dirs() if os.path.isdir(d)]
    if not dirs:
        return []
    for d in dirs:
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md"):
                continue
            fp = os.path.join(d, fn)
            try:
                with open(fp, encoding="utf-8") as f:
                    data = _parse_simple_card(f.read())
            except Exception:
                continue                   # 坏卡跳过，不影响其它卡
            if not isinstance(data, dict):
                continue
            name = _extract_method(data)
            cmd = _parse_cmd(data.get("判定命令"))
            if not name or not cmd:
                continue                   # 没写判定命令 → 这个场景没法自动判定
            if hasattr(States, name):
                continue                   # 手写方法优先，不覆盖
            desc = str(data.get("场景") or fn)
            note = str(data.get("判定说明") or "").strip()
            triggers = [str(t) for t in (data.get("触发词") or []) if t]

            # 闭包必须绑定当次的 cmd/name，否则循环结束后全指向最后一张卡
            def make(name=name, cmd=cmd):
                def raw(self):
                    return self.adb.shell(*cmd)
                raw.__name__ = "raw_" + name[3:]
                raw.__doc__ = f"{name} 的原始值（由场景卡自动生成，命令: {' '.join(cmd)}）"

                def check(self):
                    return self._truthy(raw(self))
                check.__name__ = name
                check.__doc__ = f"是否处于「{desc}」{('—— ' + note) if note else ''}"
                return check, raw

            check, raw = make()
            setattr(States, name, check)
            setattr(States, raw.__name__, raw)
            _REGISTRY.append({
                "name": name, "desc": check.__doc__,
                "triggers": triggers, "vendor": "from: " + fn,
            })
            added.append(name)
    return added


# 模块导入时即注册，保证 t.states.is_xxx() 开箱可用
load_scenarios()


# ── 命令行自描述 ──────────────────────────────────────────────────────
def list_states():
    if not _REGISTRY:
        print("（暂无登记的状态检测）")
        return
    print("可用状态检测：\n")
    for m in _REGISTRY:
        vendor = m["vendor"] or "通用"
        trig = "、".join(m["triggers"]) if m["triggers"] else "-"
        print("  %-22s %s" % (m["name"] + "()", m["desc"]))
        print("  %-22s 适用=%s  触发词=%s" % ("", vendor, trig))
    print("\n用法: python states.py <方法名>")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--list", "-l", "list"):
        list_states()
    elif len(sys.argv) > 1:
        fn = sys.argv[1]
        s = States()
        if not hasattr(s, fn) or fn.startswith("_"):
            print("未知状态方法: %s" % fn)
            print("可用: %s" % ", ".join(m["name"] for m in _REGISTRY))
            sys.exit(1)
        print("%s -> %s" % (fn, getattr(s, fn)()))
    else:
        list_states()
