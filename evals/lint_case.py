#!/usr/bin/env python3
"""用例脚本静态 lint：AST 解析，不执行。

检查规则
────────
| # | 规则                        | 级别   | 检查方式                          |
|---|-----------------------------|--------|----------------------------------|
| 1 | 必须含 USER_INPUT 字符串常量 | ERROR  | ast 找 Assign(targets=Name('USER_INPUT')) |
| 2 | 禁止裸坐标 tap_xy            | ERROR  | ast 找 Call(func=Name('tap_xy'))，参数全为常量字面量才违规 |
| 3 | 禁止无注释裸 time.sleep      | ERROR  | ast 找 sleep Call + 源码行注释检测       |
| 4 | tap 后无 if 判返回值          | HINT   | 启发：Expr(value=Call(tap_*)) 不在 If 内 |

退出码
------
- 违规（ERROR 级）→ 1（附行号）
- 仅提示（HINT 级）→ 0
- 好用例 → 0

用法
----
    python evals/lint_case.py cases/com.zui.calendar/172.py
    python evals/lint_case.py cases/ --baseline    # 基线模式：只检查指定文件
"""
import ast
import os
import sys


# ── 规则实现 ──────────────────────────────────────────────────────

class _LintVisitor(ast.NodeVisitor):
    """遍历 AST，收集违规与提示。"""

    def __init__(self, source_lines):
        self.source_lines = source_lines
        self.errors = []     # (line, rule, msg)
        self.hints = []      # (line, rule, msg)
        self._has_user_input = False
        self._in_if_body = False  # 是否在 if 语句体内
        self._in_tap_assign = False  # 是否在 tap_* 赋值语句中（ok = t.tap_...）
        self._func_has_docstring = False  # 当前函数是否有 docstring

    def visit_FunctionDef(self, node):
        """记录当前函数是否有 docstring，然后遍历子节点。"""
        old_doc = self._func_has_docstring
        has_doc = (node.body and isinstance(node.body[0], ast.Expr)
                   and isinstance(node.body[0].value, ast.Constant)
                   and isinstance(node.body[0].value.value, str))
        self._func_has_docstring = has_doc
        for child in node.body:
            self.visit(child)
        self._func_has_docstring = old_doc

    def visit_Assign(self, node):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "USER_INPUT":
                # 必须是字符串常量
                if isinstance(node.value, (ast.Constant,)) and isinstance(node.value.value, str):
                    self._has_user_input = True
        # tap_* 赋值：ok = t.tap_text(...) 形态，返回值已被捕获
        if isinstance(node.value, ast.Call):
            fn = self._call_name(node.value)
            if fn and fn.startswith("tap_"):
                old = self._in_tap_assign
                self._in_tap_assign = True
                self.generic_visit(node)
                self._in_tap_assign = old
                return
        self.generic_visit(node)

    def visit_Call(self, node):
        # ── 规则 2：裸坐标 tap_xy ──────────────────────────────────
        fn = self._call_name(node)
        if fn == "tap_xy":
            if self._all_args_literal(node):
                self.errors.append(
                    (node.lineno, "bare_tap_xy",
                     "禁止裸坐标 tap_xy（坐标应从元素 bounds 推导）"))

        # ── 规则 3：无注释裸 time.sleep ────────────────────────────
        # 豁免条件（任一成立即放行）：
        #   a) 同行或前一行有注释
        #   b) 所在函数有 docstring（函数级说明）
        #   c) sleep ≤ 3s（settle 型等待：操作后等动画/渲染稳定，
        #      与 case-writing.md「settle 注释」约定一致）
        if fn == "sleep" and not self._func_has_docstring:
            if not self._is_short_settle(node) and not self._line_has_comment(node.lineno):
                self.errors.append(
                    (node.lineno, "bare_sleep",
                     "time.sleep 必须有注释说明等待原因（或 ≤ 3s 的 settle 等待）"))

        # ── 规则 4：tap 后无 if 判返回值（提示级）────────────────
        if fn and fn.startswith("tap_") and fn != "tap_xy" and not self._in_if_body and not self._in_tap_assign:
            self.hints.append(
                (node.lineno, "tap_no_guard",
                 f"{fn}() 返回值未判断（建议 if not {fn}(...): 或 require_*）"))

        self.generic_visit(node)

    def visit_If(self, node):
        old = self._in_if_body
        self._in_if_body = True
        # 遍历 If 条件本身（检查 if t.tap_xy(...): 形态）
        self.visit(node.test)
        for child in node.body:
            self.visit(child)
        self._in_if_body = old
        for child in node.orelse:
            self.visit(child)

    def visit_Expr(self, node):
        # Expr 语句中的 Call 直接调用 generic_visit（不在 if 体内时已处理）
        self.generic_visit(node)

    # ── 辅助方法 ──────────────────────────────────────────────────
    @staticmethod
    def _call_name(node):
        """取 Call 节点的最简函数名（支持 t.tap_xy / time.sleep / sleep）。"""
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    @staticmethod
    def _all_args_literal(node):
        """位置参数与关键字参数全部为常量字面量 → True（裸坐标）。
        含变量/表达式/Attribute/BinOp → False（从元素推导，合法）。"""
        for arg in node.args:
            if not isinstance(arg, ast.Constant):
                return False
        for kw in node.keywords:
            if not isinstance(kw.value, ast.Constant):
                return False
        return True

    @staticmethod
    def _is_short_settle(node):
        """sleep ≤ 3s → settle 型等待，豁免。"""
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                return arg.value <= 3
        return False

    def _line_has_comment(self, lineno):
        """检查同行或前一行是否有注释（排除字符串内的 #）。"""
        for delta in (0, -1):
            idx = lineno - 1 + delta
            if 0 <= idx < len(self.source_lines):
                line = self.source_lines[idx]
                # 整行注释
                if line.lstrip().startswith("#"):
                    return True
                # 行尾注释：找 # 且其前无引号包裹
                in_str = None
                for i, ch in enumerate(line):
                    if ch in ('"', "'") and (i == 0 or line[i-1] != '\\'):
                        if in_str is None:
                            in_str = ch
                        elif ch == in_str:
                            in_str = None
                    elif ch == '#' and in_str is None:
                        return True
        return False


def lint_file(path):
    """lint 单个用例文件。返回 (errors, hints)。"""
    with open(path, encoding="utf-8") as f:
        source = f.read()
    source_lines = source.splitlines()

    errors = []
    hints = []

    # 规则 1：USER_INPUT
    visitor = _LintVisitor(source_lines)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        errors.append((e.lineno or 0, "syntax_error", f"语法错误: {e.msg}"))
        return errors, hints

    visitor.visit(tree)

    if not visitor._has_user_input:
        errors.append((1, "missing_user_input",
                       "缺少 USER_INPUT 字符串常量（用例原文）"))

    errors.extend(visitor.errors)
    hints.extend(visitor.hints)

    return errors, hints


# ── 入口 ──────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("用法: python evals/lint_case.py <文件或目录> [--baseline]")
        sys.exit(2)

    targets = sys.argv[1:-1] if "--baseline" in sys.argv else sys.argv[1:]
    baseline_mode = "--baseline" in sys.argv

    files = []
    for t in targets:
        if os.path.isfile(t):
            files.append(t)
        elif os.path.isdir(t):
            for root, dirs, fnames in os.walk(t):
                dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith("_")]
                for fn in fnames:
                    if fn.endswith(".py") and not fn.startswith("_"):
                        files.append(os.path.join(root, fn))

    total_errors = 0
    total_hints = 0
    for f in sorted(files):
        errors, hints = lint_file(f)
        rel = os.path.relpath(f)
        if errors:
            for line, rule, msg in errors:
                print(f"  ❌ {rel}:{line} [{rule}] {msg}")
            total_errors += len(errors)
        if hints:
            for line, rule, msg in hints:
                print(f"  💡 {rel}:{line} [{rule}] {msg}")
            total_hints += len(hints)
        if not errors and not hints:
            if baseline_mode:
                print(f"  ✅ {rel}")

    print(f"\n{len(files)} 个文件: {total_errors} 违规, {total_hints} 提示")
    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()
