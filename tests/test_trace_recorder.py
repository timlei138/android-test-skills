#!/usr/bin/env python3
"""TraceRecorder 纯逻辑单测：不依赖 uiautomator2/设备，可独立运行。

    python -m unittest tests.test_trace_recorder -v

覆盖：会话生命周期 / dump 快照落盘 / index 对应关系 / 事件日志 /
ctx 语义优先级 / 未开启时空操作。
"""
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FW = os.path.join(os.path.dirname(_HERE), "framework")
if _FW not in sys.path:
    sys.path.insert(0, _FW)

from trace_recorder import TraceRecorder   # noqa: E402


class TraceRecorderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.rec = TraceRecorder(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _session_dir(self):
        return os.path.join(self._tmp.name, "traces", "联想日历_175_test", "20260903_000000")

    def test_start_creates_session_and_events(self):
        d = self.rec.start("联想日历_175_test")
        self.assertTrue(os.path.isdir(d))
        self.assertTrue(self.rec.enabled)
        with open(os.path.join(d, "events.jsonl"), encoding="utf-8") as f:
            evs = f.read().splitlines()
        self.assertEqual(len(evs), 1)
        self.assertEqual(json.loads(evs[0])["type"], "session")

    def test_start_idempotent(self):
        d1 = self.rec.start("c")
        d2 = self.rec.start("c")
        self.assertEqual(d1, d2)

    def test_snapshot_writes_xml_and_index(self):
        d = self.rec.start("c")
        rec = self.rec.snapshot("<hierarchy/>", src="tap_text:88")
        self.assertEqual(rec["seq"], 1)
        self.assertEqual(rec["src"], "tap_text:88")
        self.assertTrue(os.path.isfile(os.path.join(d, "00001.xml")))
        with open(os.path.join(d, "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        self.assertEqual(len(idx), 1)
        self.assertEqual(idx[0]["seq"], 1)

    def test_snapshot_ctx_priority_over_src(self):
        self.rec.start("c")
        self.rec.set_ctx("probe:175_确认页")
        rec = self.rec.snapshot("<h/>", src="el_bounds:589")
        self.assertEqual(rec["src"], "probe:175_确认页")
        self.rec.clear_ctx()
        rec2 = self.rec.snapshot("<h/>", src="wait_text:777")
        self.assertEqual(rec2["src"], "wait_text:777")

    def test_event_appends_and_counts(self):
        d = self.rec.start("c")
        import time as _t
        t0 = _t.time()
        self.rec.event("wait", "text=课程时间设置", result="timeout", start=t0)
        self.rec.event("watchdog", "词表命中 知道了", result="click")
        with open(os.path.join(d, "events.jsonl"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 3)            # session + 2 events
        e2 = json.loads(lines[1])
        self.assertEqual(e2["type"], "wait")
        self.assertEqual(e2["result"], "timeout")
        self.assertIn("dur_ms", e2)

    def test_disabled_is_noop(self):
        """未 start：snapshot/event 不落盘、不建目录。"""
        self.assertIsNone(self.rec.snapshot("<h/>"))
        self.assertIsNone(self.rec.event("x", "y"))
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "traces")))

    def test_stop_ends_session_and_disables(self):
        self.rec.start("c")
        self.rec.stop()
        self.assertFalse(self.rec.enabled)
        self.assertIsNone(self.rec.snapshot("<h/>"))

    def test_empty_xml_not_snapshotted(self):
        self.rec.start("c")
        self.assertIsNone(self.rec.snapshot(""))
        self.assertEqual(len(self.rec._index), 0)

    def test_snapshot_monotonic_seq(self):
        self.rec.start("c")
        self.rec.snapshot("<a/>")
        self.rec.snapshot("<b/>")
        self.rec.snapshot("<c/>")
        with open(os.path.join(self.rec.session_dir, "index.json"),
                  encoding="utf-8") as f:
            idx = json.load(f)
        self.assertEqual([r["seq"] for r in idx], [1, 2, 3])
        for s in ("00001.xml", "00002.xml", "00003.xml"):
            self.assertTrue(os.path.isfile(os.path.join(self.rec.session_dir, s)))

    def test_prune_keeps_recent_sessions(self):
        """max_keep=2：第 3 次 start 后最旧的会话被清掉，保留最近 2 个。"""
        rec = TraceRecorder(self._tmp.name, max_keep=2)
        d1 = rec.start("c"); rec.stop()
        d2 = rec.start("c"); rec.stop()
        d3 = rec.start("c")
        rec.stop()
        self.assertTrue(os.path.isdir(d3))
        self.assertTrue(os.path.isdir(d2))
        self.assertFalse(os.path.isdir(d1))     # 最旧的已被清理
        self.assertFalse(rec.enabled)


if __name__ == "__main__":
    unittest.main()
