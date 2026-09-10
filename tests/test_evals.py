#!/usr/bin/env python3
"""evals/ 静态 lint 与事实守门单测（不需要 Android 设备）。

覆盖：lint_case.py 四条规则、check_facts.py toast 断言词守门。

运行（skill 包根目录）：
    python -m unittest tests.test_evals -v
"""
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "evals"))

import lint_case       # noqa: E402
import check_facts     # noqa: E402


# ── lint_case.py：规则覆盖 ──────────────────────────────────────
class TestLintRules(unittest.TestCase):

    def _lint(self, source):
        """将源码写入临时文件再 lint。"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write(source)
            path = f.name
        try:
            return lint_case.lint_file(path)
        finally:
            os.unlink(path)

    def test_good_case_no_errors(self):
        """好用例：有 USER_INPUT、无裸坐标、sleep 有注释。"""
        src = '''
USER_INPUT = "测试步骤"
def run():
    t = TestCase("test")
    t.step("s1")
    t.tap_xy(cx, cy)  # 坐标从元素推导
    time.sleep(1)  # 等页面渲染
    return t.finish()
'''
        errors, hints = self._lint(src)
        self.assertEqual(len(errors), 0)

    def test_missing_user_input(self):
        """规则 1：缺少 USER_INPUT。"""
        src = '''
def run():
    t = TestCase("test")
    return t.finish()
'''
        errors, hints = self._lint(src)
        self.assertTrue(any("missing_user_input" in e[1] for e in errors))

    def test_bare_tap_xy_constants(self):
        """规则 2：裸坐标 tap_xy(100, 200)。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    t.tap_xy(100, 200)
'''
        errors, hints = self._lint(src)
        self.assertTrue(any("bare_tap_xy" in e[1] for e in errors))

    def test_tap_xy_with_variables_ok(self):
        """规则 2：坐标含变量 → 合法，不报错。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    cx = 100
    t.tap_xy(cx, 200)
    t.tap_xy(cx + 5, cy * 2)
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("bare_tap_xy" in e[1] for e in errors))

    def test_bare_sleep_no_comment(self):
        """规则 3：无注释裸 sleep（> 3s 才报错，≤ 3s 视为 settle）。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    time.sleep(5)
'''
        errors, hints = self._lint(src)
        self.assertTrue(any("bare_sleep" in e[1] for e in errors))

    def test_short_settle_sleep_exempt(self):
        """规则 3：≤ 3s sleep 视为 settle 等待，豁免。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    time.sleep(1)
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("bare_sleep" in e[1] for e in errors))

    def test_sleep_with_comment_ok(self):
        """规则 3：有注释的 sleep → 不报错。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    time.sleep(1)  # 等页面渲染
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("bare_sleep" in e[1] for e in errors))

    def test_sleep_in_documented_function_ok(self):
        """规则 3：有 docstring 的函数内 sleep → 函数级豁免。"""
        src = '''
USER_INPUT = "test"
def _back(t):
    """返回上一页。"""
    t.adb_shell("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1.3)
def run():
    t = TestCase("test")
    _back(t)
    return t.finish()
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("bare_sleep" in e[1] for e in errors))

    def test_tap_no_guard_hint(self):
        """规则 4：tap 后无 if 判返回值 → 提示级。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    t.tap_text("确定")
'''
        errors, hints = self._lint(src)
        self.assertTrue(any("tap_no_guard" in h[1] for h in hints))

    def test_tap_in_if_no_hint(self):
        """规则 4：tap 在 if 体内 → 不提示。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    if not t.tap_text("确定"):
        t.record("FAIL", "not found")
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("tap_no_guard" in h[1] for h in hints))

    def test_tap_assignment_no_hint(self):
        """规则 4：ok = t.tap_... 赋值形态 → 返回值已被捕获，不提示。"""
        src = '''
USER_INPUT = "test"
def run():
    t = TestCase("test")
    ok = t.tap_text("确定")
    t.record("PASS" if ok else "FAIL", "result")
'''
        errors, hints = self._lint(src)
        self.assertFalse(any("tap_no_guard" in h[1] for h in hints))

    def test_syntax_error(self):
        """语法错误的文件 → syntax_error 报错。"""
        src = 'USER_INPUT = "test"\ndef (: pass\n'
        errors, hints = self._lint(src)
        self.assertTrue(any("syntax_error" in e[1] for e in errors))


# ── check_facts.py：toast 断言词守门 ─────────────────────────────
class TestCheckFacts(unittest.TestCase):

    def _lint_facts(self, source, storage_content=None):
        """将源码写入临时文件，语料写入临时目录，执行 check_facts。"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write(source)
            path = f.name
        td = tempfile.mkdtemp()
        if storage_content:
            with open(os.path.join(td, "probe.jsonl"), "w", encoding="utf-8") as pf:
                pf.write(storage_content)
        try:
            return check_facts.check_facts_file(path, [td])
        finally:
            os.unlink(path)
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    def test_no_capture_toast_no_suspects(self):
        """没有 capture_toast 调用 → 零检查零嫌疑。"""
        src = '''
def run():
    t = TestCase("test")
    t.record("PASS", "ok")
'''
        suspects, checked = self._lint_facts(src)
        self.assertEqual(suspects, [])
        self.assertEqual(checked, 0)

    def test_assertion_word_found_in_storage(self):
        """断言词在语料中找到 → 不报嫌疑。"""
        src = '''
def run():
    t = TestCase("test")
    texts, _shot = t.capture_toast(wait=1.0)
    hit = any("课程时间有冲突" in s for s in texts)
'''
        suspects, checked = self._lint_facts(
            src, storage_content='{"text": "课程时间有冲突，无法设置"}')
        self.assertEqual(len(suspects), 0)
        self.assertEqual(checked, 1)

    def test_assertion_word_not_found(self):
        """断言词在语料中找不到 → 报疑似编造。"""
        src = '''
def run():
    t = TestCase("test")
    texts, _shot = t.capture_toast(wait=1.0)
    hit = any("课程时间有冲突" in s for s in texts)
'''
        suspects, checked = self._lint_facts(
            src, storage_content='{"text": "其他内容"}')
        self.assertEqual(len(suspects), 1)
        self.assertEqual(suspects[0][1], "课程时间有冲突")

    def test_short_string_skipped(self):
        """< 4 字的断言词 → 跳过（噪声太大）。"""
        src = '''
def run():
    t = TestCase("test")
    texts, _shot = t.capture_toast(wait=1.0)
    hit = any("成功" in s for s in texts)
'''
        suspects, checked = self._lint_facts(src, storage_content="")
        self.assertEqual(len(suspects), 0)
        self.assertEqual(checked, 0)  # 短串不计入检查

    def test_prefix_whitelist_exempt(self):
        """前缀白名单豁免：「完成/成功/已/阻塞」开头。"""
        src = '''
def run():
    t = TestCase("test")
    texts, _shot = t.capture_toast(wait=1.0)
    hit = any("完成操作" in s for s in texts)
'''
        suspects, checked = self._lint_facts(src, storage_content="")
        self.assertEqual(len(suspects), 0)
        self.assertEqual(checked, 0)  # 白名单不计入检查


if __name__ == "__main__":
    unittest.main()
