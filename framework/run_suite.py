#!/usr/bin/env python3
"""套件级 runner：批量执行用例，子进程隔离，聚合报告。

用法：
  python run_suite.py [--package com.zui.calendar] [--device SERIAL] [--jobs 1] [--timeout SECS]

每个用例 = 独立子进程（sys.executable run_case.py <用例>），天然隔离全局态/设备连接/异常。
子进程退出码直接复用现有语义（0/1/2/3），套件退出码与之同构：
  任一 FAIL→1；无 FAIL 有 ERROR→3；仅 BLOCKED→2；全 PASS/WARN→0。

设备断连熔断：子进程返回 3（ERROR）时先 adb devices 检查设备在线，
不在线 → 立即终止套件（不浪费时间跑注定全部超时的后续用例）。
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# run_case 模块的 CASE_DIRS / _iter_case_files 复用于用例发现
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from run_case import CASE_DIRS, _iter_case_files  # noqa: E402


def _collect_cases(package=None):
    """收集要执行的用例文件列表。

    package 过滤：只返回 cases/<package>/ 下的用例。None = 全量。
    返回 [(绝对路径, 相对路径), ...]，相对路径 = 去掉 CASE_DIRS 前缀后的部分
    （传给子进程的 run_case.py 参数）。
    """
    cases = []
    seen = set()
    for abs_path in _iter_case_files():
        if abs_path in seen:
            continue
        seen.add(abs_path)
        # 计算相对路径（去掉 CASE_DIRS 中的某个前缀）
        rel = None
        for d in CASE_DIRS:
            d_norm = os.path.normcase(os.path.abspath(d))
            p_norm = os.path.normcase(abs_path)
            if p_norm.startswith(d_norm + os.sep):
                rel = os.path.relpath(abs_path, d)
                break
        if rel is None:
            rel = os.path.basename(abs_path)
        # package 过滤：相对路径的第一段目录 = 包名
        if package:
            parts = rel.replace("\\", "/").split("/")
            if len(parts) < 2 or parts[0] != package:
                continue
        cases.append((abs_path, rel))
    return cases


def _device_online(serial=None):
    """检查设备是否仍在线。serial=None 时检查是否有任何授权设备。"""
    try:
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
        if serial:
            return any(serial in line and "device" in line
                       for line in r.stdout.splitlines())
        return any("device" in line for line in r.stdout.splitlines()[1:])
    except Exception:
        return False


def _suite_exit_code(counts):
    """套件退出码：与单用例语义同构，CI 可直接消费。"""
    if counts["fail"] > 0:
        return 1
    if counts["error"] > 0:
        return 3
    if counts["blocked"] > 0:
        return 2
    return 0


def _generate_report(cases_results, counts, started_at, suite_filter, report_dir):
    """生成套件聚合报告 Markdown。返回报告文件路径。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"suite_{ts}_报告"
    path = os.path.join(report_dir, name + ".md")
    dur = time.time() - started_at

    status_emoji = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌",
                    "BLOCKED": "⛔", "ERROR": "💥"}
    lines = [
        f"# 套件报告 {name}",
        "",
        f"- **执行时间**: {datetime.fromtimestamp(started_at).isoformat(timespec='seconds')}",
        f"- **总耗时**: {dur:.1f}s",
        f"- **筛选条件**: {suite_filter or '全量'}",
        f"- **总计**: {len(cases_results)} 个用例",
        f"  - ✅ PASS: {counts['pass']}",
        f"  - ⚠️ WARN: {counts['warn']}",
        f"  - ❌ FAIL: {counts['fail']}",
        f"  - ⛔ BLOCKED: {counts['blocked']}",
        f"  - 💥 ERROR: {counts['error']}",
        f"  - ⏭️ 未执行: {counts.get('skipped', 0)}",
        "",
        "## 用例明细",
        "",
        "| # | 用例 | 结论 | 耗时 | 退出码 | 报告 |",
        "|---|------|------|------|--------|------|",
    ]
    for i, result in enumerate(cases_results, 1):
        rel, status, elapsed, code = result[:4]
        report_rel = result[4] if len(result) > 4 else ""
        emoji = status_emoji.get(status, "?")
        report_link = f"[`报告`]({report_rel})" if report_rel else "—"
        lines.append(f"| {i} | `{rel}` | {emoji} {status} | {elapsed:.1f}s | {code} | {report_link} |")
    if counts.get("skipped", 0) > 0:
        lines.append(f"| - | *剩余 {counts['skipped']} 个用例未执行* | ⏭️ | - | - | — |")

    os.makedirs(report_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    parser = argparse.ArgumentParser(description="套件级 runner：批量执行用例")
    parser.add_argument("--package", help="只跑指定包名的用例（如 com.zui.calendar）")
    parser.add_argument("--device", help="绑定设备 serial（透传给 run_case.py --device）")
    parser.add_argument("--jobs", type=int, default=1,
                        help="并行数（P2 预留，当前仅支持 1）")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单个用例超时秒数（默认 600）")
    args = parser.parse_args()

    if args.jobs > 1:
        print("⚠️  --jobs > 1 尚未实现（P2），回退为串行执行")
        args.jobs = 1

    # ── 收集用例 ──────────────────────────────────────────────────────
    cases = _collect_cases(package=args.package)
    suite_filter = f"package={args.package}" if args.package else "全量"
    if not cases:
        print(f"⚠️  无匹配用例（{suite_filter}）")
        print(f"  已查找目录: {[d for d in CASE_DIRS if os.path.isdir(d)]}")
        sys.exit(3)

    print(f"🏁 套件执行：{len(cases)} 个用例（{suite_filter}）")
    print(f"   退出码语义: PASS/WARN=0  FAIL=1  BLOCKED=2  ERROR=3")
    print()

    # ── 初始化 DB ─────────────────────────────────────────────────────
    suite_id = None
    try:
        from db import get_db, default_test_dir
        db = get_db()
        suite_id = db.start_suite(suite_filter, len(cases))
        report_dir = os.path.join(default_test_dir(), "storage", "reports")
    except Exception as e:
        print(f"⚠️ [db] 套件记录初始化失败（不影响执行）: {e}")
        db = None
        report_dir = os.path.join(
            os.environ.get("DSH_ANDROID_TEST_DIR",
                           os.path.join(os.path.expanduser("~"), "dsh-android-test")),
            "storage", "reports")

    # ── 逐用例执行 ────────────────────────────────────────────────────
    started_at = time.time()
    results = []          # [(rel_path, status, elapsed, exit_code, report_path?)]
    counts = {"pass": 0, "warn": 0, "fail": 0, "blocked": 0, "error": 0, "skipped": 0}
    aborted = False

    for idx, (abs_path, rel_path) in enumerate(cases, 1):
        # 构造子进程命令
        cmd = [sys.executable, os.path.join(HERE, "run_case.py")]
        if args.device:
            cmd += ["--device", args.device]
        cmd.append(rel_path)

        # 环境变量透传 suite_id
        env = os.environ.copy()
        if suite_id is not None:
            env["DSH_SUITE_ID"] = str(suite_id)
        # 子进程的 DSH_CASE_SCRIPT_PATH 由 run_case.py 自己注入，不在这里设

        t0 = time.time()
        print(f"  [{idx}/{len(cases)}] {rel_path} ...", end=" ", flush=True)
        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                  timeout=args.timeout)
            code = proc.returncode
            elapsed = time.time() - t0
        except subprocess.TimeoutExpired:
            code = 3
            elapsed = time.time() - t0
            print(f"💥 TIMEOUT ({elapsed:.1f}s)")
            results.append((rel_path, "ERROR", elapsed, code, ""))
            counts["error"] += 1
            # 超时也检查设备在线（USB 松动的真实表现就是子进程卡死）
            if not _device_online(args.device):
                remaining = len(cases) - idx
                counts["skipped"] = remaining
                print(f"\n🔌 设备断连！剩余 {remaining} 个用例未执行")
                aborted = True
                break
            continue
        except Exception as e:
            code = 3
            elapsed = time.time() - t0
            print(f"💥 子进程异常: {e} ({elapsed:.1f}s)")
            results.append((rel_path, "ERROR", elapsed, code, ""))
            counts["error"] += 1
            # 异常也检查设备在线
            if not _device_online(args.device):
                remaining = len(cases) - idx
                counts["skipped"] = remaining
                print(f"\n🔌 设备断连！剩余 {remaining} 个用例未执行")
                aborted = True
                break
            continue

        # 退出码 → 状态
        status_map = {0: "PASS", 1: "FAIL", 2: "BLOCKED", 3: "ERROR"}
        status = status_map.get(code, "ERROR")

        # 失败/错误时回显子进程输出（保留排查线索）
        if code != 0:
            if proc.stdout:
                for ln in proc.stdout.strip().splitlines()[-10:]:  # 最多末尾 10 行
                    print(f"      │ {ln}")
            if proc.stderr:
                for ln in proc.stderr.strip().splitlines()[-5:]:
                    print(f"      │ {ln}")

        # PASS/WARN 在单用例退出码都是 0，这里无法区分——
        # 套件级只关心是否阻断，WARN 算通过（与 flaky 口径一致）。
        # 精确 WARN/PASS 区分留给 1.1 flakiness 视图（从 DB final_status 读）。

        emoji_map = {"PASS": "✅", "FAIL": "❌", "BLOCKED": "⛔", "ERROR": "💥"}
        print(f"{emoji_map.get(status, '?')} {status} ({elapsed:.1f}s)")

        # 从子进程输出提取报告路径（run_case.py 末尾打印「📄 报告已生成: <path>」）
        case_report = ""
        if proc.stdout:
            import re as _re
            m = _re.search(r"报告已生成[：:]\s*(.+\.md)", proc.stdout)
            if m:
                rp = m.group(1).strip()
                # 转为相对路径（便于套件报告内链导航）
                try:
                    case_report = os.path.relpath(rp)
                except ValueError:
                    case_report = rp

        results.append((rel_path, status, elapsed, code, case_report))
        key = status.lower()
        counts[key] = counts.get(key, 0) + 1

        # ── 设备断连熔断 ──────────────────────────────────────────────
        if code == 3:
            online = _device_online(args.device)
            if not online:
                remaining = len(cases) - idx
                counts["skipped"] = remaining
                print(f"\n🔌 设备断连！剩余 {remaining} 个用例未执行")
                aborted = True
                break

    # ── 聚合报告 ──────────────────────────────────────────────────────
    report_path = _generate_report(results, counts, started_at, suite_filter, report_dir)
    total_dur = time.time() - started_at
    suite_code = _suite_exit_code(counts)

    print()
    print(f"{'=' * 60}")
    print(f"🏁 套件完成: {len(results)}/{len(cases)} 个用例  耗时 {total_dur:.1f}s")
    print(f"   ✅{counts['pass']}  ⚠️{counts['warn']}  ❌{counts['fail']}"
          f"  ⛔{counts['blocked']}  💥{counts['error']}"
          + (f"  ⏭️{counts['skipped']}" if counts['skipped'] else ""))
    if aborted:
        print(f"   ⚠️  套件因设备断连中止")
    print(f"   📄 报告: {report_path}")
    print(f"   退出码: {suite_code}")

    # ── DB 收尾 ────────────────────────────────────────────────────────
    if db and suite_id:
        try:
            db.finish_suite(suite_id,
                            pass_n=counts["pass"],  # WARN 单独计数
                            fail_n=counts["fail"], blocked_n=counts["blocked"],
                            error_n=counts["error"], warn_n=counts["warn"],
                            report_path=report_path)
        except Exception as e:
            print(f"⚠️ [db] 套件记录收尾失败: {e}")

    sys.exit(suite_code)


if __name__ == "__main__":
    main()
