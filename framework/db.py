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
"""

# 旧库迁移：为早期建的表补 user_input / script_path 列（幂等）
_MIGRATIONS = [
    "ALTER TABLE cases ADD COLUMN user_input TEXT",
    "ALTER TABLE cases ADD COLUMN script_path TEXT",
]


class RecordDB:
    """线程安全的测试记录库（单连接 + 锁）。"""

    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._conn = None

    def _connect(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.executescript(_SCHEMA)
            # 幂等迁移：旧库补列（列已存在时 ALTER 抛错，忽略即可）
            for stmt in _MIGRATIONS:
                try:
                    self._conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            self._conn.commit()
        return self._conn

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── cases ────────────────────────────────────────────────────────
    def start_case(self, name, device, started_at=None, user_input=None, script_path=None):
        with self._lock:
            cur = self._connect().cursor()
            cur.execute(
                "INSERT INTO cases (name, device, started_at, user_input, script_path)"
                " VALUES (?,?,?,?,?)",
                (name, device, started_at or datetime.now().isoformat(timespec="seconds"),
                 user_input, script_path))
            self._conn.commit()
            return cur.lastrowid

    def finish_case(self, case_id, report_path, summary, finished_at=None):
        with self._lock:
            self._connect().execute(
                "UPDATE cases SET finished_at=?, report_path=?, summary=? WHERE id=?",
                (finished_at or datetime.now().isoformat(timespec="seconds"),
                 report_path, summary, case_id))
            self._conn.commit()

    # ── steps ────────────────────────────────────────────────────────
    def add_step(self, case_id, name, ord_):
        with self._lock:
            cur = self._connect().cursor()
            cur.execute("INSERT INTO steps (case_id, name, ord) VALUES (?,?,?)",
                        (case_id, name, ord_))
            self._conn.commit()
            return cur.lastrowid

    # ── results ──────────────────────────────────────────────────────
    def add_result(self, step_id, result, detail, state=None, evidence=None):
        with self._lock:
            self._connect().execute(
                "INSERT INTO results (step_id, result, detail, state_json, evidence, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (step_id, result, detail,
                 json.dumps(state, ensure_ascii=False) if state else None,
                 evidence, datetime.now().isoformat(timespec="seconds")))
            self._conn.commit()

    # ── 查询（供前端/报告用）────────────────────────────────────────
    def list_cases(self, limit=50):
        with self._lock:
            cur = self._connect().cursor()
            cur.execute("SELECT id, name, device, started_at, finished_at, report_path, summary,"
                        " user_input, script_path"
                        " FROM cases ORDER BY id DESC LIMIT ?", (limit,))
            cols = ["id", "name", "device", "started_at", "finished_at",
                    "report_path", "summary", "user_input", "script_path"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_case(self, case_id):
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("SELECT id, name, device, started_at, finished_at, report_path, summary,"
                        " user_input, script_path"
                        " FROM cases WHERE id=?", (case_id,))
            row = cur.fetchone()
            if not row:
                return None
            case = dict(zip(["id", "name", "device", "started_at", "finished_at",
                             "report_path", "summary", "user_input", "script_path"], row))
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
                steps.append({"id": sid, "name": sname, "results": results})
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
