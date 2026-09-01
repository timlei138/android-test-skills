#!/usr/bin/env python3
"""用例执行器：python run_case.py 联想日历_174.py

用例查找兼容两种布局：
  1) <framework>/cases/<用例>     （framework 内的 cases）
  2) <workspace>/cases/<用例>     （与 framework 平级的 cases，setup.sh 默认布局）
传入绝对路径时直接使用该路径。
"""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIRS = [
    os.path.join(HERE, "cases"),
    os.path.join(os.path.dirname(HERE), "cases"),
]

# 用例内 `from test_framework import ...` 依赖 test_framework.py 所在目录
# （framework/），无论从哪个 cwd 启动执行器都把它放进 sys.path。
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def resolve_case(name: str) -> str | None:
    """把用例名解析为实际文件路径；找不到返回 None。"""
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    for d in CASE_DIRS:
        candidate = os.path.join(d, name)
        if os.path.exists(candidate):
            return candidate
    return None


def main():
    if len(sys.argv) < 2:
        print("用法: python run_case.py <用例文件名>")
        sys.exit(1)
    name = sys.argv[1]
    path = resolve_case(name)
    if path is None:
        print(f"用例文件不存在: {name}")
        for d in CASE_DIRS:
            print(f"  已查找: {d}/")
        sys.exit(1)

    # 通过环境变量把「用户原始输入 + 用例脚本路径」传给 TestCase 入库
    # （TestCase.__init__ 读取；AI 生成用例时把用户口述写进 USER_INPUT 常量）
    os.environ["DSH_CASE_SCRIPT_PATH"] = path
    user_input = extract_user_input(path)
    if user_input is not None:
        os.environ["DSH_CASE_USER_INPUT"] = user_input

    spec = importlib.util.spec_from_file_location("testcase", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if hasattr(mod, "run"):
        report = mod.run()
        print(f"\n🎉 用例执行完成，报告: {report}")
    else:
        print("用例文件需要定义 run() 函数")


def extract_user_input(path: str) -> str | None:
    """从用例脚本源码提取 USER_INPUT 常量（AI 生成用例时写入用户原始描述）。

    支持两种写法：
      USER_INPUT = "..."            （单行）
      USER_INPUT = 三引号字符串     （多行，三重双引号）
    找不到返回 None。
    """
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return None
    m = re.search(r'USER_INPUT\s*=\s*("""(.*?)"""|\'(.*?)\'|"(.*?)")', source, re.S)
    if not m:
        return None
    return (m.group(2) or m.group(3) or m.group(4) or "").strip() or None


if __name__ == "__main__":
    main()
