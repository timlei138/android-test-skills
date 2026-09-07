#!/usr/bin/env python3
"""
测试记录 SQLite 持久化：用例/步骤/断言结果/证据入库。
零依赖（标准库 sqlite3）。

数据库位置（按优先级）：
  1. 环境变量 DSH_ANDROID_TEST_DIR（测试工作区根，其下 storage/test_records.db）
  2. 默认 ~/dsh-android-test/storage/test_records.db

显式定位而非相对路径推导：本模块可能在 skill 包或工作区任意位置被加载，
只有显式路径才能保证读的是同一个库。
"""
import json
import os
import re
import sqlite3
import threading
from datetime import datetime


def default_test_dir() -> str:
    """测试工作区根目录：环境变量 > 默认 ~/dsh-android-test。"""
    env = os.environ.get("DSH_ANDROID_TEST_DIR")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    return os.path.join(os.path.expanduser("~"), "dsh-android-test")


# ── 容错删除 ────────────────────────────────────────────────────────
# 判定标准是「磁盘上还在不在」，而不是「有没有抛异常」。
# 原因：某些运行环境会给 Python 注入「安全删除」shim，把 os.remove 改走
# 系统回收站。这类 shim 有两种误报：
#   1) 文件确实已经删掉了，但回收站二次确认返回 0x2（文件不存在）→ 照样抛异常；
#   2) 一次删除数量超过阈值时直接抛错要求人工确认（FAIL_CLOSED）。
# 两种情况下异常都不代表删除失败，只有「文件还在」才算失败。
def safe_remove(path) -> bool:
    """删除单个文件。返回是否删除成功（原本就不存在 → False，不算错误）。"""
    try:
        if not os.path.isfile(path):
            return False
    except OSError:
        return False
    try:
        os.remove(path)
        return True
    except Exception:
        try:
            if not os.path.exists(path):   # 抛了异常但文件没了 → 实际删成功
                return True
        except OSError:
            pass
        return False


def safe_rmtree(path) -> bool:
    """删除目录树。返回是否删除成功（原本就不存在 → False，不算错误）。"""
    import shutil
    try:
        if not os.path.isdir(path):
            return False
    except OSError:
        return False
    try:
        shutil.rmtree(path)
        return True
    except Exception:
        try:
            if not os.path.exists(path):
                return True
        except OSError:
            pass
        return False


def default_db_path() -> str:
    """测试记录数据库路径：<工作区>/storage/test_records.db（与截图/报告同区）。"""
    return os.path.join(default_test_dir(), "storage", "test_records.db")


def _migrate_legacy_db():
    """旧布局迁移：test_records.db 曾放在工作区根，统一挪进 storage/。

    新路径已存在 → 什么都不做（幂等）。旧文件搬不动（如被运行中的 Web UI
    锁住）→ 退化为复制：老连接继续用旧文件，新连接用副本——宁可暂时双份，
    也不能让新路径开出一个空库、历史记录"看起来丢了"。
    """
    import shutil
    old = os.path.join(default_test_dir(), "test_records.db")
    new = default_db_path()
    if not os.path.isfile(old) or os.path.isfile(new):
        return
    try:
        os.makedirs(os.path.dirname(new), exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            src = old + suffix
            if os.path.isfile(src):
                shutil.move(src, new + suffix)
        print(f"[db] 已迁移旧库: {old} → {new}")
    except OSError as e:
        print(f"⚠️ [db] 旧库迁移失败，退化为复制（原文件保留）: {e}")
        try:
            shutil.copy2(old, new)
        except OSError as e2:
            print(f"⚠️ [db] 旧库复制也失败: {e2}（旧文件仍在 {old}，可手动迁移）")


DB_PATH = default_db_path()
_migrate_legacy_db()

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
    # package：被测 App 包名。报告丢了可以重建，但包名只写在报告里 ——
    # 不入库的话重建出来的报告这一栏就是空的。
    "ALTER TABLE cases ADD COLUMN package TEXT",
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
            # sqlite 无法在不存在的目录里建库（CANTOPEN: unable to open
            # database file）。库在 storage/ 子目录下，而 storage/ 只有用例
            # 跑过才会创建——纯 Web UI / 首次使用 / HOME 被重定向的环境里
            # 它可能不存在，这里兜底建目录。
            parent = os.path.dirname(os.path.abspath(self.path))
            if not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
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
                    finished_at=None, package=None):
        """收尾一条记录。

        package 是被测 App 包名 —— 以前只写进报告文件，报告丢了就跟着丢；
        现在入库，重建报告时能原样还原。
        """
        with self._lock:
            if package:
                self._connect().execute(
                    "UPDATE cases SET finished_at=?, report_path=?, summary=?,"
                    " final_status=?, package=? WHERE id=?",
                    (finished_at or datetime.now().isoformat(timespec="seconds"),
                     report_path, summary, final_status, package, case_id))
            else:
                # 没拿到包名就别把已有的覆盖成 NULL
                self._connect().execute(
                    "UPDATE cases SET finished_at=?, report_path=?, summary=?,"
                    " final_status=? WHERE id=?",
                    (finished_at or datetime.now().isoformat(timespec="seconds"),
                     report_path, summary, final_status, case_id))
            self._local.conn.commit()

    def backfill_package(self, dry_run=False):
        """给历史记录补 package 列。

        背景：package 列是后加的，加之前跑的记录该列为空。好在 script_path
        是 `.../cases/<包名>/<脚本>.py` 结构，目录名就是包名，可以直接反推。

        只认「长得像包名」的目录名（纯 ASCII、含点、无空白），像
        `cases/联想日历_168.py` 这种不是包名的一律跳过 —— 宁可留空也不猜。

        dry_run=True 时只报告不写库。返回 [(case_id, package), ...]。
        """
        # com.zui.calendar / com.tencent.mm 这类：段首小写字母，段内字母数字下划线
        pkg_re = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
        with self._lock:
            cur = self._connect().cursor()
            cur.execute("SELECT id, script_path, package FROM cases")
            rows = cur.fetchall()
            filled = []
            for r in rows:
                # 用索引取值：连接未必设了 row_factory，元组下标最稳
                cid, script_path, package = r[0], r[1], r[2]
                if (package or "").strip():
                    continue                       # 已有值，不动
                sp = (script_path or "").replace("\\", "/")
                # 取 cases/ 之后的第一段
                m = re.search(r"/cases/([^/]+)/", sp)
                if not m:
                    continue
                cand = m.group(1)
                if not pkg_re.match(cand):
                    continue                       # 不是包名形态，跳过
                filled.append((cid, cand))
                if not dry_run:
                    self._connect().execute(
                        "UPDATE cases SET package=? WHERE id=?", (cand, cid))
            if not dry_run and filled:
                self._local.conn.commit()
            return filled

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
                   " user_input, script_path, package,"
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
                    "package", "duration_seconds", "status"]
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
        # 数据库行已经删掉了：产物清理只是顺手打扫，**任何失败都不能回滚 /
        # 不能往上抛**——否则调用方（Web UI）会把整次删除判成失败，甚至因为
        # 未捕获异常把连接掐断，前端只看到一句无头无脑的 "Failed to fetch"。
        try:
            removed += self._remove_artifacts(evidence_paths, report_path, case_id)
        except Exception:
            pass
        return removed

    def _remove_artifacts(self, evidence_paths, report_path, case_id):
        """删除一次执行的磁盘产物（截图目录 + 报告及同前缀备份），返回删除数。"""
        removed = 0
        shot_dirs = set()
        for p in evidence_paths:
            if not is_artifact_path(p):   # 只删运行产物目录内的文件
                continue
            parent = os.path.dirname(os.path.abspath(p))
            # case_* 目录（一次执行一个目录）按目录删，其它散文件按文件删
            if os.path.basename(parent).startswith("case_"):
                shot_dirs.add(parent)
            elif safe_remove(p):
                removed += 1
        for d in shot_dirs:
            if not is_artifact_path(d):   # 整目录删除前同样过白名单
                continue
            # 不再被其它记录引用 → 整目录删；仍被引用 → 只删本 case 引用的文件
            if self._dir_referenced_elsewhere(d, exclude_case=case_id):
                for p in {e for e in evidence_paths
                          if os.path.dirname(os.path.abspath(e)) == d}:
                    if safe_remove(p):
                        removed += 1
            else:
                if safe_rmtree(d):
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
                if is_artifact_path(p) and safe_remove(p):
                    removed += 1
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
                        " user_input, script_path, final_status, package"
                        " FROM cases WHERE id=?", (case_id,))
            row = cur.fetchone()
            if not row:
                return None
            case = dict(zip(["id", "name", "device", "started_at", "finished_at",
                             "report_path", "summary", "user_input", "script_path",
                             "final_status", "package"], row))
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
