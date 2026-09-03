#!/usr/bin/env python3
"""用例执行器：python run_case.py com.zui.calendar/172.py

用例目录单一数据源 = <skill包>/cases（与 knowledge 平级，随版本同步团队共享）。
用例按被测 App 包名分目录存放：cases/<包名>/<编号>.py，
与该 App 的知识卡 knowledge/<包名>.md、探查缓存 storage/probes/<包名>/ 同键。

解析顺序：
  1) 绝对路径                        直接使用
  2) 带子目录的相对路径               cases/com.zui.calendar/172.py
  3) 裸文件名递归精确匹配             172.py
  4) 子串模糊匹配（唯一命中才接受）    172
查找目录：
  1) <skill包>/cases/          （单一数据源，递归）
  2) <framework>/cases/        （旧布局兼容，递归）
环境变量 DSH_ANDROID_TEST_CASES 可覆盖用例目录。
"""
import ast
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _case_dirs():
    """用例查找目录，按优先级返回。

    单一数据源 = skill 包 cases/。本文件有两份拷贝（skill 包 / 工作区），
    从工作区副本运行时 ./cases 不存在，所以末尾加 skill 包锚点兜底
    （与 states.py 找 scenarios 的锚点逻辑同理）。
    环境变量 DSH_ANDROID_TEST_CASES 可覆盖（优先级最高）。
    """
    dirs = []
    env = os.environ.get("DSH_ANDROID_TEST_CASES")
    if env and env.strip():
        dirs.append(os.path.abspath(os.path.expanduser(env.strip())))
    dirs += [
        os.path.join(os.path.dirname(HERE), "cases"),   # 本拷贝所属包根
        os.path.join(HERE, "cases"),                    # 旧布局兼容
    ]
    skill_root = os.environ.get("DSH_SKILL_DIR") or os.path.join(
        os.path.expanduser("~"), ".agents", "skills", "android-gui-testing")
    dirs.append(os.path.join(skill_root, "cases"))      # skill 包锚点
    seen, out = set(), []
    for d in dirs:
        k = os.path.normcase(os.path.abspath(d))
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out


CASE_DIRS = _case_dirs()

# 用例内 `from test_framework import ...` 依赖 test_framework.py 所在目录
# （framework/），无论从哪个 cwd 启动执行器都把它放进 sys.path。
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _iter_case_files():
    """递归产出所有用例文件（绝对路径）。排除 _ 开头的共享模块（_flow.py 等）。"""
    for d in CASE_DIRS:
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs
                       if x not in ("__pycache__",) and not x.startswith(".")]
            for f in files:
                if f.endswith(".py") and not f.startswith("_"):
                    yield os.path.join(root, f)


def resolve_case(name: str) -> str | None:
    """把用例名解析为实际文件路径；找不到返回 None。

    支持：绝对路径 / 带子目录的相对路径 / 裸文件名 / 唯一子串模糊匹配。
    裸名或模糊匹配命中多个时打印候选并退出（由调用方处理 None）。"""
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    n = name if name.lower().endswith(".py") else name + ".py"
    # 带子目录的相对路径（com.zui.calendar/172.py，正反斜杠均可）
    if "/" in name or "\\" in name:
        rel = name.replace("/", os.sep).replace("\\", os.sep)
        if not rel.lower().endswith(".py"):
            rel += ".py"
        for d in CASE_DIRS:
            candidate = os.path.join(d, rel)
            if os.path.isfile(candidate):
                return candidate
        return None
    # 裸文件名：递归精确匹配
    hits = [p for p in _iter_case_files() if os.path.basename(p) == n]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"用例名 '{name}' 匹配到多个文件，请写带子目录的路径:")
        for p in hits:
            print(f"  {p}")
        return None
    # 子串模糊匹配（唯一命中才接受）
    hits = [p for p in _iter_case_files() if n[:-3] in os.path.basename(p)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"用例名 '{name}' 模糊匹配到多个文件，请写更完整的名字:")
        for p in hits:
            print(f"  {p}")
    return None


# setup 会把 framework/ 复制到工作区，改动靠 sync_skill.ps1 双向搬运；
# 忘了同步就会出现"改的代码不生效"。启动时对比两份拷贝的关键文件哈希。
_DRIFT_KEY_FILES = ("test_framework.py", "states.py", "run_case.py", "db.py",
                    "vision.py", "ocr_screen.py", "webui.py")


def _file_digest(path):
    import hashlib
    h = hashlib.md5()  # 只做一致性对比，非安全用途
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def warn_if_framework_drift():
    """工作区 framework 副本与 skill 包不一致时醒目告警；任一侧缺失则跳过。"""
    try:
        from db import default_test_dir
        ws_fw = os.path.join(default_test_dir(), "framework")
    except Exception:
        return
    skill_root = os.environ.get("DSH_SKILL_DIR") or os.path.join(
        os.path.expanduser("~"), ".agents", "skills", "android-gui-testing")
    skill_fw = os.path.join(skill_root, "framework")
    if not (os.path.isdir(ws_fw) and os.path.isdir(skill_fw)):
        return
    if os.path.normcase(os.path.abspath(ws_fw)) == os.path.normcase(os.path.abspath(skill_fw)):
        return
    diffs = []
    for f in _DRIFT_KEY_FILES:
        a, b = os.path.join(skill_fw, f), os.path.join(ws_fw, f)
        if not (os.path.isfile(a) and os.path.isfile(b)):
            diffs.append(f + "（单侧缺失）")
        elif _file_digest(a) != _file_digest(b):
            diffs.append(f)
    if diffs:
        print("⚠️ " * 8)
        print(f"⚠️  工作区 framework 与 skill 包不一致: {', '.join(diffs)}")
        print(f"⚠️  skill包: {skill_fw}")
        print(f"⚠️  工作区:  {ws_fw}")
        print("⚠️  请运行 scripts/sync_skill.ps1 同步（-ToSkill 为工作区→skill包），")
        print("⚠️  否则你改的代码可能不是正在跑的代码。")
        print("⚠️ " * 8)


# 用例最终结论 → 进程退出码（CI/其他 Agent 据此机器消费结果）
EXIT_CODES = {"PASS": 0, "WARN": 0, "FAIL": 1, "BLOCKED": 2, "ERROR": 3}


def exit_code_for(status):
    """最终结论映射退出码；未知/缺失一律按 ERROR(3)，绝不默认 0 放行。"""
    return EXIT_CODES.get(status, 3)


def _last_case():
    """本进程内最近一次 finish() 的 TestCase（test_framework.LAST_CASE）。"""
    tf = sys.modules.get("test_framework")
    return getattr(tf, "LAST_CASE", None) if tf else None


def main():
    if len(sys.argv) < 2:
        print("用法: python run_case.py <用例文件名或 com.zui.calendar/172.py>")
        sys.exit(3)
    try:
        warn_if_framework_drift()
    except Exception:
        pass                        # 检测失败不影响用例执行
    name = sys.argv[1]
    path = resolve_case(name)
    if path is None:
        print(f"用例文件不存在或匹配不唯一: {name}")
        for d in CASE_DIRS:
            print(f"  已查找: {d}/")
        sys.exit(3)

    # 输出最终生效的关键路径（排查"跑的代码/用例不是我以为的那份"）
    try:
        from db import default_test_dir
        print(f"[路径] 工作区: {default_test_dir()}")
        print(f"[路径] 用例目录: {[d for d in CASE_DIRS if os.path.isdir(d)]}")
        print(f"[路径] 用例脚本: {path}")
    except Exception:
        pass

    # 通过环境变量把「用户原始输入 + 用例脚本路径」传给 TestCase 入库
    # （TestCase.__init__ 读取；AI 生成用例时把用户口述写进 USER_INPUT 常量）
    os.environ["DSH_CASE_SCRIPT_PATH"] = path
    user_input = extract_user_input(path)
    if user_input is not None:
        os.environ["DSH_CASE_USER_INPUT"] = user_input

    spec = importlib.util.spec_from_file_location("testcase", path)
    mod = importlib.util.module_from_spec(spec)
    import traceback
    try:
        spec.loader.exec_module(mod)
        if not hasattr(mod, "run"):
            print("用例文件需要定义 run() 函数")
            sys.exit(3)
        report = mod.run()
    except KeyboardInterrupt:
        raise
    except Exception as e:
        # 脚本/框架/设备异常：留完整堆栈，尽量生成已有证据的报告
        traceback.print_exc()
        tc = _last_case()
        if tc is not None:
            try:
                tc.finish()
            except Exception:
                pass
        from test_framework import CaseAbort
        code = 1 if isinstance(e, CaseAbort) else 3
        print(f"\n💥 用例异常终止（{'必需操作失败' if code == 1 else '执行异常'}），"
              f"退出码 {code}")
        sys.exit(code)

    status = getattr(_last_case(), "final_status", None)
    code = exit_code_for(status)
    if status is None:
        print("\n⚠️ 用例未调用 t.finish()，无最终结论，按 ERROR 处理")
    print(f"\n🎉 用例执行完成，报告: {report}（最终结论: {status}，退出码 {code}）")
    sys.exit(code)


def extract_user_input_from_source(source: str) -> str | None:
    """从用例源码提取 USER_INPUT 字符串常量（ast 解析，不吃引号/转义陷阱）。

    只认模块顶层的 `USER_INPUT = <字符串字面量>`；找不到或源码无法解析返回 None。
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "USER_INPUT"
                   for t in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value.strip() or None
    return None


def extract_user_input(path: str) -> str | None:
    """从用例脚本文件提取 USER_INPUT 常量（AI 生成用例时写入用户原始描述）。"""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return None
    return extract_user_input_from_source(source)


if __name__ == "__main__":
    main()
