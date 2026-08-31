#!/usr/bin/env python3
"""
Android GUI 测试框架：元素操作、断言、截图、Toast 捕捉、置灰判断、报告生成
依赖: uiautomator2 (venv 3.9) + rapidocr (venv313) — 本框架运行在 .venv (u2) 环境
"""
import io
import os
import re
import subprocess
import time
from datetime import datetime

import uiautomator2 as u2

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "screenshots", "reports")
ACTION_DELAY = 1.0   # 每次操作后的统一延时（防动画/时序竞态）


class TestCase:
    def __init__(self, name, device_id=None, case_dir=None):
        self.name = name
        # 唤醒屏幕并解锁
        subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                       capture_output=True)
        subprocess.run(["adb", "shell", "wm", "dismiss-keyguard"], capture_output=True)
        time.sleep(0.5)
        self.d = u2.connect(device_id)
        self.steps = []
        self._cur_step = None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.case_dir = case_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "screenshots", f"case_{ts}")
        os.makedirs(self.case_dir, exist_ok=True)
        self._shot_idx = 0
        self._ocr = None

    # ── 步骤管理 ────────────────────────────────────────────────────
    def step(self, name):
        """开启一个步骤，返回 self（支持 with 或直接调用）"""
        self._cur_step = {"name": name, "results": [], "evidences": []}
        self.steps.append(self._cur_step)
        print(f"\n▶ [{name}]")
        return self

    def record(self, result, detail):
        """记录一条断言结果: result ∈ {PASS, FAIL, WARN, INFO, BLOCKED}"""
        self._cur_step["results"].append({"result": result, "detail": detail})
        mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️",
                "BLOCKED": "⛔"}[result]
        print(f"   {mark} {detail}")
        return result == "PASS"

    def blocked(self, reason):
        """环境/前置不满足，无法执行"""
        return self.record("BLOCKED", f"阻塞: {reason}")

    # ── 元素操作 ────────────────────────────────────────────────────
    def _el(self, rid=None, text=None):
        if rid:
            return self.d.xpath(f'//*[@resource-id="{rid}"]')
        if text:
            return self.d(text=text)
        raise ValueError("需要 rid 或 text")

    def tap_rid(self, rid):
        self._el(rid=rid).click()
        time.sleep(ACTION_DELAY)
        return self

    def tap_text(self, text, wait=5.0):
        """点文字按钮；元素未出现时轮询等待（防导航/时序抖动）"""
        for _ in range(int(wait / 0.5)):
            try:
                if self.d(text=text).click_exists(timeout=0.3):
                    time.sleep(ACTION_DELAY)
                    return self
            except Exception:
                pass
            time.sleep(0.5)
        self.record("WARN", f"tap_text 未找到元素: {text!r}")
        return self

    def el_bounds(self, rid=None, text=None, desc=None, xpath=None):
        """按 资源id/文字/内容描述/xpath 定位元素，返回 bounds (x1,y1,x2,y2) 或 None"""
        xml = self.d.dump_hierarchy()
        for n in re.findall(r"<node[^>]*>", xml):
            ok = False
            if rid and re.search(rf'resource-id="{re.escape(rid)}"', n):
                ok = True
            elif text and re.search(rf'text="{re.escape(text)}"', n):
                ok = True
            elif desc and re.search(rf'content-desc="{re.escape(desc)}"', n):
                ok = True
            if not ok:
                continue
            b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
            if b:
                return tuple(map(int, b.groups()))
        return None

    def tap_el(self, rid=None, text=None, desc=None, xpath=None, wait=5.0):
        """按 资源id/文字/内容描述/xpath 点击（元素定位优先，坐标兜底）"""
        for _ in range(int(wait / 0.5)):
            b = self.el_bounds(rid=rid, text=text, desc=desc)
            if b:
                x1, y1, x2, y2 = b
                self.d.click((x1 + x2) // 2, (y1 + y2) // 2)
                time.sleep(ACTION_DELAY)
                return self
            time.sleep(0.5)
        self.record("WARN", f"tap_el 未找到元素: rid={rid} text={text} desc={desc}")
        return self

    def tap_desc(self, desc, wait=5.0):
        """按 content-desc 点击（图标按钮常用）"""
        return self.tap_el(desc=desc, wait=wait)

    def tap_xy(self, x, y):
        """坐标点击（最后手段；优先用 tap_el/tap_text/tap_rid）"""
        self.d.click(x, y)
        time.sleep(ACTION_DELAY)
        return self

    def input_text(self, rid, text):
        self._el(rid=rid).click()
        time.sleep(ACTION_DELAY)
        self.d.send_keys(text)
        time.sleep(ACTION_DELAY)
        return self

    def clear_text(self, rid):
        self._el(rid=rid).click()
        time.sleep(ACTION_DELAY)
        self.d.clear_text()
        time.sleep(ACTION_DELAY)
        return self

    def read_rid(self, rid):
        """读取元素属性字典: text/checked/enabled/clickable/bounds"""
        xml = self.d.dump_hierarchy()
        for n in re.findall(r"<node[^>]*>", xml):
            if re.search(rf'resource-id="{re.escape(rid)}"', n):
                g = lambda k: (re.search(rf'{k}="([^"]*)"', n) or [None, None])[1] if re.search(rf'{k}="([^"]*)"', n) else None
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
                bounds = tuple(map(int, b.groups())) if b else None
                return {"text": g("text"), "checked": g("checked"),
                        "enabled": g("enabled"), "clickable": g("clickable"),
                        "bounds": bounds}
        return None

    def first_clickable(self, y_min, y_max):
        """在指定 y 区间找第一个可点击元素中心（按 y 从小到大）"""
        xml = self.d.dump_hierarchy()
        cands = []
        for n in re.findall(r"<node[^>]*>", xml):
            if 'clickable="true"' not in n:
                continue
            b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
            if not b:
                continue
            x1, y1, x2, y2 = map(int, b.groups())
            if y_min <= y1 <= y_max:
                cands.append(((x1 + x2) // 2, (y1 + y2) // 2, y1))
        cands.sort(key=lambda c: c[2])
        return (cands[0][0], cands[0][1]) if cands else None

    # ── 系统级操作（通用前置条件）────────────────────────────────
    def adb_shell(self, *args):
        """执行 adb shell 命令，返回 stdout"""
        r = subprocess.run(["adb", "shell", *args],
                           capture_output=True, text=True)
        return r.stdout.strip()

    def pm_clear(self, package, confirm=True):
        """清空 App 数据，重置到首次使用状态（pm clear）"""
        out = self.adb_shell("pm", "clear", package)
        ok = "Success" in out
        if confirm:
            self.record("PASS" if ok else "FAIL",
                        f"pm clear {package}: {'成功' if ok else '失败 ' + out}")
        return ok

    def force_stop(self, package):
        """强制停止 App"""
        return self.adb_shell("am", "force-stop", package)

    def getprop(self, name):
        """读系统属性，如: getprop('ro.build.type') / getprop('persist.sys.xxx')"""
        return self.adb_shell("getprop", name)

    def settings_get(self, scope, key):
        """读系统设置: scope ∈ global|secure|system"""
        return self.adb_shell("settings", "get", scope, key)

    def settings_put(self, scope, key, value):
        """写系统设置"""
        return self.adb_shell("settings", "put", scope, key, value)

    def has_network(self):
        """设备是否有活动网络（dumpsys connectivity）"""
        out = self.adb_shell("dumpsys", "connectivity")
        return "Active default network: none" not in out

    def grant_permission(self, package, permission):
        """授予运行时权限"""
        return self.adb_shell("pm", "grant", package, permission)

    def current_activity(self):
        """当前前台完整 Activity（如 com.zui.calendar/.timetable.display.TimetableActivity）"""
        out = subprocess.run(
            ["adb", "shell", "dumpsys", "activity", "activities"],
            capture_output=True, text=True,
        ).stdout
        m = re.search(r"topResumedActivity=ActivityRecord\{\S* u0 ([\w./]+) ", out)
        if not m:
            m = re.search(r"ResumedActivity: ActivityRecord\{\S* u0 ([\w./]+) ", out)
        return m.group(1) if m else "unknown"

    def top_bar_icons(self):
        """工具栏图标列表 [(cx,cy,desc,class), ...] 按 x 排序。
        元素化定位：工具栏带 [100,450] 内可点击、无文字的图标元素（日期等有文字项排除）"""
        xml = self.d.dump_hierarchy()
        items = []
        for n in re.findall(r"<node[^>]*>", xml):
            if 'clickable="true"' not in n:
                continue
            b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
            if not b:
                continue
            x1, y1, x2, y2 = map(int, b.groups())
            if not (100 <= y1 <= 450):
                continue
            text = re.search(r'text="([^"]*)"', n)
            if text and text.group(1):
                continue
            desc = re.search(r'content-desc="([^"]*)"', n)
            d = desc.group(1) if desc else ""
            if "返回" in d or "上一层级" in d or "back" in d.lower():
                continue   # 排除返回箭头
            cls = re.search(r'class="([^"]*)"', n)
            items.append(((x1 + x2) // 2, (y1 + y2) // 2, d,
                          (cls.group(1) if cls else "").split(".")[-1]))
        items.sort(key=lambda i: i[0])
        return items

    def top_rightmost_icon(self):
        """右上角最右侧图标按钮（元素化：取 top_bar_icons 最右一个）"""
        icons = self.top_bar_icons()
        return (icons[-1][0], icons[-1][1]) if icons else None

    def open_more_menu(self, retries=8):
        """打开右上角'更多'菜单并验证出现'课程表'项。
        元素定位+重试：等待 App 就绪（工具栏图标出现）+ 抗转场抖动"""
        for _ in range(retries):
            icons = self.top_bar_icons()
            if not icons:
                time.sleep(1.5)
                continue
            self.tap_xy(*icons[-1][:2])
            time.sleep(1.2)
            if any("课程表" in x for x in self.screen_text()):
                return True
            time.sleep(1)
        return False

    def _screen_size(self):
        raw = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                             capture_output=True).stdout
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")

    def dismiss_first_use_dialogs(self, policy="allow", max_rounds=12, verbose=False):
        """
        处理首次使用弹窗直到主界面出现。
        注意: Android 运行时权限弹窗约 8 秒自动消失，必须"检测即点"（click_exists 快速尝试）。
        policy: "allow"=允许全部权限 | "deny"=拒绝全部权限
        """
        allow_words = ["允许", "同意", "始终允许", "仅在使用中允许", "仅在使用时允许", "仅本次使用时允许", "全部允许", "选择照片"]
        deny_words = ["拒绝并不再询问", "拒绝", "不允许", "禁止"]
        guide_words = ["我知道了", "知道了", "立即开始", "开始使用"]
        for _ in range(max_rounds):
            hit = False
            # 1) 引导/提示弹窗（无歧义，直接关）
            for w in guide_words:
                if self.d(text=w).click_exists(timeout=1.2):
                    if verbose:
                        self.record("INFO", f"关闭引导/提示弹窗: {w}")
                    time.sleep(0.6)
                    hit = True
                    break
            if hit:
                continue
            # 2) 权限弹窗（按策略立即点）
            words = allow_words if policy == "allow" else deny_words
            for w in words:
                if self.d(text=w).click_exists(timeout=1.2):
                    if verbose:
                        self.record("INFO",
                                    f"权限弹窗已{'同意' if policy=='allow' else '拒绝'}: {w}")
                    time.sleep(0.6)
                    hit = True
                    break
            if not hit:
                return True   # 无弹窗，已进入主界面
        return False

    # ── 弹窗看门狗：检测到权限/引导弹窗立即点击（8秒消失规则）────────
    def start_watchdog(self, policy="allow", interval=0.3, verbose=True):
        """
        启动后台看门狗线程：持续监视界面，检测到权限/引导弹窗立即点击。
        policy: "allow"=点同意/允许 | "deny"=点拒绝
        与主流程并行，独立 u2 连接，毫秒级响应。
        """
        import threading
        self._wd_stop = threading.Event()
        self._wd_policy = [policy]          # 可动态修改（线程间共享）
        self._wd = threading.Thread(
            target=self._watchdog_loop, args=(self._wd_policy, interval, verbose),
            daemon=True)
        self._wd.start()
        if verbose:
            print("🛡️  看门狗已启动 (policy=%s)" % policy)

    def watchdog_policy(self, policy):
        """动态切换看门狗权限策略（allow=点同意/允许，deny=点拒绝）"""
        if hasattr(self, "_wd_policy"):
            self._wd_policy[0] = policy
            print(f"🛡️  看门狗策略切换: {policy}")
        return self

    def watchdog_pause(self):
        """临时暂停看门狗（用于手动处理弹窗验证场景）"""
        if hasattr(self, "_wd_pause"):
            self._wd_pause[0] = True
            print("🛡️  看门狗已暂停")

    def watchdog_resume(self):
        """恢复看门狗"""
        if hasattr(self, "_wd_pause"):
            self._wd_pause[0] = False
            print("🛡️  看门狗已恢复")

    def _watchdog_loop(self, policy_box, interval, verbose):
        import uiautomator2 as u2
        try:
            dw = u2.connect()
        except Exception as e:
            print(f"[看门狗] 连接失败: {e}")
            return
        # 按出现频率排序：检测到即点击，轮询要快（弹窗 6-8 秒自动消失）
        allow_words = ("同意", "允许", "仅在使用时允许", "全部允许", "选择照片",
                       "始终允许", "仅本次使用时允许", "仅在使用中允许")
        deny_words = ("拒绝并不再询问", "拒绝", "不允许", "禁止")
        guide_words = ("我知道了", "知道了")
        self._wd_pause = [False]
        while not self._wd_stop.is_set():
            if self._wd_pause[0]:
                self._wd_stop.wait(interval)
                continue
            policy = policy_box[0]
            words = guide_words + (allow_words if policy == "allow" else deny_words)
            # 快速 dump 检测（~0.3s），只对存在的词做 click_exists
            try:
                xml = dw.dump_hierarchy()
            except Exception:
                self._wd_stop.wait(interval)
                continue
            for w in words:
                if f'text="{w}"' not in xml:
                    continue
                # 弹窗可能还在滑入动画：等 0.5s 稳定后再点击
                self._wd_stop.wait(0.5)
                try:
                    if dw(text=w).click_exists(timeout=0.5):
                        if verbose:
                            print(f"🛡️  [看门狗] 已点击弹窗按钮: {w}")
                    else:
                        if verbose:
                            print(f"🛡️  [看门狗] 检测到但点击失败: {w}")
                except Exception:
                    pass
                break   # 一轮只处理一个按钮
            self._wd_stop.wait(interval)

    def stop_watchdog(self):
        """停止看门狗"""
        if hasattr(self, "_wd_stop"):
            self._wd_stop.set()
            print("🛡️  看门狗已停止")

    def current_package(self):
        out = subprocess.run(["adb", "shell", "dumpsys", "activity", "activities"],
                             capture_output=True, text=True).stdout
        m = re.search(r"topResumedActivity=ActivityRecord\{\S* u0 ([\w.]+)/", out)
        return m.group(1) if m else "unknown"

    # ── 断言 ────────────────────────────────────────────────────────
    def assert_equals(self, actual, expect, msg=""):
        return self.record("PASS" if actual == expect else "FAIL",
                           f"{msg} 期望={expect!r} 实际={actual!r}")

    def assert_text(self, rid, expect, msg="文本断言"):
        v = self.read_rid(rid)
        actual = v["text"] if v else None
        return self.record("PASS" if actual == expect else "FAIL",
                           f"{msg}: 期望={expect!r} 实际={actual!r}")

    def assert_switch(self, rid, expect, msg="开关状态"):
        v = self.read_rid(rid)
        actual = v["checked"] if v else None
        return self.record("PASS" if actual == expect else "FAIL",
                           f"{msg}: 期望={expect} 实际={actual}")

    def assert_true(self, cond, msg):
        return self.record("PASS" if cond else "FAIL", msg)

    def assert_length_le(self, rid, limit, msg="长度上限"):
        v = self.read_rid(rid)
        actual = len(v["text"]) if v and v["text"] else 0
        return self.record("PASS" if actual <= limit else "FAIL",
                           f"{msg}: {limit} 实际={actual}")

    # ── 置灰断言（截图裁剪 + 颜色对比度）────────────────────────────
    def _region_contrast(self, bounds, scale=3):
        """计算按钮区域内文字与背景的对比度（0-255 差值）"""
        x1, y1, x2, y2 = bounds
        raw = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                             capture_output=True).stdout
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("L")
        crop = img.crop((x1, y1, x2, y2))
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
        px = list(crop.getdata())
        # 背景 = 众数附近的亮度；文字 = 与背景差异大的像素
        bg = sorted(px)[len(px) // 2]
        text_px = [p for p in px if abs(p - bg) > 40]
        if not text_px:
            return 0.0
        text_brightness = sum(text_px) / len(text_px)
        return abs(text_brightness - bg)

    def assert_grayed(self, rid, ref_contrast, msg="置灰断言", ratio=0.5):
        """
        断言按钮置灰: 当前对比度 < 参考对比度 * ratio
        ref_contrast: 按钮正常(可点击)态下的文字对比度
        """
        v = self.read_rid(rid)
        if not v or not v["bounds"]:
            return self.record("FAIL", f"{msg}: 元素不存在")
        cur = self._region_contrast(v["bounds"])
        grayed = cur < ref_contrast * ratio
        return self.record("PASS" if grayed else "FAIL",
                           f"{msg}: 对比度 {cur:.0f} vs 参考 {ref_contrast:.0f} "
                           f"→ {'置灰' if grayed else '未置灰'}")

    def contrast_of(self, rid):
        """获取元素当前文字对比度（供 assert_grayed 作参考）"""
        v = self.read_rid(rid)
        if not v or not v["bounds"]:
            return 0.0
        return self._region_contrast(v["bounds"])

    # ── 证据与辅助 ──────────────────────────────────────────────────
    def screenshot(self, label):
        self._shot_idx += 1
        path = os.path.join(self.case_dir, f"{self._shot_idx:02d}_{label}.png")
        subprocess.run(["adb", "exec-out", "screencap", "-p"],
                       stdout=open(path, "wb"))
        self._cur_step["evidences"].append(path)
        print(f"   📷 {path}")
        return path

    def capture_toast(self, wait=1.5, max_lines=3):
        """捕捉 Toast: 读 logcat 最近 Toast 文本"""
        subprocess.run(["adb", "logcat", "-c"], capture_output=True)
        time.sleep(wait)
        out = subprocess.run(["adb", "logcat", "-d", "-s", "Toast"],
                             capture_output=True, text=True).stdout
        m = re.findall(r"showToast.*?text=([^\s]+)", out)
        return m[-1] if m else None

    def screen_text(self):
        xml = self.d.dump_hierarchy()
        return [m.group(1) for n in re.findall(r"<node[^>]*>", xml)
                if (m := re.search(r'text="([^"]*)"', n)) and m.group(1)]

    # ── OCR（Canvas 内容读取）────────────────────────────────────────
    def ocr(self, y_min=0, y_max=99999, x_min=0, x_max=99999):
        """截屏 + rapidocr，返回 [(x, y, conf, text)]（原图像素坐标）"""
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr = RapidOCR()
        import numpy as np
        from PIL import Image
        raw = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                             capture_output=True).stdout
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        s = 1600 / max(w, h)
        img2 = img.resize((int(w * s), int(h * s)))
        res, _ = self._ocr(np.asarray(img2))
        out = []
        for box, text, conf in res or []:
            xs = [p[0] / s for p in box]
            ys = [p[1] / s for p in box]
            cx, cy = int(sum(xs) / 4), int(sum(ys) / 4)
            if y_min <= cy <= y_max and x_min <= cx <= x_max:
                out.append((cx, cy, float(conf), text))
        return out

    def ocr_find(self, keyword, y_min=0, y_max=99999):
        """在 OCR 结果中找包含关键字的项，返回第一个 (x, y) 或 None"""
        for x, y, c, t in self.ocr(y_min, y_max):
            if keyword in t:
                return (x, y)
        return None

    # ── 报告 ────────────────────────────────────────────────────────
    def finish(self):
        os.makedirs(REPORT_DIR, exist_ok=True)
        path = os.path.join(REPORT_DIR, f"{self.name}_报告.md")
        lines = [f"# 测试报告：{self.name}",
                 f"\n**测试日期**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
                 f"**设备**：{self.d.app_current()['package']}",
                 f"**证据目录**：{self.case_dir}\n"]
        total = pass_n = 0
        for s in self.steps:
            lines.append(f"\n## {s['name']}")
            for r in s["results"]:
                total += 1
                if r["result"] == "PASS":
                    pass_n += 1
                mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️",
                        "BLOCKED": "⛔"}[r["result"]]
                lines.append(f"- {mark} {r['detail']}")
            for ev in s["evidences"]:
                lines.append(f"  - 证据: `{ev}`")
        lines.append(f"\n---\n**汇总**: {pass_n}/{total} 通过")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n📄 报告已生成: {path}")
        return path
