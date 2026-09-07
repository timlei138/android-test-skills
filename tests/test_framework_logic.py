#!/usr/bin/env python3
"""framework 纯逻辑单测（不需要 Android 设备）。

覆盖：不改设备的确定性逻辑 —— XML 节点解析、场景卡解析/自动注册、
用例名解析、USER_INPUT 提取、工作区漂移检测、Web UI 路径安全。

运行（skill 包根目录）：
    python -m unittest discover -s tests -v
_parse_nodes 的用例需要 uiautomator2（import test_framework 依赖），
系统 Python 没有时会自动跳过；用工作区 venv 跑可覆盖全部：
    ~/dsh-android-test/.venv/bin/python -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "framework"))

# webui 模块级会解析工作区路径；指到临时目录避免读写真实工作区
os.environ.setdefault("DSH_ANDROID_TEST_DIR",
                      os.path.join(tempfile.gettempdir(), "dsh-unittest-ws"))

import db        # noqa: E402
import run_case  # noqa: E402
import states    # noqa: E402
import vision    # noqa: E402
import webui     # noqa: E402

try:
    import test_framework as tf
except ImportError:  # 系统 Python 无 uiautomator2 时跳过相关用例
    tf = None


# ── states.py：场景卡解析与自动注册 ──────────────────────────────────
class TestParseCmd(unittest.TestCase):
    def test_with_adb_prefix(self):
        self.assertEqual(
            states._parse_cmd("adb shell settings get global zen_mode"),
            ["settings", "get", "global", "zen_mode"])

    def test_without_prefix(self):
        self.assertEqual(states._parse_cmd("settings get global zen_mode"),
                         ["settings", "get", "global", "zen_mode"])

    def test_empty(self):
        self.assertIsNone(states._parse_cmd(""))
        self.assertIsNone(states._parse_cmd(None))
        self.assertIsNone(states._parse_cmd("adb shell"))


class TestParseSimpleCard(unittest.TestCase):
    def test_scalar_and_list(self):
        card = "场景: 勿扰模式\n判定命令: settings get global zen_mode\n触发词:\n- 勿扰\n- dnd\n"
        d = states._parse_simple_card(card)
        self.assertEqual(d["场景"], "勿扰模式")
        self.assertEqual(d["判定命令"], "settings get global zen_mode")
        self.assertEqual(d["触发词"], ["勿扰", "dnd"])

    def test_heading_ends_current_key(self):
        # MD 标题之后的 "- 项" 不应被吸进前面的列表键
        card = "触发词:\n- a\n## 正文\n- 这不是触发词\n"
        d = states._parse_simple_card(card)
        self.assertEqual(d["触发词"], ["a"])

    def test_comment_and_prose_ignored(self):
        card = "<!-- 注释 -->\n场景: x\n随便一行散文\n"
        d = states._parse_simple_card(card)
        self.assertEqual(d, {"场景": "x"})


class TestScenarioRegistration(unittest.TestCase):
    """场景卡写「判定命令」→ 自动注册 is_xxx / raw_xxx。"""

    def test_load_scenarios_registers_methods(self):
        card = ("场景: 单测假场景\n"
                "判定方法: states.is_test_dummy_mode()\n"
                "判定命令: settings get global test_dummy\n"
                "触发词:\n- 单测假场景\n")
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "sys.单测.md"), "w", encoding="utf-8") as f:
                f.write(card)
            old = os.environ.get("DSH_SCENARIOS_DIR")
            os.environ["DSH_SCENARIOS_DIR"] = td
            try:
                states.load_scenarios()
                self.assertTrue(hasattr(states.States, "is_test_dummy_mode"))
                self.assertTrue(hasattr(states.States, "raw_test_dummy_mode"))

                class FakeAdb:
                    def shell(self, *args):
                        return "2"

                s = states.States(adb=FakeAdb())
                self.assertTrue(s.is_test_dummy_mode())      # 非0 → True
                self.assertEqual(s.raw_test_dummy_mode(), "2")
            finally:
                if old is None:
                    os.environ.pop("DSH_SCENARIOS_DIR", None)
                else:
                    os.environ["DSH_SCENARIOS_DIR"] = old
                # 清理注册，避免污染同进程其它测试
                for attr in ("is_test_dummy_mode", "raw_test_dummy_mode"):
                    if attr in states.States.__dict__:
                        delattr(states.States, attr)
                states._REGISTRY[:] = [m for m in states._REGISTRY
                                       if m["name"] != "is_test_dummy_mode"]


class TestTruthy(unittest.TestCase):
    def test_values(self):
        t = states.States._truthy
        for v in ("1", "2", "true", "on", "yes"):
            self.assertTrue(t(v), v)
        for v in ("", "0", "null", "none", "false", "off", None):
            self.assertFalse(t(v), v)


# ── run_case.py：用例解析与 USER_INPUT 提取 ─────────────────────────
class TestResolveCase(unittest.TestCase):
    def setUp(self):
        self._old_dirs = run_case.CASE_DIRS
        self._td = tempfile.TemporaryDirectory()
        root = self._td.name
        os.makedirs(os.path.join(root, "cases", "com.a.x"))
        os.makedirs(os.path.join(root, "cases", "com.b.y"))
        for rel in ("com.a.x/1.py", "com.b.y/1.py", "com.a.x/2.py",
                    "com.a.x/_flow.py"):
            p = os.path.join(root, "cases", *rel.split("/"))
            with open(p, "w", encoding="utf-8") as f:
                f.write("def run():\n    pass\n")
        run_case.CASE_DIRS = [os.path.join(root, "cases")]

    def tearDown(self):
        run_case.CASE_DIRS = self._old_dirs
        self._td.cleanup()

    def test_qualified_path(self):
        hit = run_case.resolve_case("com.a.x/2.py")
        self.assertTrue(hit.endswith(os.path.join("com.a.x", "2.py")))

    def test_bare_unique(self):
        hit = run_case.resolve_case("2.py")
        self.assertIsNotNone(hit)

    def test_bare_ambiguous_returns_none(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(run_case.resolve_case("1.py"))  # 两个包都有

    def test_shared_module_not_matched(self):
        self.assertIsNone(run_case.resolve_case("_flow.py"))

    def test_missing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(run_case.resolve_case("999.py"))


class TestExtractUserInput(unittest.TestCase):
    def test_triple_quoted(self):
        src = 'USER_INPUT = """前提：无\n步骤：点\n预期：成"""\n'
        self.assertEqual(run_case.extract_user_input_from_source(src),
                         "前提：无\n步骤：点\n预期：成")

    def test_content_with_quotes(self):
        # 旧正则在这类内容上会截断/失配；ast 按语法树取常量
        src = "USER_INPUT = '''包含 \"\"\" 三引号 和 \"双引号\" 的内容'''"
        self.assertEqual(run_case.extract_user_input_from_source(src),
                         '包含 """ 三引号 和 "双引号" 的内容')

    def test_single_line(self):
        src = 'USER_INPUT = "单行描述"'
        self.assertEqual(run_case.extract_user_input_from_source(src), "单行描述")

    def test_missing_and_broken(self):
        self.assertIsNone(run_case.extract_user_input_from_source("x = 1"))
        self.assertIsNone(run_case.extract_user_input_from_source("def (:"))
        self.assertIsNone(run_case.extract_user_input_from_source('USER_INPUT = 123'))

    def test_from_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write('USER_INPUT = """文件里的描述"""\n')
            path = f.name
        try:
            self.assertEqual(run_case.extract_user_input(path), "文件里的描述")
        finally:
            os.unlink(path)


class TestFrameworkDrift(unittest.TestCase):
    def _mk(self, root, files):
        fw = os.path.join(root, "framework")
        os.makedirs(fw)
        for name, content in files.items():
            with open(os.path.join(fw, name), "w", encoding="utf-8") as f:
                f.write(content)
        return fw

    def test_drift_detected_and_clean(self):
        base = {f: "same" for f in run_case._DRIFT_KEY_FILES}
        with tempfile.TemporaryDirectory() as td:
            self._mk(os.path.join(td, "ws"), base)
            self._mk(os.path.join(td, "skill"), base)
            # 三方比对：把运行副本注入成工作区那份（真实 HERE 是开发仓，内容不同）
            run_fw = os.path.join(td, "ws", "framework")
            old = {k: os.environ.get(k)
                   for k in ("DSH_ANDROID_TEST_DIR", "DSH_SKILL_DIR")}
            os.environ["DSH_ANDROID_TEST_DIR"] = os.path.join(td, "ws")
            os.environ["DSH_SKILL_DIR"] = os.path.join(td, "skill")
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    run_case.warn_if_framework_drift(run_fw=run_fw)
                self.assertNotIn("不一致", buf.getvalue())
                # 制造漂移
                with open(os.path.join(td, "ws", "framework",
                                       "test_framework.py"), "w") as f:
                    f.write("changed")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    run_case.warn_if_framework_drift(run_fw=run_fw)
                self.assertIn("不一致", buf.getvalue())
                self.assertIn("test_framework.py", buf.getvalue())
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_three_way_detects_stale_installed_copy(self):
        """运行副本纳入比对：从开发仓直接跑时也能发现安装副本过期（168 实测场景）。"""
        base = {f: "same" for f in run_case._DRIFT_KEY_FILES}
        with tempfile.TemporaryDirectory() as td:
            self._mk(os.path.join(td, "ws"), base)
            self._mk(os.path.join(td, "skill"), base)
            run = self._mk(os.path.join(td, "run"), base)
            for f in run_case._DRIFT_KEY_FILES:
                with open(os.path.join(run, f), "w", encoding="utf-8") as fh:
                    fh.write("newer")          # 运行副本比另外两份新
            old = {k: os.environ.get(k)
                   for k in ("DSH_ANDROID_TEST_DIR", "DSH_SKILL_DIR")}
            os.environ["DSH_ANDROID_TEST_DIR"] = os.path.join(td, "ws")
            os.environ["DSH_SKILL_DIR"] = os.path.join(td, "skill")
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    run_case.warn_if_framework_drift(run_fw=run)
                out = buf.getvalue()
                self.assertIn("正在运行", out)
                self.assertIn("工作区备份", out)
                self.assertIn("skill包", out)
                self.assertIn("test_framework.py", out)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


# ── webui.py：用例路径安全 ──────────────────────────────────────────
class TestSafeCaseName(unittest.TestCase):
    def test_flat_and_subdir(self):
        self.assertEqual(webui._safe_case_name("172.py"), "172.py")
        self.assertEqual(webui._safe_case_name("com.zui.calendar/172.py"),
                         "com.zui.calendar/172.py")
        self.assertEqual(webui._safe_case_name("com.zui.calendar\\172.py"),
                         "com.zui.calendar/172.py")

    def test_rejects_traversal_and_bad_input(self):
        for bad in ("../escape.py", "com.zui.calendar/../../x.py",
                    "a/b/c.py", ".hidden/x.py", "x.txt", "", "/abs/x.py"):
            self.assertIsNone(webui._safe_case_name(bad), bad)


# ── test_framework.py：UI XML 节点解析（需要 uiautomator2 环境）──────
_DUMP = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="He said &quot;hi&quot;" resource-id="com.x:id/tv"
        class="android.widget.TextView" content-desc=""
        clickable="false" enabled="true" selected="false" checked="false"
        bounds="[0,100][200,160]" />
  <node text="" resource-id="" class="android.widget.ImageView"
        content-desc="更多" clickable="true" enabled="true"
        bounds="[1600,100][1800,200]" />
</hierarchy>"""


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestParseNodes(unittest.TestCase):
    def test_elementtree_path(self):
        nodes = tf._parse_nodes(_DUMP)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["text"], 'He said "hi"')  # 转义被正确还原
        self.assertEqual(nodes[0]["bounds_xy"], (0, 100, 200, 160))
        self.assertEqual(nodes[0]["clickable"], "false")
        self.assertEqual(nodes[1]["desc"], "更多")
        self.assertEqual(nodes[1]["clickable"], "true")

    def test_regex_fallback_on_broken_xml(self):
        # 属性值含未转义的 & —— ElementTree 判定非法 XML，回退正则提取
        broken = '<hierarchy><node text="a & b" bounds="[1,2][3,4]" clickable="true"/></hierarchy>'
        nodes = tf._parse_nodes(broken)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["text"], "a & b")
        self.assertEqual(nodes[0]["bounds_xy"], (1, 2, 3, 4))


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestWaitPrimitives(unittest.TestCase):
    """条件等待原语：命中即返回、超时有界（替代裸 sleep）。"""
    _XML = ('<hierarchy><node text="目标文字" resource-id="com.x:id/btn" '
            'bounds="[0,0][10,10]" clickable="true"/></hierarchy>')

    def _mk(self):
        t = object.__new__(tf.TestCase)     # 绕过 __init__（不连设备）
        t._wd_enabled = False

        class FakeD:
            def dump_hierarchy(self_):
                return TestWaitPrimitives._XML
        t.d = FakeD()
        return t

    def test_wait_text_hit_and_timeout(self):
        t = self._mk()
        self.assertTrue(t.wait_text("目标文字", timeout=1, interval=0.1))
        t0 = time.time()
        self.assertFalse(t.wait_text("不存在", timeout=0.5, interval=0.2))
        self.assertLess(time.time() - t0, 2)   # 超时确实有界返回

    def test_wait_rid(self):
        t = self._mk()
        self.assertTrue(t.wait_rid("com.x:id/btn", timeout=1, interval=0.1))
        self.assertFalse(t.wait_rid("com.x:id/none", timeout=0.4, interval=0.2))

    def test_wait_activity(self):
        t = self._mk()
        t.current_activity = lambda: "com.x/.ui.MainActivity"  # 实例属性遮蔽方法
        self.assertEqual(t.wait_activity("mainactivity", timeout=0.5),
                         "com.x/.ui.MainActivity")
        self.assertEqual(t.wait_activity("settings", timeout=0.3, interval=0.1), "")


# ── 结果语义：最终状态与退出码 ─────────────────────────────────────
class TestExitCodes(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(run_case.exit_code_for("PASS"), 0)
        self.assertEqual(run_case.exit_code_for("WARN"), 0)
        self.assertEqual(run_case.exit_code_for("FAIL"), 1)
        self.assertEqual(run_case.exit_code_for("BLOCKED"), 2)
        # 未知/缺失一律按 ERROR(3)，绝不默认 0 放行
        self.assertEqual(run_case.exit_code_for("ERROR"), 3)
        self.assertEqual(run_case.exit_code_for(None), 3)
        self.assertEqual(run_case.exit_code_for("随便什么"), 3)


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestFinalStatus(unittest.TestCase):
    """最终结论规则：FAIL > BLOCKED > WARN > PASS，不从摘要文本推断。"""

    def _mk(self, results):
        t = object.__new__(tf.TestCase)
        t.steps = [{"name": "s", "results":
                    [{"result": r, "detail": "x"} for r in results],
                    "evidences": []}]
        return t

    def test_priority(self):
        self.assertEqual(self._mk(["PASS", "PASS"])._compute_final_status(), "PASS")
        self.assertEqual(self._mk(["PASS", "WARN"])._compute_final_status(), "WARN")
        self.assertEqual(self._mk(["BLOCKED"])._compute_final_status(), "BLOCKED")
        # 关键回归：只有 BLOCKED 的用例绝不能是 PASS
        self.assertNotEqual(self._mk(["BLOCKED"])._compute_final_status(), "PASS")
        self.assertEqual(self._mk(["BLOCKED", "FAIL"])._compute_final_status(), "FAIL")
        self.assertEqual(self._mk(["WARN", "FAIL"])._compute_final_status(), "FAIL")
        self.assertEqual(self._mk(["INFO"])._compute_final_status(), "PASS")

    def test_fatal_error_forces_error(self):
        """异常路径（N4）：run_case 捕获异常设 _fatal_error 后补调 finish()，
        结论必须压成 ERROR —— 用例没跑完，断言统计再好看也不可信。"""
        t = self._mk(["PASS", "PASS"])
        t.name = "单测_fatal_error"
        t.device_info = "fake-device"
        t.case_dir = tempfile.mkdtemp()      # finish() 报告头引用证据目录
        t._case_start_time = time.time()
        t._dump_count = 0
        t._db = None
        t._db_case_id = None
        t._fatal_error = RuntimeError("模拟执行中途异常")
        old_last = tf.LAST_CASE
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "ERROR")
        finally:
            tf.LAST_CASE = old_last

    def test_finish_idempotent(self):
        """finish() 幂等守卫（P3-1）：用例正常 finish 后收尾代码再抛异常时，
        run_case 的异常兜底会再调一次 finish —— 必须直接返回旧报告，
        不重复备份/写库/重算结论。"""
        t = self._mk(["PASS"])
        t.name = "单测_finish_幂等"
        t.device_info = "fake-device"
        t.case_dir = tempfile.mkdtemp()
        t._case_start_time = time.time()
        t._dump_count = 0
        t._db = None
        t._db_case_id = None
        t._fatal_error = None
        old_last = tf.LAST_CASE
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                p1 = t.finish()
                self.assertTrue(t._finished)
                self.assertEqual(t.final_status, "PASS")
                # 模拟"finish 后收尾代码又出事"：改变状态再调 finish，
                # 守卫应直接返回旧路径，final_status 不被重算覆盖
                t._fatal_error = RuntimeError("finish 之后的收尾异常")
                p2 = t.finish()
            self.assertEqual(p1, p2)
            self.assertEqual(t.final_status, "PASS")   # 不变 ERROR
            self.assertEqual(buf.getvalue().count("报告已生成"), 1)
        finally:
            tf.LAST_CASE = old_last

    def test_finish_idempotent(self):
        """finish() 幂等守卫（P3-1）：用例正常 finish 后收尾代码再抛异常时，
        run_case 的异常兜底会再调一次 finish —— 必须直接返回旧报告，
        不重复备份/写库/重算结论。"""
        t = self._mk(["PASS"])
        t.name = "单测_finish_幂等"
        t.device_info = "fake-device"
        t.case_dir = tempfile.mkdtemp()
        t._case_start_time = time.time()
        t._dump_count = 0
        t._db = None
        t._db_case_id = None
        t._fatal_error = None
        old_last = tf.LAST_CASE
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()) as rd:
                p1 = t.finish()
                self.assertTrue(t._finished)
                self.assertEqual(t.final_status, "PASS")
                # 模拟"finish 后收尾代码又出事"：改变状态再调 finish，
                # 守卫应直接返回旧路径，final_status 不被重算覆盖
                t._fatal_error = RuntimeError("finish 之后的收尾异常")
                p2 = t.finish()
            self.assertEqual(p1, p2)
            self.assertEqual(t.final_status, "PASS")   # 不变 ERROR
            self.assertEqual(buf.getvalue().count("报告已生成"), 1)
        finally:
            tf.LAST_CASE = old_last


# ── 被测 App 包名推断（finish() 报告头 / DB package 列的数据源）──────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestCasePackageInference(unittest.TestCase):
    """package 取值优先级：脚本目录名（像包名）> 收尾前台包。

    背景：用例常停在 PhotoPicker 等系统页收尾，前台包可能根本不是被测 App
    （168 实测报成 com.android.providers.media.module）。"""

    def _mk(self, script_path):
        t = object.__new__(tf.TestCase)
        t.script_path = script_path
        return t

    def test_infers_from_windows_script_path(self):
        t = self._mk("C:\\Users\\u\\.agents\\skills\\android-gui-testing"
                     "\\cases\\com.zui.calendar\\168.py")
        self.assertEqual(t._case_package_from_script(), "com.zui.calendar")

    def test_infers_from_posix_script_path(self):
        t = self._mk("/home/u/skills/android-gui-testing/cases/com.a.b/172.py")
        self.assertEqual(t._case_package_from_script(), "com.a.b")

    def test_non_package_dir_returns_none(self):
        t = self._mk("D:\\x\\cases\\联想日历\\168.py")
        self.assertIsNone(t._case_package_from_script())

    def test_no_cases_segment_returns_none(self):
        t = self._mk("D:\\somewhere\\168.py")
        self.assertIsNone(t._case_package_from_script())

    def test_no_script_path_returns_none(self):
        self.assertIsNone(self._mk(None)._case_package_from_script())


# ── vision.py：鉴权头使用真实 Key（mock HTTP，不触网、不泄露）──────
class TestVisionAuth(unittest.TestCase):
    def test_authorization_uses_real_key(self):
        v = vision.Vision(api_key="sk-unit-test-dummy", base_url="http://127.0.0.1")
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "OK"}}]}).encode()

        def fake_urlopen(req, timeout=None):
            captured["auth"] = req.headers.get("Authorization")
            return FakeResp()

        with mock.patch.object(vision.urllib.request, "urlopen", fake_urlopen):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                out = v.ask("测试", b"\x89PNG\r\n\x1a\n")  # 假 PNG 字节即可
        self.assertEqual(out, "OK")
        self.assertEqual(captured["auth"], "Bearer sk-unit-test-dummy")
        # 日志/输出不得出现明文 Key
        self.assertNotIn("sk-unit-test-dummy", buf.getvalue())


# ── db.py / webui.py：产物目录白名单 ────────────────────────────────
class TestArtifactPath(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old = os.environ.get("DSH_ANDROID_TEST_DIR")
        os.environ["DSH_ANDROID_TEST_DIR"] = self._td.name
        self.shots = os.path.join(self._td.name, "storage", "screenshots")
        os.makedirs(self.shots)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DSH_ANDROID_TEST_DIR", None)
        else:
            os.environ["DSH_ANDROID_TEST_DIR"] = self._old
        self._td.cleanup()

    def test_inside_outside(self):
        inside = os.path.join(self.shots, "case_1", "01.png")
        self.assertTrue(db.is_artifact_path(inside))
        self.assertTrue(db.is_artifact_path(
            os.path.join(self._td.name, "storage", "reports", "r.md")))
        # 目录外 / 相对路径 / 穿越伪装一律拒绝
        self.assertFalse(db.is_artifact_path(os.path.join(self._td.name, "x.py")))
        self.assertFalse(db.is_artifact_path("relative.png"))
        evil = os.path.join(self.shots, "..", "..", "db.py")
        self.assertFalse(db.is_artifact_path(evil))
        self.assertFalse(db.is_artifact_path(""))


# ── test_framework.py：多设备 serial 绑定与 require_* 强语义 ────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestResolveSerial(unittest.TestCase):
    def _fake_adb_devices(self, stdout):
        def fake_run(cmd, *a, **k):
            class R:
                pass
            r = R()
            r.stdout = stdout
            return r
        return mock.patch("subprocess.run", fake_run)

    def test_single_device(self):
        out = "List of devices attached\nemulator-5554\tdevice\n\n"
        with self._fake_adb_devices(out):
            self.assertEqual(tf._resolve_serial(), "emulator-5554")

    def test_multi_device_fails_fast(self):
        out = ("List of devices attached\nemulator-5554\tdevice\n"
               "192.168.1.2:5555\tdevice\n\n")
        with self._fake_adb_devices(out):
            with self.assertRaises(RuntimeError):
                tf._resolve_serial()

    def test_no_device(self):
        with self._fake_adb_devices("List of devices attached\n\n"):
            with self.assertRaises(RuntimeError):
                tf._resolve_serial()

    def test_explicit_passthrough(self):
        # 显式指定时不查 adb devices
        self.assertEqual(tf._resolve_serial("device-b"), "device-b")


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestRequireTap(unittest.TestCase):
    """require_* 找不到元素：记 FAIL + 抛 CaseAbort（防假通过）。"""

    def _mk(self, xml):
        t = object.__new__(tf.TestCase)
        t._wd_enabled = False
        t._db = None
        t._db_step_id = None
        t._cur_step = {"name": "s", "results": [], "evidences": []}
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()

        class FakeD:
            def dump_hierarchy(self_):
                return xml
        t.d = FakeD()
        return t

    def test_missing_element_aborts(self):
        t = self._mk('<hierarchy><node text="别的" bounds="[0,0][1,1]"/></hierarchy>')
        buf = io.StringIO()
        with self.assertRaises(tf.CaseAbort):
            with contextlib.redirect_stdout(buf):   # record 会打 ❌ emoji，GBK 控制台会崩
                t.require_tap_text("不存在", wait=0.4)
        results = t._cur_step["results"]
        self.assertTrue(any(r["result"] == "FAIL" for r in results))


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestRecordAutoStep(unittest.TestCase):
    """record() 在未开 step 时自动补一个可追溯步骤，而不是崩在 NoneType。

    回归保护：框架 record() 曾直接解引用 _cur_step，调用方忘了 t.step() 就抛
    TypeError: 'NoneType' object is not subscriptable —— 报错完全看不出根因是
    没开 step（175 用例踩到，排查成本很高）。
    """

    def _mk(self):
        t = object.__new__(tf.TestCase)
        t._cur_step = None              # 关键前提：从未开过 step
        t.steps = []
        t._db = None
        t._db_step_id = None
        t._db_step_ord = 0
        t._wd_enabled = False
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()
        return t

    def test_record_without_step_auto_creates(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()):
            t.record("PASS", "无 step 直接记录")      # 不该抛异常
        self.assertEqual(len(t.steps), 1)
        self.assertIn("未显式声明", t.steps[0]["name"])
        self.assertEqual(t.steps[0]["results"][0]["detail"], "无 step 直接记录")

    def test_blocked_without_step_auto_creates(self):
        t = self._mk()                                 # blocked() 走 record，同样受保护
        with contextlib.redirect_stdout(io.StringIO()):
            t.blocked("环境不满足")
        self.assertEqual(len(t.steps), 1)
        self.assertIn("阻塞", t.steps[0]["results"][0]["detail"])

    def test_existing_step_not_overridden(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()):
            t.step("我的步骤")
            t.record("PASS", "正常记录")
        # 已开过 step 时不能另起一个，结果必须落在原步骤里
        self.assertEqual(len(t.steps), 1)
        self.assertEqual(t.steps[0]["name"], "我的步骤")


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestTapUnifiedContract(unittest.TestCase):
    """统一动作基元契约（N1/N2 修复的回归保护）：
    tap_* 轮询定位返回 bool；找不到默认记 WARN；silent=True 时交由调用方
    守卫分支记录（防双重记录）；observe=False 立即返回不延迟。"""

    _XML = ('<hierarchy><node text="确定" resource-id="com.x:id/btn" '
            'bounds="[10,20][110,60]" clickable="true"/></hierarchy>')

    def _mk(self, xml=None):
        t = object.__new__(tf.TestCase)
        t._wd_enabled = False
        t._db = None
        t._db_step_id = None
        t._cur_step = {"name": "s", "results": [], "evidences": []}
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()
        t._auto_screenshot = lambda *a, **k: None     # 单测不真截屏
        clicks = []

        class FakeD:
            def dump_hierarchy(self_):
                return xml if xml is not None else TestTapUnifiedContract._XML

            def click(self_, x, y):
                clicks.append((x, y))

            def send_keys(self_, s):
                pass

            def clear_text(self_):
                pass
        t.d = FakeD()
        t._clicks = clicks
        return t

    def test_hit_returns_true_and_clicks_center(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertTrue(t.tap_text("确定", wait=1))
        self.assertEqual(t._clicks, [(60, 40)])   # bounds [10,20][110,60] 中心

    def test_miss_returns_false_and_records_warn(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertFalse(t.tap_text("不存在", wait=0.3))
        self.assertEqual([r["result"] for r in t._cur_step["results"]], ["WARN"])

    def test_miss_silent_records_nothing(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertFalse(t.tap_text("不存在", wait=0.3, silent=True))
        self.assertEqual(t._cur_step["results"], [])   # 守卫语义交还调用方

    def test_observe_false_returns_immediately(self):
        t = self._mk()
        t0 = time.time()
        self.assertTrue(t.tap_rid("com.x:id/btn", observe=False))
        self.assertLess(time.time() - t0, 0.5)          # 不 sleep(ACTION_DELAY)
        self.assertEqual(t._clicks, [(60, 40)])

    def test_tap_xy_returns_true(self):
        t = self._mk()
        self.assertIs(t.tap_xy(5, 5, observe=False), True)  # 不再返回 self 链式

    def test_input_text_hit_and_miss_silent(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertTrue(t.input_text("com.x:id/btn", "你好", wait=1))
            self.assertFalse(t.input_text("com.x:id/none", "x", wait=0.3,
                                          silent=True))
        self.assertEqual(t._clicks, [(60, 40)])
        self.assertEqual(t._cur_step["results"], [])   # silent：无 WARN 兜底


@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestRequireTapSingleImpl(unittest.TestCase):
    """require_* 收敛为 tap_* 的 silent 模式（N2 竞态消除）：
    命中时不产生任何 WARN/FAIL 记录 —— 旧实现"先 wait 后 tap"两步之间的
    竞态窗口已消除，且不会双重记录。"""

    def _mk(self):
        t = object.__new__(tf.TestCase)
        t._wd_enabled = False
        t._db = None
        t._db_step_id = None
        t._cur_step = {"name": "s", "results": [], "evidences": []}
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()
        t._auto_screenshot = lambda *a, **k: None

        class FakeD:
            def dump_hierarchy(self_):
                return ('<hierarchy><node text="确定" resource-id="com.x:id/btn" '
                        'bounds="[0,0][100,50]" clickable="true"/></hierarchy>')

            def click(self_, x, y):
                pass
        t.d = FakeD()
        return t

    def test_hit_records_nothing(self):
        t = self._mk()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(tf, "ACTION_DELAY", 0):
            self.assertIs(t.require_tap_text("确定", wait=1), True)
        self.assertEqual(t._cur_step["results"], [])   # 命中：零记录零噪声


if __name__ == "__main__":
    unittest.main()
