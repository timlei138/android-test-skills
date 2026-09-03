#!/usr/bin/env python3
"""
测试记录 SQLite 持久化：用例/步骤/断言结果/证据入库。
零依赖（标准库 sqlite3）。

数据库位置（按优先级）：
  1. 环境变量 DSH_ANDROID_TEST_DIR（测试工作区根，其下 test_records.db）
  2. 默认 ~/dsh-android-test/test_records.db

显式定位而非相对路径推导：本模块可能在 skill 包或工作区任意位置被加载，
只有显式路径才能保证读的是同一个库。
"""
import json
import os
import sqlite3
import threading
from datetime import datetime


def default_test_dir() -> str:
    """测试工作区根目录：环境变量 > 默认 ~/dsh-android-test。"""
    env = os.environ.get("DSH_ANDROID_TEST_DIR")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    return os.path.join(os.path.expanduser("~"), "dsh-android-test")


def default_db_path() -> str:
    """测试记录数据库路径。"""
    return os.path.join(default_test_dir(), "test_records.db")


DB_PATH = default_db_path()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  device TEXT,
  started_at TEXT,
  finished_at TEXT,
  report_path TEXT,
  summary TEXT,
  user_input TEXT,
  script_path TEXT
);
CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id INTEGER NOT NULL REFERENCES cases(id),
  name TEXT,
  ord INTEGER
);
CREATE TABLE IF NOT EXISTS results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id INTEGER NOT NULL REFERENCES steps(id),
  result TEXT,
  detail TEXT,
  state_json TEXT,
  evidence TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS step_evidences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id INTEGER NOT NULL REFERENCES steps(id),
  evidence TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS step_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id INTEGER NOT NULL REFERENCES steps(id),
  action TEXT,
  detail TEXT,
  duration_ms INTEGER,
  created_at TEXT
);
"""

# 旧库迁移：为早期建的表补列（幂等；列已存在时 ALTER 抛错，忽略即可）
_MIGRATIONS = [
    "ALTER TABLE cases ADD COLUMN user_input TEXT",
    "ALTER TABLE cases ADD COLUMN script_path TEXT",
    # final_status：用例最终结论（PASS/FAIL/BLOCKED/WARN/ERROR），由框架显式写入。
    # 老库没有此列时，列表查询回退到摘要文本推断（仅兼容历史数据，新数据不再推断）。
    "ALTER TABLE cases ADD COLUMN final_status TEXT",
]


def _artifact_roots():
    """运行产物目录（截图/报告）——文件读写/删除只允许落在这两棵子树里。"""
    base = os.path.join(default_test_dir(), "storage")
    return [os.path.realpath(os.path.join(base, "screenshots")),
            os.path.realpath(os.path.join(base, "reports"))]


def is_artifact_path(path):
    """path 是否位于运行产物目录内（realpath 解符号链接 + commonpath 校验）。"""
    if not path or not os.path.isabs(path):
        return False
    try:
        full = os.path.realpath(path)
    except OSError:
        return False
    for root in _artifact_roots():
        try:
            if os.path.commonpath([full, root]) == root:
                return True
        except ValueError:       # 跨盘符（Windows）
            continue
    return False


class RecordDB:
    """线程安全的测试记录库（单连接 + 锁）。"""

    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        # 线程本地连接：测试框架和 Web UI 可能在不同线程使用同一实例，
        # 每个线程独立持有连接可彻底规避 "SQLite objects created in one thread" 错误。
        self._local = threading.local()

    def _connect(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.path, check_same_thread=False)
            self._local.conn.executescript(_SCHEMA)
            # 幂等迁移：旧库补列（列已存在时 ALTER 抛错，忽略即可）
            for stmt in _MIGRATIONS:
                try:
                    self._local.conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            self._local.conn.commit()
        return self._local.conn

    def close(self):
        with self._lock:
            if hasattr(self._local, 'conn') and self._local.conn is not None:
                self._local.conn.close()
                self._local.conn = None

    # ── cases ────────────────────────────────────────────────────────
    def start_case(self, name, device, started_at=None, user_input=None, script_path=None):
        with self._lock:
            cur = self._connect().cursor()
            cur.execute(
                "INSERT INTO cases (name, device, started_at, user_input, script_path)"
                " VALUES (?,?,?,?,?)",
                (name, device, started_at or datetime.now().isoformat(timespec="seconds"),
                 user_input, script_path))
            self._local.conn.commit()
            return cur.lastrowid

    def finish_case(self, case_id, report_path, summary, final_status=None,
                    finished_at=None):
        with self._lock:
            self._connect().execute(
                "UPDATE cases SET finished_at=?, report_path=?, summary=?,"
                " final_status=? WHERE id=?",
                (finished_at or datetime.now().isoformat(timespec="seconds"),
                 report_path, summary, final_status, case_id))
            self._local.conn.commit()

    # ── steps ────────────────────────────────────────────────────────
    def add_step(self, case_id, name, ord_):
        with self._lock:
            cur = self._connect().cursor()
            cur.execute("INSERT INTO steps (case_id, name, ord) VALUES (?,?,?)",
                        (case_id, name, ord_))
            self._local.conn.commit()
            return cur.lastrowid

    # ── results ──────────────────────────────────────────────────────
    def add_step_evidence(self, step_id, evidence):
        with self._lock:
            self._connect().execute(
                "INSERT INTO step_evidences (step_id, evidence, created_at) VALUES (?,?,?)",
                (step_id, evidence, datetime.now().isoformat(timespec="seconds")))
            self._local.conn.commit()

    def add_step_action(self, step_id, action, detail=None, duration_ms=0):
        with self._lock:
            self._connect().execute(
                "INSERT INTO step_actions (step_id, action, detail, duration_ms, created_at)"
                " VALUES (?,?,?,?,?)",
                (step_id, action, detail, duration_ms,
                 datetime.now().isoformat(timespec="seconds")))
            self._local.conn.commit()

    def add_result(self, step_id, result, detail, state=None, evidence=None):
        with self._lock:
            self._connect().execute(
                "INSERT INTO results (step_id, result, detail, state_json, evidence, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (step_id, result, detail,
                 json.dumps(state, ensure_ascii=False) if state else None,
                 evidence, datetime.now().isoformat(timespec="seconds")))
            self._local.conn.commit()

    # ── 查询（供前端/报告用）────────────────────────────────────────
    def list_cases(self, limit=50, include_internal=False):
        """列出测试记录。

        默认隐藏内部记录（名称以 _ 开头：__probe__ / __states_probe__ 等
        框架探针与调试用例），它们是执行过程的中间数据，不是真用例。
        include_internal=True 时全量返回（命令行排查用）。
        """
        with self._lock:
            cur = self._connect().cursor()
            sql = ("SELECT id, name, device, started_at, finished_at, report_path, summary,"
                   " user_input, script_path,"
                   " CASE WHEN started_at IS NOT NULL AND finished_at IS NOT NULL THEN"
                   "  ROUND((julianday(finished_at) - julianday(started_at)) * 86400, 1)"
                   " ELSE NULL END as duration_seconds,"
                   # 新数据读 final_status 列；老数据（该列为空）回退摘要文本推断
                   " CASE WHEN final_status IS NOT NULL AND final_status <> ''"
                   "      THEN final_status"
                   "      WHEN summary LIKE '% 0 失败%' THEN 'PASS'"
                   "      WHEN summary LIKE '% 失败%' THEN 'FAIL'"
                   "      ELSE 'UNKNOWN' END as status"
                   " FROM cases")
            params = []
            if not include_internal:
                sql += " WHERE name NOT LIKE '\\_%' ESCAPE '\\'"
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            cols = ["id", "name", "device", "started_at", "finished_at",
                    "report_path", "summary", "user_input", "script_path",
                    "duration_seconds", "status"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def delete_case(self, case_id, remove_artifacts=False):
        """删除用例记录，级联删除 steps/results/evidences/actions。

        remove_artifacts=True 时连带删除磁盘产物：
        - 报告（report_path）及其同前缀的时间戳备份（<name>_<ts>_报告.md）
        - 证据截图（results.evidence / step_evidences.evidence 引用的文件）
        - 截图目录：该 case 引用截图所在的 case_* 目录若不再被其它记录
          引用，整目录删除
        返回删除的文件/目录数（失败不阻塞，记录仍会删除）。
        """
        removed = 0
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            # 收集磁盘产物路径（删库行之前取，否则查不到）
            cur.execute("SELECT report_path FROM cases WHERE id=?", (case_id,))
            row = cur.fetchone()
            report_path = row[0] if row else None
            cur.execute(
                "SELECT evidence FROM step_evidences WHERE step_id IN"
                " (SELECT id FROM steps WHERE case_id=?)", (case_id,))
            evidence_paths = [r[0] for r in cur.fetchall() if r[0]]
            cur.execute(
                "SELECT evidence FROM results WHERE step_id IN"
                " (SELECT id FROM steps WHERE case_id=?) AND evidence IS NOT NULL",
                (case_id,))
            evidence_paths += [r[0] for r in cur.fetchall() if r[0]]
            # 删库行
            cur.execute("DELETE FROM results WHERE step_id IN"
                        " (SELECT id FROM steps WHERE case_id=?)", (case_id,))
            cur.execute("DELETE FROM step_evidences WHERE step_id IN"
                        " (SELECT id FROM steps WHERE case_id=?)", (case_id,))
            cur.execute("DELETE FROM step_actions WHERE step_id IN"
                        " (SELECT id FROM steps WHERE case_id=?)", (case_id,))
            cur.execute("DELETE FROM steps WHERE case_id=?", (case_id,))
            cur.execute("DELETE FROM cases WHERE id=?", (case_id,))
            conn.commit()

        if not remove_artifacts:
            return 0

        # ── 磁盘产物删除（锁外执行，文件 IO 不阻塞其它记录操作）──
        import shutil
        shot_dirs = set()
        for p in evidence_paths:
            if not is_artifact_path(p):   # 只删运行产物目录内的文件
                continue
            try:
                parent = os.path.dirname(os.path.abspath(p))
                # case_* 目录（一次执行一个目录）按目录删，其它散文件按文件删
                if os.path.basename(parent).startswith("case_"):
                    shot_dirs.add(parent)
                elif os.path.isfile(p):
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
        for d in shot_dirs:
            if not is_artifact_path(d):   # 整目录删除前同样过白名单
                continue
            # 不再被其它记录引用 → 整目录删；仍被引用 → 只删本 case 引用的文件
            if self._dir_referenced_elsewhere(d, exclude_case=case_id):
                for p in {e for e in evidence_paths
                          if os.path.dirname(os.path.abspath(e)) == d}:
                    try:
                        if os.path.isfile(p):
                            os.remove(p)
                            removed += 1
                    except OSError:
                        pass
            else:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        # 报告：主报告 + 同前缀时间戳备份（联想日历_168_20260902_151054_报告.md）
        if report_path:
            rp = os.path.abspath(report_path)
            rdir = os.path.dirname(rp)
            base = os.path.basename(rp)
            stem = base[:-len("_报告.md")] if base.endswith("_报告.md") else base
            candidates = {rp}
            try:
                for f in os.listdir(rdir):
                    if f.startswith(stem) and f.endswith("_报告.md"):
                        candidates.add(os.path.join(rdir, f))
            except OSError:
                pass
            for p in candidates:
                if not is_artifact_path(p):
                    continue
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                        removed += 1
                except OSError:
                    pass
        return removed

    def _dir_referenced_elsewhere(self, shot_dir, exclude_case):
        """检查截图目录是否被除 exclude_case 外的其它记录引用。

        在 delete_case 的锁外调用，内部短暂重新加锁。
        """
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            like = shot_dir.rstrip(os.sep) + os.sep + "%"
            cur.execute(
                "SELECT COUNT(*) FROM step_evidences WHERE evidence LIKE ?"
                " AND step_id IN (SELECT id FROM steps WHERE case_id != ?)",
                (like, exclude_case))
            n1 = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM results WHERE evidence LIKE ?"
                " AND step_id IN (SELECT id FROM steps WHERE case_id != ?)",
                (like, exclude_case))
            n2 = cur.fetchone()[0]
            return (n1 + n2) > 0

    def get_case(self, case_id):
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("SELECT id, name, device, started_at, finished_at, report_path, summary,"
                        " user_input, script_path, final_status"
                        " FROM cases WHERE id=?", (case_id,))
            row = cur.fetchone()
            if not row:
                return None
            case = dict(zip(["id", "name", "device", "started_at", "finished_at",
                             "report_path", "summary", "user_input", "script_path",
                             "final_status"], row))
            cur.execute("SELECT id, name, ord FROM steps WHERE case_id=? ORDER BY ord", (case_id,))
            steps = []
            for sid, sname, sord in cur.fetchall():
                cur.execute("SELECT result, detail, state_json, evidence FROM results"
                            " WHERE step_id=? ORDER BY id", (sid,))
                res_rows = cur.fetchall()
                results = []
                for r, d, st, ev in res_rows:
                    entry = {"result": r, "detail": d}
                    if st:
                        try:
                            entry["state"] = json.loads(st)
                        except ValueError:
                            pass
                    if ev:
                        entry["evidence"] = ev
                    results.append(entry)
                cur.execute("SELECT evidence FROM step_evidences"
                            " WHERE step_id=? ORDER BY id", (sid,))
                evidences = [row[0] for row in cur.fetchall() if row[0]]
                cur.execute("SELECT action, detail, duration_ms, created_at FROM step_actions"
                            " WHERE step_id=? ORDER BY id", (sid,))
                actions = [{"action": a, "detail": d, "duration_ms": dur, "created_at": ca}
                           for a, d, dur, ca in cur.fetchall()]
                steps.append({"id": sid, "name": sname, "results": results,
                              "evidences": evidences, "actions": actions})
            case["steps"] = steps
            return case


# 全局单例（供框架懒加载）
_db_singleton = None


def get_db():
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = RecordDB()
    return _db_singleton


if __name__ == "__main__":
    db = get_db()
    for c in db.list_cases(5):
        print(f"#{c['id']} {c['name']} {c['started_at']} {c['summary']}")
