#!/usr/bin/env python3
"""从数据库重建测试报告。

报告文件可能因为跨机器迁移、路径变更、清理误伤等原因丢失，但
`steps` / `results` / `step_evidences` 里的明细一直都在 —— 报告只是这些
数据的 Markdown 渲染结果，随时可以重新算出来。

渲染格式与 `TestBase.finish()` 保持一致，重建出来的报告和现场生成的
看不出区别。
"""
import json
import os
import re
from datetime import datetime

from db import default_test_dir

MARK = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️",
        "INFO": "ℹ️", "BLOCKED": "⛔"}


def _fmt_time(iso):
    """'2026-09-03T14:28:59' -> '2026-09-03 14:28'（报告用，只到分钟）。"""
    if not iso:
        return "-"
    return iso.replace("T", " ")[:16]


def _reports_dir(db=None):
    """报告目录：与 db._artifact_roots() 同一套定位规则。"""
    base = os.path.join(default_test_dir(), "storage")
    return os.path.join(base, "reports")


def load_case(db, case_id):
    """取出一条记录及其全部明细。"""
    conn = db._connect()
    conn.row_factory = None
    row = conn.execute(
        "SELECT id, name, device, started_at, finished_at, report_path,"
        " summary, user_input, script_path, final_status, package"
        " FROM cases WHERE id=?", (case_id,)).fetchone()
    if not row:
        return None
    keys = ["id", "name", "device", "started_at", "finished_at", "report_path",
            "summary", "user_input", "script_path", "final_status", "package"]
    case = dict(zip(keys, row))

    steps = []
    for sid, sname, sord in conn.execute(
            "SELECT id, name, ord FROM steps WHERE case_id=? ORDER BY ord, id",
            (case_id,)):
        results = []
        for _rid, res, detail, state_json, evidence in conn.execute(
                "SELECT id, result, detail, state_json, evidence"
                " FROM results WHERE step_id=? ORDER BY id", (sid,)):
            state = None
            if state_json:
                try:
                    state = json.loads(state_json)
                except (ValueError, TypeError):
                    state = state_json
            results.append({"result": res, "detail": detail,
                            "state": state, "evidence": evidence})
        evidences = [e for (e,) in conn.execute(
            "SELECT evidence FROM step_evidences WHERE step_id=? ORDER BY id",
            (sid,))]
        steps.append({"name": sname, "results": results, "evidences": evidences})
    case["steps"] = steps
    return case


def render_report(case):
    """把一条记录渲染成 Markdown（与 finish() 同格式）。"""
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "INFO": 0, "BLOCKED": 0}
    for s in case["steps"]:
        for r in s["results"]:
            counts[r["result"]] = counts.get(r["result"], 0) + 1
    total = counts["PASS"] + counts["FAIL"]

    # 最终结论与 finish() 同一套规则：FAIL > BLOCKED > WARN > PASS
    if counts["FAIL"]:
        final = "FAIL"
    elif counts["BLOCKED"]:
        final = "BLOCKED"
    elif counts["WARN"]:
        final = "WARN"
    else:
        final = "PASS"

    # 证据目录：从第一条证据反推（case_* 目录）
    case_dir = "-"
    for s in case["steps"]:
        for r in s["results"]:
            if r.get("evidence"):
                case_dir = os.path.dirname(os.path.abspath(r["evidence"]))
                break
        if case_dir != "-":
            break
    if case_dir == "-":
        for s in case["steps"]:
            if s["evidences"]:
                case_dir = os.path.dirname(os.path.abspath(s["evidences"][0]))
                break

    lines = [f"# 测试报告：{case['name']}",
             f"\n**测试日期**：{_fmt_time(case.get('started_at'))}",
             f"**设备**：{case.get('device') or '-'}",
             # 包名从入库字段取；老记录没有时退回脚本路径（不强求，缺就写 unknown）
             f"**被测 App**：{case.get('package') or case.get('script_path') or 'unknown'}",
             f"**最终结论**：{final}",
             f"**证据目录**：{case_dir}\n"]
    for s in case["steps"]:
        lines.append(f"\n## {s['name']}")
        for r in s["results"]:
            mark = MARK.get(r["result"], "•")
            lines.append(f"- {mark} {r['detail']}")
            if r.get("state"):
                lines.append(f"  - 状态: {r['state']}")
            if r.get("evidence"):
                lines.append(f"  - 证据: `{r['evidence']}`")
        for ev in s["evidences"]:
            lines.append(f"  - 证据: `{ev}`")

    duration = "-"
    if case.get("started_at") and case.get("finished_at"):
        try:
            t0 = datetime.fromisoformat(case["started_at"])
            t1 = datetime.fromisoformat(case["finished_at"])
            duration = f"{round((t1 - t0).total_seconds(), 1)}s"
        except ValueError:
            duration = "-"
    summary = (f"✅ {counts['PASS']} 通过 / ❌ {counts['FAIL']} 失败 / "
               f"⚠️ {counts['WARN']} 警告 / ⛔ {counts['BLOCKED']} 阻塞 / "
               f"ℹ️ {counts['INFO']} 记录 / 共 {total} 条断言")
    lines.append(f"\n---\n**汇总**: {summary} / 耗时 {duration}")
    lines.append(f"**最终结论**: {final}")
    lines.append("\n> 本报告由数据库记录重建（原始报告文件已丢失），"
                 "数据与现场执行时一致。")
    return "\n".join(lines), final, summary


def _target_path(db, case, prefer_original=False):
    """决定写到哪里。

    `prefer_original=True`：写回记录原本的 report_path（用于"这条记录本来就该
    持有这份报告"的场景）。

    否则生成**独立**报告 `<name>_<运行时间>_报告.md`。这么设计是因为多条同名
    用例的历史记录会指向同一个报告名（`<name>_报告.md` 是 finish() 的既定
    语义：重跑覆盖）。如果都往同一个文件写，最后只留下一份，其余记录还是
    "报告缺失" —— 那就白重建了。

    独立命名既有唯一性，文件名上也能看出是哪次跑的；真撞名再补记录号。
    """
    rp = case.get("report_path")
    if prefer_original and rp:
        d = os.path.dirname(os.path.abspath(rp))
        if os.path.isdir(d):
            return rp

    rdir = _reports_dir(db)
    ts = ""
    if case.get("started_at"):
        ts = (case["started_at"].replace("T", "_")
              .replace(":", "").replace("-", "")[:15])   # 20260903_142859
    stem = case["name"]
    name = f"{stem}_{ts}_报告.md" if ts else f"{stem}_报告.md"
    path = os.path.join(rdir, name)
    if os.path.exists(path):
        base, ext = os.path.splitext(name)
        path = os.path.join(rdir, f"{base}_#{case['id']}{ext}")
    return path


def rebuild_report(case_id, db=None, prefer_original=False):
    """重建一条记录的报告：渲染 → 写盘 → 回写 report_path。

    返回 (报告路径, 汇总文本)。失败抛异常。
    """
    if db is None:
        import db as _db
        db = _db.get_db()
    case = load_case(db, case_id)
    if case is None:
        raise ValueError(f"记录不存在: {case_id}")

    text, final, summary = render_report(case)
    path = _target_path(db, case, prefer_original=prefer_original)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    conn = db._connect()
    conn.execute("UPDATE cases SET report_path=?, summary=?, final_status=? WHERE id=?",
                 (path, summary, final, case_id))
    conn.commit()
    return path, summary


REBUILT_MARK = "本报告由数据库记录重建"


def _report_is_stale(rp, started_at, finished_at):
    """报告文件里的内容不是这条记录的。

    判定看落款时间：报告要么用开始时间（重建的），要么用完成时间（现场生成的），
    两者之一对得上就算本条的；都对不上就是别人的报告被指到了这条记录上。
    容差 60 秒（跨分钟边界 + 渲染耗时）。
    """
    if not rp or not os.path.isfile(rp):
        return False
    try:
        with open(rp, encoding="utf-8", errors="ignore") as f:
            head = f.read(3000)
    except OSError:
        return False
    m = re.search(r"\*\*测试日期\*\*：(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})", head)
    if not m:
        return False
    try:
        rd = datetime.strptime(m.group(1).replace("T", " "), "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    for cand in (started_at, finished_at):
        if not cand:
            continue
        try:
            cd = datetime.strptime(cand.replace("T", " ")[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if abs((rd - cd).total_seconds()) <= 60:
            return False
    return True


def rebuild_report_auto(case_id, db=None):
    """给 Web UI 用的一键重建：自动决定写回原路径还是另起独立文件名。

    原路径还被**别的记录**指着时改成独立命名 —— 否则覆盖完，别人那条记录
    看到的就是这份报告的内容，又变成"报告对不上"了。
    """
    if db is None:
        import db as _db
        db = _db.get_db()
    case = load_case(db, case_id)
    if case is None:
        raise ValueError(f"记录不存在: {case_id}")
    rp = case.get("report_path")
    prefer = True
    if rp:
        n = db._connect().execute(
            "SELECT COUNT(*) FROM cases WHERE report_path=? AND id<>?",
            (rp, case_id)).fetchone()[0]
        prefer = (n == 0)
    return rebuild_report(case_id, db, prefer_original=prefer)


def rebuild_broken(db=None, limit=None):
    """批量重建两类「报告对不上」的记录：

    A. 报告文件缺失（跨机器迁移、清理误伤等）
    B. 多条记录共用同一个报告文件 —— 只有最新那条的内容会被留下来，
       其余记录点开看到的其实是别人的报告

    B 类保留最新一条占用原路径（沿用 finish() 的语义），其余各自生成独立报告。

    返回 [(case_id, path, err), ...]。
    """
    if db is None:
        import db as _db
        db = _db.get_db()
    conn = db._connect()

    # 共享路径 -> 各引用它的记录（按 id 升序）；最新一条继续持有原路径
    shared = {}
    for rp, cid in conn.execute(
            "SELECT report_path, id FROM cases"
            " WHERE report_path IS NOT NULL ORDER BY id"):
        shared.setdefault(rp, []).append(cid)
    owner = {rp: ids[-1] for rp, ids in shared.items() if len(ids) > 1}

    out = []
    done = 0
    for cid, rp, started, finished in conn.execute(
            "SELECT id, report_path, started_at, finished_at"
            " FROM cases ORDER BY id").fetchall():
        missing = (not rp) or (not os.path.isfile(rp))
        shadowed = rp in owner and owner[rp] != cid        # 报告其实是别人的
        is_owner = rp in owner and owner[rp] == cid        # 持有者本人
        stale = _report_is_stale(rp, started, finished)    # 内容不是本条的
        if not (missing or shadowed or is_owner or stale):
            continue
        try:
            # 持有者 / 唯一引用者 -> 写回原路径（它本来就"该"持有这份报告）；
            # 被别人占着报告的 -> 另起独立文件名
            path, _summary = rebuild_report(
                cid, db, prefer_original=(missing or is_owner or stale))
            out.append((cid, path, None))
        except Exception as e:
            out.append((cid, None, f"{type(e).__name__}: {e}"))
        done += 1
        if limit and done >= limit:
            break
    return out


if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import db as dbmod

    ap = argparse.ArgumentParser(description="从数据库重建丢失的测试报告")
    ap.add_argument("case_id", nargs="?", type=int, help="只重建指定记录")
    ap.add_argument("--all", action="store_true", help="重建所有报告缺失的记录")
    ap.add_argument("--force-all", action="store_true",
                    help="强制重建所有记录（报告没坏也重建）。"
                         "用于报告字段变更后刷新历史文件内容，"
                         "例如 package 入库后要把「被测 App」从脚本路径改成包名")
    args = ap.parse_args()

    database = dbmod.get_db()
    if args.case_id:
        p, s = rebuild_report(args.case_id, database)
        print(f"已重建 #{args.case_id} -> {p}")
        print(f"  {s}")
    elif args.all:
        res = rebuild_broken(database)
        ok = sum(1 for _c, p, e in res if p)
        for cid, p, err in res:
            print(("  OK   " if p else "  FAIL ") + f"#{cid} " + (p or err))
        print(f"\n重建完成：成功 {ok} / 失败 {len(res) - ok}")
    elif args.force_all:
        # 报告没坏也重建：report_path 已经一一对应，重建就是各自覆盖自己的那份
        rows = database.list_cases(limit=100000, include_internal=True)
        ok = 0
        fail = 0
        for r in rows:
            cid = r.get("id")
            try:
                rebuild_report_auto(cid, database)
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  FAIL #{cid} {type(e).__name__}: {e}")
        print(f"\n强制重建完成：成功 {ok} / 失败 {fail}")
    else:
        ap.print_help()
