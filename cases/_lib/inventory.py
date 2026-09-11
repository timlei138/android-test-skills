#!/usr/bin/env python3
"""页面库存生成器 + 离线预检（构建期工具，零真机）。

从探查缓存 storage/probes/<包名>/<label>/ 生成"页面库存"——这一页有哪些
可交互项 / 可断言的 rid（带当前值与可点标记）/ 无 rid 的结构文本 / Canvas
需 OCR 区。写用例时对照库存"挑料"，不靠猜页面结构。

子命令：
  inventory  [labels...]  生成页面库存（默认该包全部 label）。--json 输出机器可读。
  verify     --texts/--rids/--re  对指定 label 缓存逐项查命中，供写用例前离线预检。

用法示例：
  python cases/_lib/inventory.py com.zui.calendar 175_课程时间设置页
  python cases/_lib/inventory.py com.zui.calendar --json > inv.json
  python cases/_lib/inventory.py com.zui.calendar verify 175_设置页 \
      --rids tv_lesson_duration --texts 课程时间设置 --re '^第\\d+节$'

存储位置：环境变量 DSH_WORKSPACE_DIR 指向工作区根，或默认 ~/android-test-skills-data。
本工具不 import uiautomator2/test_framework，任何 Python 环境可跑。
"""
import argparse
import json
import os
import re
import sys

# ── 存储定位：与 framework 同源（db.default_test_dir 逻辑），此处不依赖 db ──
def workspace_dir():
    env = os.environ.get("DSH_WORKSPACE_DIR")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    return os.path.join(os.path.expanduser("~"), "android-test-skills-data")


PROBE_ROOT = os.path.join(workspace_dir(), "storage", "probes")

# 弹框骨架保留（android 框架节点，App 无 rid 但断言弹框标题/按钮需要）
KEEP_ANDROID = {
    "android:id/alertTitle", "android:id/button1", "android:id/button2",
    "android:id/button3", "android:id/content", "android:id/customPanel",
    "android:id/title",
}


def list_labels(pkg):
    d = os.path.join(PROBE_ROOT, pkg)
    if not os.path.isdir(d):
        return []
    return sorted(x for x in os.listdir(d)
                  if os.path.isdir(os.path.join(d, x))
                  and os.path.isfile(os.path.join(d, x, "dump.xml")))


def parse_nodes(xml):
    """逐 <node> 提取属性（正则，容忍属性顺序变化）。"""
    out = []
    for m in re.finditer(r"<node[^>]*>", xml):
        seg = m.group(0)

        def g(k):
            mm = re.search(k + r'="([^"]*)"', seg)
            return mm.group(1) if mm else ""

        out.append({"rid": g("resource-id"), "text": g("text"),
                    "desc": g("content-desc"), "cls": g("class"),
                    "clickable": g("clickable") == "true",
                    "enabled": g("enabled") != "false",
                    "bounds": g("bounds")})
    return out


def app_nodes(nodes, pkg):
    """过滤：App 自有节点 + android 弹框骨架 + **无 rid 的节点**。
    去掉系统状态栏/导航装饰。

    无 rid 的节点必须保留（2026-09-11 修）：按 rid 前缀过滤会**丢掉所有没有
    resource-id 的文本节点**，而空状态文案、纯文本提示正是断言最常用的东西
    （实例：课程表空状态页「还未添加课程表」rid 为空，导致 verify 报
    "未命中（改 label 或核对文案）"——文案明明是对的，提示却把人引向
    改文案，逼着人改用全屏 screen_text 扫描，慢且脆）。
    无 rid 的节点不属于任何包，按"当前页内容"保留。
    """
    return [n for n in nodes
            if (not n["rid"])                       # 无 rid：页面文本，保留
            or n["rid"].startswith(pkg + ":id/")    # App 自有
            or n["rid"] in KEEP_ANDROID]            # android 弹框骨架


def short_rid(rid, pkg):
    prefix = pkg + ":id/"
    return rid[len(prefix):] if rid.startswith(prefix) else rid


def load_page(pkg, label):
    """读一个 label 的缓存，返回结构化页面。缺 dump 时返回 None。"""
    d = os.path.join(PROBE_ROOT, pkg, label)
    fp = os.path.join(d, "dump.xml")
    if not os.path.isfile(fp):
        return None
    xml = open(fp, encoding="utf-8").read()
    nodes = app_nodes(parse_nodes(xml), pkg)
    page = {"label": label, "pkg": pkg, "meta": {}, "nodes": nodes}
    fp_m = os.path.join(d, "meta.json")
    if os.path.isfile(fp_m):
        try:
            page["meta"] = json.load(open(fp_m, encoding="utf-8"))
        except ValueError:
            pass
    page["has_ocr"] = os.path.isfile(os.path.join(d, "ocr.json"))
    return page


# ── inventory：按用途分类摊开 ─────────────────────────────────────
def classify(page, pkg):
    """把页面节点分成三类料：可交互 / 可断言 rid / 结构文本。"""
    interact = []      # 可点且带文字（交互步骤的料）
    assertable = []    # App 有 rid 的节点（可 read_rid 精确断言）
    structure = []     # 无 rid 的可见文本（分组标题/说明等，只能文本匹配）
    for n in page["nodes"]:
        is_app = n["rid"].startswith(pkg + ":id/")
        if is_app:
            if n["clickable"] and n["text"]:
                interact.append({"rid": short_rid(n["rid"], pkg), "text": n["text"],
                                 "desc": n["desc"]})
            assertable.append({"rid": short_rid(n["rid"], pkg),
                               "text": n["text"], "desc": n["desc"],
                               "clickable": n["clickable"]})
        elif n["rid"] in KEEP_ANDROID:
            # android 弹框骨架：button1=确定 button2=取消 alertTitle=标题
            name = n["rid"].replace("android:id/", "")
            if n["text"] or name in ("alertTitle", "button1", "button2", "button3"):
                interact.append({"rid": f"android:{name}", "text": n["text"],
                                 "desc": "", "clickable": n["clickable"]})
        elif n["text"]:
            structure.append(n["text"])
    return interact, assertable, sorted(set(structure))


def render_inventory(pkg, page, verbose=False):
    lines = [f"===== [{page['label']}] ====="]
    act, asr, stru = classify(page, pkg)
    if page["meta"].get("package"):
        lines.append(f"  meta: package={page['meta']['package']}  texts={len(page['meta'].get('texts', []))}")
    if act:
        lines.append("  [可交互]  tap_text/tap_rid 可点的入口")
        for a in act:
            tag = f"rid={a['rid']}" if not a["rid"].startswith("android") else "android"
            txt = f"text={a['text']!r}" if a["text"] else f"desc={a['desc']!r}"
            lines.append(f"    {a['rid']:<32} {txt}")
    if asr:
        lines.append("  [可断言]  read_rid 可精确读值的 App 节点")
        for a in asr:
            if a["rid"].startswith("android"):
                continue
            mark = "可点" if a["clickable"] else "    "
            val = f"val={a['text']!r}" if a["text"] else ""
            desc = f" desc={a['desc']!r}" if (not a["text"] and a["desc"]) else ""
            if verbose or a["text"] or a["clickable"]:
                lines.append(f"    {a['rid']:<34} {mark}  {val}{desc}")
    if stru:
        lines.append("  [结构文本]  无 rid（分组标题/说明/Canvas 外文本），只能 screen_text 匹配")
        lines.append("    " + " | ".join(stru))
    if page["has_ocr"]:
        lines.append("  [OCR 缓存存在]  该页有 Canvas 内容，可读 ocr.json 补料")
    return "\n".join(lines)


def cmd_inventory(args):
    labels = args.labels or list_labels(args.pkg)
    if not labels:
        print(f"[inventory] {args.pkg} 无探查缓存（{PROBE_ROOT}）", file=sys.stderr)
        return 1
    pages = [load_page(args.pkg, x) for x in labels]
    pages = [p for p in pages if p]
    if args.json:
        out = {p["label"]: _page_to_json(p, args.pkg) for p in pages}
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for p in pages:
            print(render_inventory(args.pkg, p, verbose=args.verbose))
            print()
    return 0


def _page_to_json(page, pkg):
    act, asr, stru = classify(page, pkg)
    return {"interactions": act, "assertables": asr, "structure": stru,
            "has_ocr": page["has_ocr"], "meta": page["meta"]}


# ── verify：写用例前离线预检 ──────────────────────────────────────
def _page_texts(page):
    return [n["text"] for n in page["nodes"] if n["text"]]


def _page_rids(page, pkg):
    return {short_rid(n["rid"], pkg) for n in page["nodes"]
            if n["rid"].startswith(pkg + ":id/")}


def cmd_verify(args):
    page = load_page(args.pkg, args.label)
    if not page:
        print(f"[verify] 缓存不存在: {args.pkg}/{args.label}", file=sys.stderr)
        return 1
    texts, rids = _page_texts(page), _page_rids(page, args.pkg)
    ok = True
    for t in (args.texts or []):
        hit = t in texts
        ok &= hit
        print(f"  [{'ok' if hit else 'FAIL'}] text {t!r} "
              f"{'命中' if hit else '未命中（改 label 或核对文案）'}")
    for r in (args.rids or []):
        hit = r in rids
        ok &= hit
        print(f"  [{'ok' if hit else 'FAIL'}] rid  {r} "
              f"{'命中' if hit else '未命中（核对 rid 或改 label）'}")
    for rx in (args.re or []):
        try:
            pat = re.compile(rx)
            hits = [t for t in texts if pat.search(t)]
        except re.error as e:
            print(f"  [FAIL] regex {rx!r} 非法: {e}")
            ok = False
            continue
        hit = bool(hits)
        ok &= hit
        print(f"  [{'ok' if hit else 'FAIL'}] regex {rx!r} "
              f"{f'{len(hits)} 命中 {hits[:5]}' if hit else '0 命中（页面结构理解可能有误，先别写这行断言）'}")
    print(f"  → {'全部命中，可以放心写这页的断言' if ok else '存在未命中，修正后再写用例'}")
    return 0 if ok else 2


# ── CLI ───────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pkg", help="包名（对应 storage/probes/<pkg>/）")
    sub = ap.add_subparsers(dest="cmd")

    pi = sub.add_parser("inventory", help="生成页面库存")
    pi.add_argument("labels", nargs="*", help="label 列表，缺省=全部")
    pi.add_argument("--json", action="store_true")
    pi.add_argument("--verbose", action="store_true")

    pv = sub.add_parser("verify", help="离线预检：texts/rids/正则是否命中缓存")
    pv.add_argument("label")
    pv.add_argument("--texts", nargs="*", default=[])
    pv.add_argument("--rids", nargs="*", default=[])
    pv.add_argument("--re", nargs="*", default=[])

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 1
    return (cmd_inventory if args.cmd == "inventory" else cmd_verify)(args)


if __name__ == "__main__":
    sys.exit(main())
