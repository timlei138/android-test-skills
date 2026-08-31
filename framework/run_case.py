#!/usr/bin/env python3
"""用例执行器：python run_case.py cases/联想日历_174.py"""
import importlib.util
import os
import sys

CASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")


def main():
    if len(sys.argv) < 2:
        print("用法: python run_case.py <用例文件名>")
        sys.exit(1)
    name = sys.argv[1]
    path = name if os.path.isabs(name) else os.path.join(CASES_DIR, name)
    if not os.path.exists(path):
        print(f"用例文件不存在: {path}")
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("testcase", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if hasattr(mod, "run"):
        report = mod.run()
        print(f"\n🎉 用例执行完成，报告: {report}")
    else:
        print("用例文件需要定义 run() 函数")


if __name__ == "__main__":
    main()
