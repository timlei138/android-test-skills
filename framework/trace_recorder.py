#!/usr/bin/env python3
"""采集会话档案（trace recorder）：探查/采集阶段的 dump 快照与事件日志。

职责单一：把一个"采集会话"的所有 UI 树快照 + 关键事件落盘到同一目录，
供事后排查与补料：
- 排查：events.jsonl 定位"卡在哪个动作"（超时/看门狗/异常），按时间线翻
  对应序号的 dump 快照看"当时页面什么状态"；
- 补料：probes 语义缓存缺料时，从这里的原始 dump 重新解析，不必重跑真机。

设计约束：
- 与 TestCase 解耦：TestCase 只持有本类实例并在 _dump()/关键动作处委托；
  本类不 import uiautomator2，可独立单测（tests/test_trace_recorder.py）。
- trace 关闭时所有方法为空操作，正常回归零额外 IO。

目录结构（一次采集会话一个目录）:
  storage/traces/<用例名>/<会话时间戳>/
    00001.xml …              每次 _dump 的 UI 树快照（文件名 = 序号）
    index.json               [{seq, ts, src, ctx, bytes}] 快照 ↔ 触发点对应
    events.jsonl             [{seq, ts, type, detail, result?, dur_ms?}] 事件流
"""
import json
import os
import re
import shutil
import time
from datetime import datetime


class TraceRecorder:
    """采集会话录制器：snapshot() 落 dump、event() 落事件，共享同一会话时间线。

    用法（由 TestCase 委托，不直接面向用例）:
        rec = TraceRecorder(storage_dir)   # storage/ 目录
        rec.start("联想日历_175")           # 开启会话（幂等）
        rec.snapshot(xml)                  # 每次 dump 后落一份快照
        rec.event("wait", "text=xx", result="timeout", start=t0)
        rec.stop()

    每个用例目录保留最近 max_keep 个会话（start 时自动清理更早的机器产物）。
    """

    def __init__(self, storage_dir, max_keep=None, max_idle_min=None):
        self._trace_dir = os.path.join(storage_dir, "traces")
        # max_keep：每用例保留的最近会话数。默认 0 = 不自动清理
        # （批量 rmtree 会撞环境删除护栏中断采集，需清理时显式开启：
        #   TraceRecorder(..., max_keep=5) 或环境变量 DSH_TRACE_MAXKEEP=5）
        if max_keep is None:
            max_keep = int(os.environ.get("DSH_TRACE_MAXKEEP", "0") or 0)
        self.max_keep = max(0, max_keep)
        # max_idle_min：按空闲时间清理——距**最后修改**超过该分钟数的会话目录删除。
        # 默认 30 分钟（2026-09-10 讨论定稿）：探查产物用完即弃，
        # 超时自动删，不靠人/AI 记得清理（"一个自觉弥补另一个自觉"的物证：
        # 当天 D:\dsh 下 15 个临时探针脚本全部未清理）。
        # 基准是 mtime 而非创建时间——183 从探查到验证通过跨 2 小时，
        # 按创建时间会在写用例中途删掉正在查的缓存。
        # 设 0 可关闭。
        if max_idle_min is None:
            max_idle_min = int(os.environ.get("DSH_TRACE_MAXIDLE_MIN", "30") or 0)
        self.max_idle_min = max(0, max_idle_min)
        self.enabled = False
        self.session_dir = None
        self.ctx = None          # 语义上下文（如 probe:label），snapshot 时登记
        self._dump_seq = 0       # 快照序号（文件名即序号）
        self._ev_seq = 0         # 事件序号
        self._index = []         # 快照登记表（写 index.json）

    # ── 会话生命周期 ─────────────────────────────────────────
    def start(self, case_name):
        """开启新采集会话。返回会话目录；已开启则返回现有会话（幂等）。"""
        if self.enabled:
            return self.session_dir
        self.enabled = True
        self._dump_seq = 0
        self._ev_seq = 0
        self._index = []
        safe = re.sub(r'[\\/:*?"<>|]', "_", case_name or "case")
        case_root = os.path.join(self._trace_dir, safe)
        self._prune_old_sessions(case_root)
        # 毫秒级时间戳：连续多次 start（<1s）也不撞目录，且目录名可排序
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        self.session_dir = os.path.join(case_root, ts)
        os.makedirs(self.session_dir, exist_ok=True)
        self.event("session", f"采集会话开始 dir={self.session_dir}")
        return self.session_dir

    def _prune_old_sessions(self, case_root):
        """清理该用例下最旧的超量会话目录（仅限 traces/<用例>/ 内，机器产物）。

        max_keep<1 直接跳过（默认安全）。在本次新会话创建前调用，故保留
        max_keep-1 个旧会话（预留本次的位），最终每用例目录 ≤ max_keep 个。

        另叠加**按空闲时间**清理（max_idle_min）：距最后修改超过该分钟数的
        会话目录一并删除。两者独立生效，取其并集。
        """
        if not os.path.isdir(case_root):
            return
        try:
            subs = sorted(
                (d for d in os.listdir(case_root)
                 if os.path.isdir(os.path.join(case_root, d))),
                key=lambda d: (os.path.getmtime(os.path.join(case_root, d)), d))

            # ① 按空闲时间清理：mtime 距现在超过 max_idle_min 分钟 → 删。
            #    **基准是 mtime（最后修改）而非创建时间**——写用例可能跨数小时，
            #    按创建时间会在使用中删掉正在查的缓存（183 探查→验证跨 2 小时）。
            #    会话目录内任何写入都会刷新 mtime，故"还在用就不会被删"。
            if self.max_idle_min > 0:
                cutoff = time.time() - self.max_idle_min * 60
                keep = []
                for d in subs:
                    p = os.path.join(case_root, d)
                    try:
                        if os.path.getmtime(p) < cutoff:
                            shutil.rmtree(p, ignore_errors=True)
                        else:
                            keep.append(d)
                    except OSError:
                        keep.append(d)
                subs = keep

            # ② 按数量清理（原行为，默认关闭）
            if self.max_keep < 1:
                return
            over = len(subs) - (self.max_keep - 1)   # 预留本次新建的会话位
            for old in subs[:max(0, over)]:
                shutil.rmtree(os.path.join(case_root, old), ignore_errors=True)
        except OSError:
            pass

    def stop(self):
        """结束会话（幂等）。"""
        if self.enabled:
            self.event("session", "采集会话结束")
        self.enabled = False

    # ── 语义上下文 ───────────────────────────────────────────
    def set_ctx(self, ctx):
        """标记当前操作语义（如 probe 的 label），后续快照归属其下。"""
        self.ctx = ctx

    def clear_ctx(self):
        self.ctx = None

    # ── dump 快照 ────────────────────────────────────────────
    def snapshot(self, xml, src=None):
        """落盘一份 UI 树快照并登记 index。返回登记记录或 None（关闭/空 xml）。

        触发点来源优先级：ctx（语义，如 probe label）> src 参数 > "dump"。
        """
        if not self.enabled or not xml:
            return None
        self._dump_seq += 1
        seq = self._dump_seq
        fn = os.path.join(self.session_dir, f"{seq:05d}.xml")
        rec = {"seq": seq,
               "ts": datetime.now().isoformat(timespec="milliseconds"),
               "src": self.ctx or src or "dump",
               "bytes": len(xml)}
        try:
            with open(fn, "w", encoding="utf-8") as f:
                f.write(xml)
            self._index.append(rec)
            # 整体覆写（会话内快照通常 ≤ 数百份，代价可控且保证随时可读）
            with open(os.path.join(self.session_dir, "index.json"),
                      "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False, indent=1)
            return rec
        except OSError:
            return None

    # ── 事件日志 ─────────────────────────────────────────────
    def event(self, etype, detail, result=None, start=None):
        """记录一个事件（session/wait/watchdog/dump/error/...）。关闭时 no-op。

        start 为动作开始时刻 time.time()，用于记 dur_ms（如等待耗时）。
        """
        if not self.enabled:
            return None
        self._ev_seq += 1
        ev = {"seq": self._ev_seq,
              "ts": datetime.now().isoformat(timespec="milliseconds"),
              "type": etype, "detail": detail}
        if result:
            ev["result"] = result
        if start:
            ev["dur_ms"] = int((time.time() - start) * 1000)
        try:
            with open(os.path.join(self.session_dir, "events.jsonl"),
                      "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            return ev
        except OSError:
            return None
