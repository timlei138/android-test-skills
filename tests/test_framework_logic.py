#!/usr/bin/env python3
"""framework 纯逻辑单测（不需要 Android 设备）。

覆盖：不改设备的确定性逻辑 —— XML 节点解析、场景卡解析/自动注册、
用例名解析、USER_INPUT 提取、工作区漂移检测、Web UI 路径安全。

运行（skill 包根目录）：
    python -m unittest discover -s tests -v
_parse_nodes 的用例需要 uiautomator2（import test_framework 依赖），
系统 Python 没有时会自动跳过；用工作区 venv 跑可覆盖全部：
    ~/android-test-skills-data/.venv/bin/python -m unittest discover -s tests -v
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
os.environ.setdefault("DSH_WORKSPACE_DIR",
                      os.path.join(tempfile.gettempdir(), "dsh-unittest-ws"))

import db        # noqa: E402
import run_case  # noqa: E402
import states    # noqa: E402
import vision    # noqa: E402
import webui     # noqa: E402

# scripts/ 目录加入 sys.path 以便导入预算门禁脚本
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import check_context_budget as budget_mod  # noqa: E402

# framework/smoke.py 加入 sys.path 以便测试 _check_adb
import smoke  # noqa: E402

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
                   for k in ("DSH_WORKSPACE_DIR", "DSH_SKILL_DIR")}
            os.environ["DSH_WORKSPACE_DIR"] = os.path.join(td, "ws")
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
                   for k in ("DSH_WORKSPACE_DIR", "DSH_SKILL_DIR")}
            os.environ["DSH_WORKSPACE_DIR"] = os.path.join(td, "ws")
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
        t = self._mk("C:\\Users\\u\\.agents\\skills\\android-test-skills"
                     "\\cases\\com.zui.calendar\\168.py")
        self.assertEqual(t._case_package_from_script(), "com.zui.calendar")

    def test_infers_from_posix_script_path(self):
        t = self._mk("/home/u/skills/android-test-skills/cases/com.a.b/172.py")
        self.assertEqual(t._case_package_from_script(), "com.a.b")

    def test_non_package_dir_returns_none(self):
        t = self._mk("D:\\x\\cases\\联想日历\\168.py")
        self.assertIsNone(t._case_package_from_script())

    def test_no_cases_segment_returns_none(self):
        t = self._mk("D:\\somewhere\\168.py")
        self.assertIsNone(t._case_package_from_script())

    def test_no_script_path_returns_none(self):
        self.assertIsNone(self._mk(None)._case_package_from_script())


# ── db.py：库文件父目录缺失时自动创建（webui 纯前端场景回归）────────
class TestDbMissingParentDir(unittest.TestCase):
    """storage/ 未创建（纯 Web UI / 首次使用 / HOME 被重定向）时，
    connect 曾直接抛 OperationalError: unable to open database file。"""

    def test_connect_creates_missing_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            deep = os.path.join(td, "ws", "storage", "sub")
            dbp = os.path.join(deep, "test_records.db")
            self.assertFalse(os.path.isdir(deep))
            rdb = db.RecordDB(path=dbp)
            try:
                self.assertEqual(rdb.list_cases(), [])   # 不抛 CANTOPEN
            finally:
                rdb.close()
            self.assertTrue(os.path.isfile(dbp))


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
        self._old = os.environ.get("DSH_WORKSPACE_DIR")
        os.environ["DSH_WORKSPACE_DIR"] = self._td.name
        self.shots = os.path.join(self._td.name, "storage", "screenshots")
        os.makedirs(self.shots)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DSH_WORKSPACE_DIR", None)
        else:
            os.environ["DSH_WORKSPACE_DIR"] = self._old
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


# ── states.py：环境漂移检测 ─────────────────────────────────────
class TestEnvSnapshot(unittest.TestCase):
    """env_snapshot() 取设备环境快照，env_diff() 比对差异。"""

    def _mk_states(self, values):
        """造一个假 States，adb.shell 按命令返回预设值。"""
        s = states.States.__new__(states.States)

        class FakeAdb:
            def shell(self_, *args):
                cmd = " ".join(args)
                for key, val in values.items():
                    if key in cmd:
                        return val
                return ""

        s.adb = FakeAdb()
        return s

    def test_snapshot_returns_all_keys(self):
        s = self._mk_states({
            "accelerometer_rotation": "0",
            "user_rotation": "0",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "0",
        })
        snap = s.env_snapshot()
        self.assertEqual(snap["accelerometer_rotation"], "0")
        self.assertEqual(snap["user_rotation"], "0")
        self.assertEqual(snap["stay_on_while_plugged_in"], "3")
        self.assertEqual(snap["zen_mode"], "0")
        # foreground_package 和 screen_brightness 已移除（误报源）
        self.assertNotIn("foreground_package", snap)
        self.assertNotIn("screen_brightness", snap)

    def test_diff_no_change(self):
        s = self._mk_states({
            "accelerometer_rotation": "0",
            "user_rotation": "0",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "0",
        })
        baseline = s.env_snapshot()
        diff = s.env_diff(baseline)
        self.assertEqual(diff, {})

    def test_diff_detects_change(self):
        before = {"accelerometer_rotation": "0", "user_rotation": "0",
                  "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        # 用例跑了之后 zen_mode 变成了 1（勿扰）
        s = self._mk_states({
            "accelerometer_rotation": "0",
            "user_rotation": "0",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "1",
        })
        diff = s.env_diff(before)
        self.assertIn("zen_mode", diff)
        self.assertEqual(diff["zen_mode"], ("0", "1"))
        self.assertNotIn("accelerometer_rotation", diff)

    def test_diff_ignore_keys(self):
        before = {"accelerometer_rotation": "0", "user_rotation": "0",
                  "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        # 用例改了 user_rotation（合法操作），但豁免
        s = self._mk_states({
            "accelerometer_rotation": "0",
            "user_rotation": "1",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "0",
        })
        diff = s.env_diff(before, ignore=("user_rotation",))
        self.assertEqual(diff, {})
        # 不豁免则能检测到
        diff2 = s.env_diff(before)
        self.assertIn("user_rotation", diff2)

    def test_diff_multiple_changes(self):
        before = {"accelerometer_rotation": "0", "user_rotation": "0",
                  "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        s = self._mk_states({
            "accelerometer_rotation": "1",
            "user_rotation": "3",
            "stay_on_while_plugged_in": "3",
            "zen_mode": "2",
        })
        diff = s.env_diff(before)
        self.assertEqual(len(diff), 3)   # 除了 stay_on_while_plugged_in


# ── test_framework.py：finish() 环境漂移检测集成 ────────────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestEnvDriftInFinish(unittest.TestCase):
    """finish() 前环境漂移检测：有漂移 → PASS 降级为 WARN；env_ignore 豁免。"""

    def _mk(self, env_baseline, env_after, env_ignore=()):
        t = object.__new__(tf.TestCase)
        t.name = "单测_漂移检测"
        t.device_info = "fake"
        t.case_dir = tempfile.mkdtemp()
        t._case_start_time = time.time()
        t._dump_count = 0
        t._db = None
        t._db_case_id = None
        t._fatal_error = None
        t._env_ignore = set(env_ignore)
        t._env_baseline = env_baseline
        t.steps = [{"name": "s", "results":
                    [{"result": "PASS", "detail": "ok"}],
                    "evidences": []}]
        # mock states
        s = states.States.__new__(states.States)

        class FakeAdb:
            def shell(self_, *args):
                cmd = " ".join(args)
                for key, val in env_after.items():
                    if key in cmd:
                        return val
                return ""

        s.adb = FakeAdb()
        t.states = s
        return t

    def test_no_drift_stays_pass(self):
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        t = self._mk(baseline, dict(baseline))
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "PASS")
        finally:
            tf.LAST_CASE = old_last

    def test_drift_downgrades_to_warn(self):
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        after = dict(baseline)
        after["zen_mode"] = "1"   # 用例开了勿扰没关
        t = self._mk(baseline, after)
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "WARN")
            # 报告里应有漂移信息
            warn_records = [r for s in t.steps for r in s["results"]
                           if r["result"] == "WARN"]
            self.assertTrue(any("污染设备环境" in r["detail"]
                               for r in warn_records))
        finally:
            tf.LAST_CASE = old_last

    def test_env_ignore_prevents_drift_warn(self):
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        after = dict(baseline)
        after["user_rotation"] = "1"   # 合法操作（横屏用例）
        t = self._mk(baseline, after, env_ignore=("user_rotation",))
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "PASS")
        finally:
            tf.LAST_CASE = old_last

    def test_drift_skipped_on_error(self):
        """ERROR 状态不做漂移检测（用例没跑完，环境不可信）。"""
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        after = dict(baseline)
        after["zen_mode"] = "1"
        t = self._mk(baseline, after)
        t._fatal_error = RuntimeError("模拟异常")
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "ERROR")
            # ERROR 路径不应添加漂移 WARN 记录
            warn_records = [r for s in t.steps for r in s["results"]
                           if r["result"] == "WARN" and "污染" in r["detail"]]
            self.assertEqual(len(warn_records), 0)
        finally:
            tf.LAST_CASE = old_last

    def test_fail_not_downgraded_by_drift(self):
        """FAIL 不被漂移检测覆盖（漂移只影响 PASS 用例）。"""
        baseline = {"accelerometer_rotation": "0", "user_rotation": "0",
                    "stay_on_while_plugged_in": "3", "zen_mode": "0"}
        after = dict(baseline)
        after["zen_mode"] = "1"
        t = self._mk(baseline, after)
        # 覆盖步骤为 FAIL
        t.steps = [{"name": "s", "results":
                    [{"result": "FAIL", "detail": "断言失败"}],
                    "evidences": []}]
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                t.finish()
            self.assertEqual(t.final_status, "FAIL")
        finally:
            tf.LAST_CASE = old_last


# ── scripts/check_context_budget.py：上下文预算门禁 ────────────
class TestContextBudget(unittest.TestCase):
    """临时目录造超限/达标文件，验证 check() 返回值。"""

    def _setup_root(self, files):
        """files: {rel_path: line_count}"""
        td = tempfile.TemporaryDirectory()
        for rel, count in files.items():
            fp = os.path.join(td.name, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write("\n".join([f"line {i}" for i in range(count)]))
        return td

    def test_over_limit_returns_error(self):
        """超限文件 → errors 非空。"""
        td = self._setup_root({"SKILL.md": 300})
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("SKILL.md", 100)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(len(errors), 1)
            self.assertIn("超限", errors[0])
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_within_limit_no_error(self):
        """达标文件 → errors 为空。"""
        td = self._setup_root({"SKILL.md": 50})
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("SKILL.md", 100)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(len(errors), 0)
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_warn_at_80_percent(self):
        """达 80% 预算 → warnings 非空但 errors 为空。"""
        td = self._setup_root({"SKILL.md": 85})
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("SKILL.md", 100)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(len(errors), 0)
            self.assertEqual(len(warnings), 1)
            self.assertIn("接近", warnings[0])
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_glob_pattern(self):
        """glob 模式匹配多个文件。"""
        td = self._setup_root({
            "knowledge/a.md": 30,
            "knowledge/b.md": 500,
        })
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("knowledge/*.md", 400)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(len(errors), 1)   # b.md 超限
            self.assertIn("b.md", errors[0])
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_missing_file_ignored(self):
        """预算规则中的文件不存在 → 不报错。"""
        td = tempfile.TemporaryDirectory()
        old = budget_mod.BUDGETS
        budget_mod.BUDGETS = [("nonexistent.md", 100)]
        try:
            errors, warnings, _ = budget_mod.check(td.name)
            self.assertEqual(errors, [])
        finally:
            budget_mod.BUDGETS = old
        td.cleanup()

    def test_token_estimate(self):
        """token 估算：中文 + 英文混合。"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as f:
            f.write("这是中文测试 hello world test")
            path = f.name
        try:
            tok = budget_mod._token_estimate(path)
            # 6 中文 + 4 英文词 = 6 + 4*1.3 ≈ 11
            self.assertGreater(tok, 5)
            self.assertLess(tok, 20)
        finally:
            os.unlink(path)


# ── 3.2 VERSION + drift ─────────────────────────────────────────────────
class TestVersionInDrift(unittest.TestCase):
    """VERSION 纳入 _DRIFT_KEY_FILES，版本不一致时触发漂移告警。"""

    def test_version_in_drift_keys(self):
        self.assertIn("VERSION", run_case._DRIFT_KEY_FILES)

    def test_version_file_exists(self):
        """framework/VERSION 文件存在且内容合法（语义化版本 x.y.z）。"""
        ver_path = os.path.join(_ROOT, "framework", "VERSION")
        self.assertTrue(os.path.isfile(ver_path))
        with open(ver_path, encoding="utf-8") as f:
            ver = f.read().strip()
        # 简单语义化版本校验
        self.assertRegex(ver, r"^\d+\.\d+\.\d+")


# ── 3.4 _is_loopback ──────────────────────────────────────────────────
class TestIsLoopback(unittest.TestCase):
    def test_loopback_addresses(self):
        for addr in ("127.0.0.1", "localhost", "::1"):
            self.assertTrue(webui._is_loopback(addr), addr)

    def test_non_loopback_addresses(self):
        for addr in ("0.0.0.0", "192.168.1.1", "10.0.0.5", ""):
            self.assertFalse(webui._is_loopback(addr), addr)


# ── 3.3 smoke._check_adb ────────────────────────────────────────────────
class TestSmokeCheckAdb(unittest.TestCase):
    """smoke._check_adb() 的 mock 测试（不依赖真实设备）。"""

    def test_adb_not_found(self):
        """adb 不在 PATH → 返回 False + 引导文案。"""
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            ok, msg = smoke._check_adb()
        self.assertFalse(ok)
        self.assertIn("adb", msg)

    def test_no_device(self):
        """adb 在但无设备 → 返回 False。"""
        mock_result = mock.Mock()
        mock_result.stdout = "List of devices attached\n\n"
        with mock.patch("subprocess.run", return_value=mock_result):
            ok, msg = smoke._check_adb()
        self.assertFalse(ok)
        self.assertIn("设备", msg)

    def test_device_found(self):
        """有授权设备 → 返回 True。"""
        mock_result = mock.Mock()
        mock_result.stdout = "List of devices attached\nABCDEF123\tdevice\n"
        with mock.patch("subprocess.run", return_value=mock_result):
            ok, msg = smoke._check_adb()
        self.assertTrue(ok)
        self.assertIn("ABCDEF123", msg)


# ── 阶段四：record() RESULT_TYPES 枚举 ──────────────────────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestRecordResultTypes(unittest.TestCase):
    """record() 非标准结果类型抛 ValueError（拼写错误在开发期即暴露）。"""

    def _mk(self):
        t = object.__new__(tf.TestCase)
        t._cur_step = None
        t.steps = []
        t._db = None
        t._db_step_id = None
        t._db_step_ord = 0
        t._wd_enabled = False
        t._shot_idx = 0
        t.case_dir = tempfile.mkdtemp()
        return t

    def test_valid_types_no_error(self):
        """标准结果类型不抛异常。"""
        for rt in ("PASS", "FAIL", "WARN", "INFO", "BLOCKED"):
            t = self._mk()
            with contextlib.redirect_stdout(io.StringIO()):
                t.record(rt, f"test {rt}")   # 不应抛

    def test_invalid_type_raises(self):
        """拼写错误 → ValueError。"""
        t = self._mk()
        with self.assertRaises(ValueError) as cm:
            t.record("PASSS", "typo")
        self.assertIn("PASSS", str(cm.exception))

    def test_lowercase_raises(self):
        """小写也不行。"""
        t = self._mk()
        with self.assertRaises(ValueError):
            t.record("pass", "lowercase")


# ── 阶段四：finish() 报告文件名清洗 ──────────────────────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestFinishSafeName(unittest.TestCase):
    """用例名含文件系统非法字符时，finish() 报告文件名用 re.sub 清洗。"""

    def _mk(self):
        t = object.__new__(tf.TestCase)
        t.steps = [{"name": "s1", "results": [{"result": "PASS", "detail": "ok",
                     "state": None, "evidence": None}], "evidences": [], "actions": []}]
        t._cur_step = t.steps[0]
        t._db = None
        t._db_case_id = None
        t._fatal_error = None
        t._env_baseline = None
        t._env_ignore = ()
        t._case_start_time = time.time()
        t._dump_count = 0
        t._finished = False
        t._report_path = None
        t.device_info = "fake"
        t.case_dir = tempfile.mkdtemp()
        t.script_path = ""
        t.states = None
        return t

    def test_colon_in_name(self):
        """用例名含 : → 替换为 _，报告正常生成。"""
        t = self._mk()
        t.name = "用例:带冒号"
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()) as rd:
                path = t.finish()
            self.assertNotIn(":", os.path.basename(path))
            self.assertTrue(os.path.isfile(path))
        finally:
            tf.LAST_CASE = old_last

    def test_slash_in_name(self):
        """用例名含 / → 替换为 _。"""
        t = self._mk()
        t.name = "用例/带斜杠"
        old_last = tf.LAST_CASE
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(tf, "REPORT_DIR", tempfile.mkdtemp()):
                path = t.finish()
            self.assertNotIn("/", os.path.basename(path).replace("_报告.md", ""))
            self.assertTrue(os.path.isfile(path))
        finally:
            tf.LAST_CASE = old_last


# ── 阶段四：already_finished 分支（run_case.py L288-301）─────────
class TestAlreadyFinishedBranch(unittest.TestCase):
    """用例正常 finish 后收尾代码再抛异常时，退出码沿用 final_status 映射而非 3。"""

    def test_exit_code_uses_final_status(self):
        """already_finished=True 时 exit_code_for(final_status) 而非 3。"""
        # PASS 用例的 already_finished 分支应返回 0
        self.assertEqual(run_case.exit_code_for("PASS"), 0)
        self.assertEqual(run_case.exit_code_for("WARN"), 0)
        # FAIL 用例应返回 1
        self.assertEqual(run_case.exit_code_for("FAIL"), 1)

    def test_already_finished_logic_in_run_case(self):
        """模拟 run_case.py 的 already_finished 判定逻辑。"""
        # 模拟：tc._finished = True, final_status = "PASS"
        class FakeTC:
            _finished = True
            final_status = "PASS"
        tc = FakeTC()
        already_finished = bool(tc is not None and getattr(tc, "_finished", False))
        self.assertTrue(already_finished)
        code = run_case.exit_code_for(getattr(tc, "final_status", None))
        self.assertEqual(code, 0)   # PASS → 0，不是 3


# ── 阶段四：VISION_CONF_FILE 惰性求值 ──────────────────────────
class TestVisionConfLazyEval(unittest.TestCase):
    """import vision 后改环境变量，_vision_conf_path() 应反映新路径。"""

    def test_lazy_eval_after_env_change(self):
        old = os.environ.get("DSH_WORKSPACE_DIR")
        try:
            os.environ["DSH_WORKSPACE_DIR"] = "/tmp/test_ws_1"
            p1 = vision._vision_conf_path()
            self.assertIn("test_ws_1", p1)

            os.environ["DSH_WORKSPACE_DIR"] = "/tmp/test_ws_2"
            p2 = vision._vision_conf_path()
            self.assertIn("test_ws_2", p2)
            self.assertNotEqual(p1, p2)
        finally:
            if old is None:
                os.environ.pop("DSH_WORKSPACE_DIR", None)
            else:
                os.environ["DSH_WORKSPACE_DIR"] = old


# ── 阶段四：学习词表 (mtime, words) 缓存 ──────────────────────
@unittest.skipIf(tf is None, "需要 uiautomator2（用工作区 venv 跑本测试）")
class TestDialogWordsCache(unittest.TestCase):
    """\u005f_load_learned_words 带 (mtime, words) 缓存，文件未变时不重读。"""

    def test_cache_hit_same_mtime(self):
        """同一文件连续两次调用，只读一次磁盘。"""
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "dialog_words.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump({"guide": ["ok1"], "allow": [], "deny": []}, f)
            # 指到临时文件
            old_file = tf.LEARNED_WORDS_FILE
            old_cache = tf._dialog_words_cache.copy()
            tf.LEARNED_WORDS_FILE = fp
            tf._dialog_words_cache = {"mtime": None, "words": None}
            try:
                t = object.__new__(tf.TestCase)
                w1 = t._load_learned_words()
                self.assertEqual(w1["guide"], ["ok1"])
                # 缓存已填充
                self.assertIsNotNone(tf._dialog_words_cache["mtime"])
                # 第二次调用应从缓存返回
                w2 = t._load_learned_words()
                self.assertEqual(w2["guide"], ["ok1"])
            finally:
                tf.LEARNED_WORDS_FILE = old_file
                tf._dialog_words_cache = old_cache

    def test_cache_invalidate_on_file_change(self):
        """文件 mtime 变化后缓存失效，重新读取。"""
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "dialog_words.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump({"guide": ["old"], "allow": [], "deny": []}, f)
            old_file = tf.LEARNED_WORDS_FILE
            old_cache = tf._dialog_words_cache.copy()
            tf.LEARNED_WORDS_FILE = fp
            tf._dialog_words_cache = {"mtime": None, "words": None}
            try:
                t = object.__new__(tf.TestCase)
                w1 = t._load_learned_words()
                self.assertEqual(w1["guide"], ["old"])
                # 改文件
                time.sleep(0.05)  # 确保 mtime 变化
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump({"guide": ["new"], "allow": [], "deny": []}, f)
                w2 = t._load_learned_words()
                self.assertEqual(w2["guide"], ["new"])
            finally:
                tf.LEARNED_WORDS_FILE = old_file
                tf._dialog_words_cache = old_cache


# ── 阶段四：结构化日志 _setup_logging ─────────────────────────
class TestSetupLogging(unittest.TestCase):
    """run_case._setup_logging() 创建日志文件并写入日志。"""

    def test_creates_log_file(self):
        with tempfile.TemporaryDirectory() as td:
            ws = os.path.join(td, "ws")
            os.makedirs(os.path.join(ws, "storage"))
            old = os.environ.get("DSH_WORKSPACE_DIR")
            os.environ["DSH_WORKSPACE_DIR"] = ws
            try:
                log_path = run_case._setup_logging()
                self.assertTrue(os.path.isfile(log_path))
                self.assertIn("run_", os.path.basename(log_path))
                # 日志文件含至少一行日志
                with open(log_path, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("日志文件", content)
            finally:
                if old is None:
                    os.environ.pop("DSH_WORKSPACE_DIR", None)
                else:
                    os.environ["DSH_WORKSPACE_DIR"] = old
                # 清理 logging handler 避免污染其它测试
                import logging
                for h in logging.getLogger().handlers[:]:
                    if isinstance(h, logging.FileHandler):
                        h.close()
                        logging.getLogger().removeHandler(h)


# ── db.py: list_cases 基本查询 ────────────────────────────
class TestListCasesBasic(unittest.TestCase):
    """list_cases 基本查询（迭代清理由 start_case mtime 检测处理）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._td.name, "test.db")
        self.rec = db.RecordDB(self.db_path)

    def tearDown(self):
        self.rec.close()
        self._td.cleanup()

    def _insert(self, name, sp, status="PASS"):
        cid = self.rec.start_case(name, device="test", script_path=sp)
        self.rec.finish_case(cid, f"/tmp/{name}_报告.md", f"1 通过 / 0 失败",
                             final_status=status)
        return cid

    def test_returns_all_records(self):
        self._insert("app_1", "/cases/com.a/1.py", "FAIL")
        self._insert("app_1", "/cases/com.a/1.py", "PASS")
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2)

    def test_no_script_path_kept(self):
        self._insert("manual_1", None, "PASS")
        self._insert("manual_2", None, "FAIL")
        recs = self.rec.list_cases()
        self.assertEqual(len(recs), 2)


# ── db.py: 迭代清理（mtime 信号）──────────────────────────────
class TestIterativeCleanup(unittest.TestCase):
    """start_case() 基于脚本 mtime 自动清理迭代旧记录。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._td.name, "test.db")
        self.rec = db.RecordDB(self.db_path)
        # 创建临时脚本文件（mtime 可控）
        self.script = os.path.join(self._td.name, "test_case.py")
        with open(self.script, "w") as f:
            f.write("# v1\n")

    def tearDown(self):
        self.rec.close()
        self._td.cleanup()

    def _run(self, status="PASS", device="dev1"):
        cid = self.rec.start_case("test", device=device, script_path=self.script)
        self.rec.finish_case(cid, f"/tmp/test_{cid}_报告.md",
                             "1 通过 / 0 失败", final_status=status)
        return cid

    def test_iterate_deletes_old(self):
        """脚本被改过 → 旧记录是迭代 → 删除。"""
        self._run("FAIL")          # 第 1 次
        # 模拟 Agent 改脚本
        time.sleep(1.1)  # 确保 mtime 差异（精度 1s）
        with open(self.script, "w") as f:
            f.write("# v2\n")
        self._run("PASS")          # 第 2 次
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 1, "迭代后应只留 1 条")
        self.assertEqual(all_recs[0]["status"], "PASS")

    def test_no_change_keeps_old(self):
        """脚本没改 → 有意复跑 → 保留。"""
        self._run("PASS")          # 第 1 次
        time.sleep(0.5)
        self._run("PASS")          # 第 2 次，脚本没动
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2, "脚本没改应保留全部")

    def test_different_device_keeps(self):
        """换设备 → 保留。"""
        self._run("PASS", device="devA")
        time.sleep(1.1)
        with open(self.script, "w") as f:
            f.write("# v2\n")
        self._run("PASS", device="devB")
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2, "换设备应保留")

    def test_suite_id_keeps(self):
        """套件记录 → 保留（suite_id 不为 NULL 不清理）。"""
        cid1 = self.rec.start_case("test", device="dev1", script_path=self.script,
                                   suite_id=42)
        self.rec.finish_case(cid1, "/tmp/r.md", "1/0", final_status="FAIL")
        time.sleep(1.1)
        with open(self.script, "w") as f:
            f.write("# v2\n")
        cid2 = self.rec.start_case("test", device="dev1", script_path=self.script,
                                   suite_id=43)
        self.rec.finish_case(cid2, "/tmp/r.md", "1/0", final_status="PASS")
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2, "套件记录应保留")

    def test_script_missing_no_cleanup(self):
        """脚本不存在 → 不清理（安全降级）。"""
        fake = os.path.join(self._td.name, "nonexistent.py")
        cid1 = self.rec.start_case("test", device="dev1", script_path=fake)
        self.rec.finish_case(cid1, "/tmp/r.md", "1/0", final_status="FAIL")
        cid2 = self.rec.start_case("test", device="dev1", script_path=fake)
        self.rec.finish_case(cid2, "/tmp/r.md", "1/0", final_status="PASS")
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 2, "脚本不存在时不应清理")

    def test_multiple_iterations_keep_only_latest(self):
        """连续 5 次迭代，始终只留最新一条。"""
        for i in range(5):
            time.sleep(1.1)
            with open(self.script, "w") as f:
                f.write(f"# v{i+1}\n")
            status = "FAIL" if i < 4 else "PASS"
            self._run(status)
        all_recs = self.rec.list_cases()
        self.assertEqual(len(all_recs), 1)
        self.assertEqual(all_recs[0]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
