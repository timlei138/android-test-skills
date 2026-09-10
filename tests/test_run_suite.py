#!/usr/bin/env python3
"""run_suite + run_case._parse_args 纯逻辑单测（不需要 Android 设备）。

覆盖：用例收集与包名过滤、退出码计算、聚合报告生成、设备断连熔断、
run_case --device 参数解析（L231 name 覆盖回归）。

运行（skill 包根目录）：
    python -m unittest tests.test_run_suite -v
"""
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

import db         # noqa: E402
import run_case    # noqa: E402
import run_suite   # noqa: E402


# ── run_case._parse_args 回归测试（L231 name 覆盖 bug）──────────────
class TestParseArgs(unittest.TestCase):
    """_parse_args 必须正确解析 --device 参数，不被 sys.argv 覆盖。"""

    def test_bare_name(self):
        name, device = run_case._parse_args(["prog", "172.py"])
        self.assertEqual(name, "172.py")
        self.assertIsNone(device)

    def test_device_before_name(self):
        name, device = run_case._parse_args(
            ["prog", "--device", "ABC123", "com.zui.calendar/172.py"])
        self.assertEqual(name, "com.zui.calendar/172.py")
        self.assertEqual(device, "ABC123")

    def test_device_after_name(self):
        name, device = run_case._parse_args(
            ["prog", "172.py", "--device", "SERIAL_X"])
        self.assertEqual(name, "172.py")
        self.assertEqual(device, "SERIAL_X")

    def test_no_args_exits(self):
        with self.assertRaises(SystemExit):
            run_case._parse_args(["prog"])


# ── _collect_cases：用例收集与包名过滤 ──────────────────────────────
class TestCollectCases(unittest.TestCase):
    def setUp(self):
        self._old_dirs_rc = run_case.CASE_DIRS
        self._old_dirs_rs = run_suite.CASE_DIRS
        self._td = tempfile.TemporaryDirectory()
        root = self._td.name
        # 建两个包的用例目录
        for pkg in ("com.a.x", "com.b.y"):
            os.makedirs(os.path.join(root, "cases", pkg))
        for rel in ("com.a.x/1.py", "com.a.x/2.py",
                    "com.b.y/3.py", "com.a.x/_flow.py"):
            p = os.path.join(root, "cases", *rel.split("/"))
            with open(p, "w", encoding="utf-8") as f:
                f.write("def run():\n    pass\n")
        new_dirs = [os.path.join(root, "cases")]
        run_case.CASE_DIRS = new_dirs
        run_suite.CASE_DIRS = new_dirs   # from-import 创建了独立引用

    def tearDown(self):
        run_case.CASE_DIRS = self._old_dirs_rc
        run_suite.CASE_DIRS = self._old_dirs_rs
        self._td.cleanup()

    def test_all_cases(self):
        cases = run_suite._collect_cases(package=None)
        names = [rel.replace("\\", "/") for _, rel in cases]
        # _flow.py 被排除（_ 开头），应只有 3 个用例
        self.assertEqual(len(cases), 3)
        self.assertIn("com.a.x/1.py", names)
        self.assertIn("com.b.y/3.py", names)

    def test_package_filter(self):
        cases = run_suite._collect_cases(package="com.a.x")
        self.assertEqual(len(cases), 2)
        for _, rel in cases:
            self.assertTrue(rel.replace("\\", "/").startswith("com.a.x/"))

    def test_package_filter_no_match(self):
        cases = run_suite._collect_cases(package="com.nonexistent")
        self.assertEqual(len(cases), 0)


# ── _suite_exit_code：退出码计算 ─────────────────────────────────────
class TestSuiteExitCode(unittest.TestCase):
    def test_all_pass(self):
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 5, "warn": 0, "fail": 0, "blocked": 0, "error": 0}), 0)

    def test_any_fail(self):
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 3, "warn": 0, "fail": 1, "blocked": 0, "error": 0}), 1)

    def test_no_fail_with_error(self):
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 3, "warn": 0, "fail": 0, "blocked": 0, "error": 2}), 3)

    def test_only_blocked(self):
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 0, "warn": 0, "fail": 0, "blocked": 3, "error": 0}), 2)

    def test_fail_takes_priority_over_error(self):
        """FAIL 存在时退出码 = 1，即使也有 ERROR。"""
        self.assertEqual(run_suite._suite_exit_code(
            {"pass": 0, "warn": 0, "fail": 1, "blocked": 0, "error": 1}), 1)


# ── _generate_report：聚合报告生成 ──────────────────────────────────
class TestGenerateReport(unittest.TestCase):
    def test_report_content(self):
        with tempfile.TemporaryDirectory() as td:
            started = time.time() - 60
            results = [
                ("com.a.x/1.py", "PASS", 12.3, 0),
                ("com.a.x/2.py", "FAIL", 8.1, 1),
                ("com.b.y/3.py", "ERROR", 3.0, 3),
            ]
            counts = {"pass": 1, "warn": 0, "fail": 1, "blocked": 0,
                      "error": 1, "skipped": 0}
            path = run_suite._generate_report(
                results, counts, started, "package=com.a.x", td)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            # 关键内容断言
            self.assertIn("套件报告", content)
            self.assertIn("com.a.x/1.py", content)
            self.assertIn("FAIL", content)
            self.assertIn("ERROR", content)
            self.assertIn("package=com.a.x", content)

    def test_report_with_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            results = [("com.a.x/1.py", "ERROR", 5.0, 3)]
            counts = {"pass": 0, "warn": 0, "fail": 0, "blocked": 0,
                      "error": 1, "skipped": 4}
            path = run_suite._generate_report(
                results, counts, time.time(), "全量", td)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("未执行", content)
            self.assertIn("4", content)


# ── _device_online：设备在线检测（mock adb）──────────────────────────
class TestDeviceOnline(unittest.TestCase):
    def test_serial_found(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\nABC123\tdevice\nDEF456\toffline\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertTrue(run_suite._device_online("ABC123"))

    def test_serial_not_found(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\nDEF456\tdevice\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertFalse(run_suite._device_online("ABC123"))

    def test_serial_offline(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\nABC123\toffline\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertFalse(run_suite._device_online("ABC123"))

    def test_no_serial_any_device(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\nXYZ\tdevice\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertTrue(run_suite._device_online(None))

    def test_no_serial_no_device(self):
        fake = mock.MagicMock()
        fake.stdout = "List of devices attached\n"
        with mock.patch("subprocess.run", return_value=fake):
            self.assertFalse(run_suite._device_online(None))

    def test_adb_failure(self):
        with mock.patch("subprocess.run", side_effect=OSError("adb not found")):
            self.assertFalse(run_suite._device_online())


if __name__ == "__main__":
    unittest.main()


# ── db.case_history + db.flaky_stats：flakiness 查询 ──────────────────
class TestFlakiness(unittest.TestCase):
    """构造临时 SQLite 内存库，测试 case_history / flaky_stats 查询逻辑。"""

    def _mk_db(self):
        """建一个独立的 RecordDB 实例（内存库），不影响全局单例。"""
        d = db.RecordDB(":memory:")
        d._connect()  # 触发 schema 创建 + migration
        return d

    def _insert(self, d, script_path, status, started, finished, package=None):
        """插入一条用例记录（绕过 start_case/finish_case 简化构造）。"""
        with d._lock:
            conn = d._connect()
            conn.execute(
                "INSERT INTO cases (name, device, started_at, finished_at,"
                " script_path, final_status, package)"
                " VALUES (?,?,?,?,?,?,?)",
                ("test", "dev", started, finished, script_path, status, package))
            conn.commit()

    def test_case_history_order_and_limit(self):
        """同一脚本的 10 条记录，新→旧排序，limit=5 只返回最近 5 条。"""
        d = self._mk_db()
        sp = "/cases/com.x/1.py"
        for i in range(10):
            self._insert(d, sp, "PASS" if i % 2 == 0 else "FAIL",
                         f"2026-09-0{i%9+1}T10:00:00",
                         f"2026-09-0{i%9+1}T10:01:00")
        hist = d.case_history(sp, limit=5)
        self.assertEqual(len(hist), 5)
        # 最新的是 id=10，应该在第一个
        self.assertEqual(hist[0]["id"], 10)
        self.assertGreater(hist[0]["id"], hist[1]["id"])

    def test_case_history_excludes_empty_status(self):
        """final_status 为空或 NULL 的记录不出现在历史中。"""
        d = self._mk_db()
        sp = "/cases/com.x/2.py"
        self._insert(d, sp, "PASS", "2026-09-01T10:00:00", "2026-09-01T10:01:00")
        # 插入一条 final_status 为空的（模拟未完成的记录）
        with d._lock:
            d._connect().execute(
                "INSERT INTO cases (name, started_at, finished_at, script_path)"
                " VALUES (?,?,?,?)",
                ("test", "2026-09-02T10:00:00", "2026-09-02T10:01:00", sp))
            d._local.conn.commit()
        hist = d.case_history(sp)
        self.assertEqual(len(hist), 1)  # 只有 PASS 那条

    def test_flaky_stats_pass_rate(self):
        """6 PASS + 4 FAIL = 60% 通过率，flaky=True（5<=runs 且 0.2<0.6<0.8）。"""
        d = self._mk_db()
        sp = "/cases/com.x/3.py"
        for i in range(10):
            status = "PASS" if i < 6 else "FAIL"
            self._insert(d, sp, status,
                         f"2026-09-01T10:0{i}:00",
                         f"2026-09-01T10:0{i}:30", "com.x")
        stats = d.flaky_stats(min_runs=5)
        self.assertEqual(len(stats), 1)
        s = stats[0]
        self.assertEqual(s["runs"], 10)
        self.assertAlmostEqual(s["pass_rate"], 0.6, places=2)
        self.assertTrue(s["flaky"])

    def test_flaky_stats_warn_counts_as_pass(self):
        """WARN 算通过：4 PASS + 2 WARN + 4 FAIL = 60%。"""
        d = self._mk_db()
        sp = "/cases/com.x/4.py"
        statuses = ["PASS"]*4 + ["WARN"]*2 + ["FAIL"]*4
        for i, st in enumerate(statuses):
            self._insert(d, sp, st,
                         f"2026-09-01T10:{i:02d}:00",
                         f"2026-09-01T10:{i:02d}:30")
        stats = d.flaky_stats(min_runs=5)
        self.assertEqual(len(stats), 1)
        self.assertAlmostEqual(stats[0]["pass_rate"], 0.6, places=2)

    def test_flaky_stats_below_min_runs_not_flaky(self):
        """4 条记录（< min_runs=5）→ flaky=False（样本不足不误报）。"""
        d = self._mk_db()
        sp = "/cases/com.x/5.py"
        for i in range(4):
            status = "PASS" if i < 2 else "FAIL"
            self._insert(d, sp, status,
                         f"2026-09-01T10:0{i}:00",
                         f"2026-09-01T10:0{i}:30")
        stats = d.flaky_stats(min_runs=5)
        self.assertEqual(len(stats), 1)
        self.assertFalse(stats[0]["flaky"])

    def test_flaky_stats_all_pass_not_flaky(self):
        """100% 通过率 → flaky=False。"""
        d = self._mk_db()
        sp = "/cases/com.x/6.py"
        for i in range(6):
            self._insert(d, sp, "PASS",
                         f"2026-09-01T10:0{i}:00",
                         f"2026-09-01T10:0{i}:30")
        stats = d.flaky_stats(min_runs=5)
        self.assertEqual(len(stats), 1)
        self.assertAlmostEqual(stats[0]["pass_rate"], 1.0)
        self.assertFalse(stats[0]["flaky"])


# ── db.card_freshness：知识卡新鲜度查询 ────────────────────────────────
class TestCardFreshness(unittest.TestCase):
    """测试 card_freshness() 的入库与查询逻辑。"""

    def _mk_db(self):
        d = db.RecordDB(":memory:")
        d._connect()
        return d

    def _insert(self, d, script_path, status, started, finished, package=None):
        with d._lock:
            conn = d._connect()
            conn.execute(
                "INSERT INTO cases (name, device, started_at, finished_at,"
                " script_path, final_status, package)"
                " VALUES (?,?,?,?,?,?,?)",
                ("test", "dev", started, finished, script_path, status, package))
            conn.commit()

    def test_basic_pass_record(self):
        """有 PASS 记录的包 → last_pass_at 非空。"""
        d = self._mk_db()
        self._insert(d, "/c/com.x/1.py", "PASS",
                     "2026-09-01T10:00:00", "2026-09-01T10:01:00", "com.x")
        rows = d.card_freshness()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["package"], "com.x")
        self.assertIsNotNone(r["last_pass_at"])
        self.assertIn("2026-09-01", r["last_pass_at"])
        self.assertEqual(r["runs"], 1)

    def test_warn_not_counted_as_pass(self):
        """WARN 不算验证通过 → last_pass_at 为 None（从未 PASS）。"""
        d = self._mk_db()
        self._insert(d, "/c/com.y/1.py", "WARN",
                     "2026-09-01T10:00:00", "2026-09-01T10:01:00", "com.y")
        self._insert(d, "/c/com.y/2.py", "FAIL",
                     "2026-09-02T10:00:00", "2026-09-02T10:01:00", "com.y")
        rows = d.card_freshness()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertIsNone(r["last_pass_at"])
        self.assertEqual(r["runs"], 2)  # 两条都算执行次数

    def test_never_passed_package(self):
        """只有 FAIL 的包 → last_pass_at=None，runs 正确。"""
        d = self._mk_db()
        for i in range(3):
            self._insert(d, f"/c/com.z/{i}.py", "FAIL",
                         f"2026-09-0{i+1}T10:00:00",
                         f"2026-09-0{i+1}T10:01:00", "com.z")
        rows = d.card_freshness()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["last_pass_at"])
        self.assertEqual(rows[0]["runs"], 3)

    def test_multiple_packages(self):
        """多个包各自独立统计。"""
        d = self._mk_db()
        self._insert(d, "/c/com.a/1.py", "PASS",
                     "2026-09-01T10:00:00", "2026-09-01T10:01:00", "com.a")
        self._insert(d, "/c/com.b/1.py", "FAIL",
                     "2026-09-02T10:00:00", "2026-09-02T10:01:00", "com.b")
        rows = d.card_freshness()
        pkgs = {r["package"]: r for r in rows}
        self.assertIn("com.a", pkgs)
        self.assertIn("com.b", pkgs)
        self.assertIsNotNone(pkgs["com.a"]["last_pass_at"])
        self.assertIsNone(pkgs["com.b"]["last_pass_at"])

    def test_last_pass_id_matches_latest(self):
        """last_pass_id 指向最近的 PASS 记录（而非最早的）。"""
        d = self._mk_db()
        self._insert(d, "/c/com.p/1.py", "PASS",
                     "2026-09-01T10:00:00", "2026-09-01T10:01:00", "com.p")
        self._insert(d, "/c/com.p/1.py", "FAIL",
                     "2026-09-02T10:00:00", "2026-09-02T10:01:00", "com.p")
        self._insert(d, "/c/com.p/1.py", "PASS",
                     "2026-09-03T10:00:00", "2026-09-03T10:01:00", "com.p")
        rows = d.card_freshness()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        # 最近 PASS 是第 3 条（id=3）
        self.assertEqual(r["last_pass_id"], 3)
        self.assertIn("2026-09-03", r["last_pass_at"])
        self.assertEqual(r["runs"], 3)
