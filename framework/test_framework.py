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

# 弹窗自动点击词表（u2 原生 watcher 注册用）
DIALOG_GUIDE_WORDS = ("我知道了", "知道了", "立即开始", "开始使用")
DIALOG_ALLOW_WORDS = ("允许", "同意", "始终允许", "仅在使用中允许",
                      "仅在使用时允许", "仅本次使用时允许", "全部允许", "选择照片")
DIALOG_DENY_WORDS = ("拒绝并不再询问", "拒绝", "不允许", "禁止")

# AI 学习词表持久化文件：AI 处理过的未知弹窗按钮自动并入，下次走快路径
LEARNED_WORDS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dialog_words.json")


class TestCase:
    def __init__(self, name, device_id=None, case_dir=None, user_input=None, script_path=None):
        self.name = name
        # 未显式传入时，从环境变量取（run_case.py 注入：用户原始输入 + 脚本路径）
        self.user_input = user_input if user_input is not None \
            else os.environ.get("DSH_CASE_USER_INPUT")
        self.script_path = script_path if script_path is not None \
            else os.environ.get("DSH_CASE_SCRIPT_PATH")
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
        self._vision = None
        # 弹窗 watcher 状态：单连接 + 主流程驱动，无独立线程、无并发 dump
        self._wd_enabled = False
        self._wd_policy = "allow"
        # AI 未知弹窗处理：节流（同一次动作窗口内最多 N 次 AI 调用）
        self._ai_dialog_calls = 0
        self._ai_dialog_max = 3        # 每次动作窗口最多 AI 处理几次（防失控连环点）
        self._ai_dialog_last = 0.0     # 上次 AI 调用时间戳
        self._ai_dialog_interval = 8.0 # 两次 AI 调用最小间隔（秒）
        # SQLite 记录：用例/步骤/结果入库
        self._db = None
        self._db_case_id = None
        self._db_step_id = None
        self._db_step_ord = 0
        # 建用例记录（失败不阻塞测试）
        try:
            from db import get_db
            self._db = get_db()
            self._db_case_id = self._db.start_case(
                self.name, str(self.d.app_current().get("package", "")),
                user_input=self.user_input, script_path=self.script_path)
        except Exception:
            self._db = None

    # ── 视觉模型通道（颜色/布局/OCR 盲区检查）────────────────────────
    def _get_vision(self):
        """懒加载视觉模型客户端（deepseek-v4-flash-vision-exp）"""
        if self._vision is None:
            from vision import Vision
            self._vision = Vision()
        return self._vision

    def _vision_crop_bytes(self, rid=None, bounds=None):
        """截取屏幕（或裁剪到元素区域），返回 PNG 字节。
        优先按元素 bounds 裁剪：聚焦目标、省 token、判断更准。"""
        raw = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                             capture_output=True).stdout
        if not (rid or bounds):
            return raw
        b = bounds
        if b is None:
            v = self.read_rid(rid)
            b = v["bounds"] if v else None
        if not b:
            return raw
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            x1, y1, x2, y2 = b
            x1, y1 = max(0, x1 - 20), max(0, y1 - 20)
            x2, y2 = min(img.width, x2 + 20), min(img.height, y2 + 20)
            crop = img.crop((x1, y1, x2, y2))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return raw

    def vision_ask(self, prompt, rid=None, bounds=None):
        """通用视觉问答：截图（可裁剪到元素）→ 文本结论"""
        return self._get_vision().ask(prompt, self._vision_crop_bytes(rid=rid, bounds=bounds))

    def assert_visual(self, prompt, expect, msg="视觉断言", rid=None, bounds=None):
        """视觉断言：让视觉模型判断截图状态，期望命中关键词（expect 可含多个任一词）。
        用于颜色/布局/样式等 UI 树读不到、像素断言又不可靠的场景。"""
        try:
            answer = self.vision_ask(prompt, rid=rid, bounds=bounds)
        except Exception as e:
            return self.record("WARN", f"{msg}: 视觉调用失败 {e}")
        expects = [expect] if isinstance(expect, str) else list(expect)
        ok = any(e in answer for e in expects)
        return self.record("PASS" if ok else "FAIL",
                           f"{msg}: 视觉模型回答={answer!r}", rid=rid)

    def assert_button_state_visual(self, rid, expected, msg="视觉按钮状态断言"):
        """视觉按钮状态断言：直接让视觉模型判断按钮置灰/可点击。
        expected: 'grayed'=断言置灰 | 'clickable'=断言可点击。
        替代 _region_contrast 像素法（后者只能测亮度差，对样式变化不可靠）。"""
        state = "grayed" if expected == "grayed" else "clickable"
        prompt = ("这个 Android 界面元素处于什么状态？请判断它是否被置灰（disabled/不可点击）。"
                  "只回答：置灰 或 可点击。")
        expect = ("置灰", "灰", "不可点击", "禁用") if state == "grayed" else ("可点击", "可用")
        return self.assert_visual(prompt, expect, msg=msg, rid=rid)

    def assert_grayed_visual(self, rid, msg="视觉置灰断言"):
        """视觉置灰断言（等价 assert_button_state_visual(rid, 'grayed')）"""
        return self.assert_button_state_visual(rid, "grayed", msg=msg)

    # ── 弹窗自动点击（u2 原生 watcher，主流程驱动，零额外 dump）──────
    def _load_learned_words(self):
        """读取 AI 学习词表（AI 处理过的未知弹窗按钮），返回 {category: [words]}"""
        try:
            import json
            with open(LEARNED_WORDS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return {k: list(v) for k, v in data.items()}
        except (OSError, ValueError):
            return {"guide": [], "allow": [], "deny": []}

    def _learn_word(self, category, word):
        """AI 命中后学习按钮文字：持久化并入词表，下次同款弹窗走快路径"""
        if not word or len(word) > 30:
            return
        learned = self._load_learned_words()
        if word in learned.get(category, []):
            return
        learned.setdefault(category, []).append(word)
        try:
            import json
            with open(LEARNED_WORDS_FILE, "w", encoding="utf-8") as f:
                json.dump(learned, f, ensure_ascii=False, indent=2)
            print(f"🧠 [AI弹窗] 已学习按钮 {word!r} → {category} 词表")
        except OSError as e:
            print(f"[AI弹窗] 学习词表写入失败: {e}")

    def _dialog_words(self, policy):
        """当前策略下的弹窗词：内置词表 + AI 学习词表"""
        learned = self._load_learned_words()
        if policy == "deny":
            return DIALOG_GUIDE_WORDS + tuple(learned.get("guide", [])) \
                + DIALOG_DENY_WORDS + tuple(learned.get("deny", []))
        return DIALOG_GUIDE_WORDS + tuple(learned.get("guide", [])) \
            + DIALOG_ALLOW_WORDS + tuple(learned.get("allow", []))

    def _register_dialog_watchers(self, policy=None):
        """注册 u2 原生 watcher：命中词即点击。
        匹配与点击都复用已 dump 的 source（PageSource），不产生新 dump。
        检查由主流程每次 dump 后调用 _run_dialog_watchers 触发。"""
        policy = policy or self._wd_policy
        self._wd_policy = policy
        try:
            self.d.watcher.reset()
            for w in self._dialog_words(policy):
                self.d.watcher.when(w).click()
        except Exception as e:
            print(f"[watcher] 注册失败: {e}")

    def _run_dialog_watchers(self, xml):
        """在已 dump 的 XML 上运行弹窗 watcher（不重新 dump）。
        两层：① 词表命中 → u2 watcher 点击（毫秒级）；② 词表未命中但疑似弹窗
        → AI 视觉识别（节流 + 置信度门槛 + 次数上限），覆盖未知弹窗。"""
        if not self._wd_enabled or not xml:
            return
        words = self._dialog_words(self._wd_policy)
        if any(f'text="{w}"' in xml for w in words):
            try:
                from uiautomator2.xpath import PageSource
                self.d.watcher.run(PageSource.parse(xml))
            except Exception:
                pass
            return
        # 词表未命中：疑似未知弹窗 → AI 兜底
        self._handle_unknown_dialog(xml)

    def _handle_unknown_dialog(self, xml):
        """AI 处理未知弹窗：词表未命中时，用视觉模型识别弹窗并决策。
        触发条件：UI 树存在可点击文本节点（说明有交互浮层/对话框）。
        保护：节流（min 间隔）+ 次数上限（防失控连环点）+ 置信度门槛。"""
        # 无任何可点击文本 → 不是可交互弹窗，不触发 AI
        if not re.search(r'clickable="true"[^>]*text="[^"]+"', xml) \
           and not re.search(r'text="[^"]+"[^>]*clickable="true"', xml):
            return
        # 页面内常见按钮词（非弹窗）→ 跳过，避免把 App 普通页面误判成弹窗
        page_words = ("完成", "取消", "确定", "左转", "右转", "上一步", "下一步",
                      "保存", "删除", "添加", "更多", "设置", "返回")
        for w in page_words:
            if f'text="{w}"' in xml:
                return
        # 节流：距上次 AI 调用不足间隔 → 跳过
        now = time.time()
        if now - self._ai_dialog_last < self._ai_dialog_interval:
            return
        # 次数上限
        if self._ai_dialog_calls >= self._ai_dialog_max:
            return
        self._ai_dialog_calls += 1
        self._ai_dialog_last = now
        try:
            raw = subprocess.run(["adb", "exec-out", "screencap", "-p"],
                                 capture_output=True).stdout
            res = self._get_vision().ask_json(
                "这是 Android 设备截图。仅当屏幕上出现【模态弹窗/对话框】（居中浮层，"
                "背景变暗被遮罩，通常带标题和确定/取消按钮）时 is_dialog 才为 true。"
                "普通页面上的工具栏按钮、编辑表单、列表项【不算弹窗】。"
                "如果确认为弹窗，识别它并给出处理建议。只输出 JSON："
                "{\"is_dialog\": true/false, \"title\": \"弹窗标题\", "
                "\"buttons\": [\"按钮文字列表\"], "
                "\"action\": \"close|allow|deny|skip\", "
                "\"button_to_click\": \"建议点击的按钮完整文字\", "
                "\"confidence\": 0到1的置信度}。"
                "action 含义: close=点关闭/取消/知道了类按钮 dismiss 掉它; "
                "allow=点允许/同意/确定类按钮; deny=点拒绝类按钮; "
                "skip=不应自动点击（如需要用户选择/输入）。"
                "没有弹窗时 is_dialog=false, action=skip。",
                raw,
                fields=["is_dialog", "title", "buttons", "action",
                        "button_to_click", "confidence"])
        except Exception as e:
            print(f"🤖 [AI弹窗] 识别失败: {e}")
            return
        if not res.get("is_dialog"):
            return
        conf = float(res.get("confidence") or 0)
        action = str(res.get("action") or "skip")
        btn = str(res.get("button_to_click") or "").strip()
        title = str(res.get("title") or "?")
        # 决策：按当前策略 + AI 建议 + 置信度门槛
        if conf < 0.7 or not btn:
            print(f"🤖 [AI弹窗] 置信度不足({conf:.2f})或未给出按钮，跳过: {title}")
            return
        if action == "skip":
            print(f"🤖 [AI弹窗] AI 建议不自动点击（{title}），记录后跳过")
            self.record("WARN", f"未知弹窗需人工确认: {title} (AI 建议不自动点)")
            return
        if action == "deny" and self._wd_policy != "deny":
            print(f"🤖 [AI弹窗] AI 建议拒绝但策略是 {self._wd_policy}，跳过: {title}")
            return
        if action == "allow" and self._wd_policy != "allow":
            print(f"🤖 [AI弹窗] AI 建议允许但策略是 {self._wd_policy}，跳过: {title}")
            return
        # 执行点击：优先按按钮文字点，失败则记录
        try:
            # disabled 按钮点击无效，跳过并提示（如分享选择器里未选目标时的"仅此一次"）
            import io as _io
            xml_now = self.d.dump_hierarchy()
            m = re.search(rf'<node[^>]*text="{re.escape(btn)}"[^>]*enabled="(true|false)"', xml_now)
            if m and m.group(1) == "false":
                print(f"🤖 [AI弹窗] 按钮 {btn!r} 当前 disabled，跳过（{title}）")
                return
            if self.d(text=btn).click_exists(timeout=0.6):
                print(f"🤖 [AI弹窗] 已按 AI 建议点击 {btn!r}（{title}）")
                # 学习：按钮文字并入对应词表，下次同款弹窗走快路径
                cat = {"close": "guide", "allow": "allow", "deny": "deny"}.get(action)
                if cat:
                    self._learn_word(cat, btn)
            else:
                self.record("WARN", f"AI 建议点 {btn!r} 但未找到按钮（{title}）")
        except Exception:
            pass

    def _check_dialogs_after_action(self, rounds=10, interval=0.5):
        """点击/输入等动作后检查弹窗：动作常触发弹窗（可能连续多个——
        App 提示"知道了"→ 系统权限弹窗），在窗口期内持续 dump 喂 watcher，
        命中即点击，不提前退出窗口（权限弹窗 6-8s 自动消失，必须检测即点）。"""
        if not self._wd_enabled:
            return False
        try:
            from uiautomator2.xpath import PageSource
        except Exception:
            return False
        handled = False
        for _ in range(rounds):
            try:
                if not self._wd_enabled:
                    break
                xml = self.d.dump_hierarchy()
                words = self._dialog_words(self._wd_policy)
                if any(f'text="{w}"' in xml for w in words):
                    if self.d.watcher.run(PageSource.parse(xml)):
                        handled = True
                else:
                    # 词表未命中：疑似未知弹窗 → AI 兜底
                    self._handle_unknown_dialog(xml)
            except Exception:
                pass
            time.sleep(interval)
        return handled

    # ── 步骤管理 ────────────────────────────────────────────────────
    def step(self, name):
        """开启一个步骤，返回 self（支持 with 或直接调用）"""
        self._cur_step = {"name": name, "results": [], "evidences": []}
        self.steps.append(self._cur_step)
        if self._db is not None and self._db_case_id is not None:
            try:
                self._db_step_ord += 1
                self._db_step_id = self._db.add_step(
                    self._db_case_id, name, self._db_step_ord)
            except Exception:
                self._db_step_id = None
        print(f"\n▶ [{name}]")
        return self

    def record(self, result, detail, rid=None, evidence=True):
        """记录一条断言结果: result ∈ {PASS, FAIL, WARN, INFO, BLOCKED}
        - rid: 关联元素 resource-id，自动附 read_rid 状态（enabled/selected/checked/clickable）
        - evidence: FAIL/WARN 时自动截屏留证（默认 True）
        """
        entry = {"result": result, "detail": detail}
        # 状态快照：状态类断言必须记录实际状态值（以 case 为准原则）
        if rid:
            v = self.read_rid(rid)
            if v:
                states = {k: v[k] for k in ("enabled", "selected", "checked", "clickable")
                          if k in v}
                entry["state"] = states        # FAIL/WARN/BLOCKED 自动截屏留证（不打断正常流程）
        if evidence and result in ("FAIL", "WARN", "BLOCKED"):
            try:
                self._shot_idx += 1
                path = os.path.join(self.case_dir,
                                    f"{self._shot_idx:02d}_证据_{result}.png")
                subprocess.run(["adb", "exec-out", "screencap", "-p"],
                               stdout=open(path, "wb"))
                entry["evidence"] = path
            except Exception:
                pass
        self._cur_step["results"].append(entry)
        # 入库（失败不阻塞测试）
        if self._db is not None and self._db_step_id is not None:
            try:
                self._db.add_result(self._db_step_id, result, detail,
                                    state=entry.get("state"),
                                    evidence=entry.get("evidence"))
            except Exception:
                pass
        mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️",
                "BLOCKED": "⛔"}[result]
        print(f"   {mark} {entry['detail']}")
        if entry.get("state"):
            print(f"   📊 状态 {entry['state']}")
        if entry.get("evidence"):
            print(f"   📷 {entry['evidence']}")
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
        self._check_dialogs_after_action()
        return self

    def tap_text(self, text, wait=5.0):
        """点文字按钮；元素未出现时轮询等待（防导航/时序抖动）"""
        for _ in range(int(wait / 0.5)):
            try:
                if self.d(text=text).click_exists(timeout=0.3):
                    time.sleep(ACTION_DELAY)
                    self._check_dialogs_after_action()
                    return self
            except Exception:
                pass
            time.sleep(0.5)
        self.record("WARN", f"tap_text 未找到元素: {text!r}")
        return self

    def el_bounds(self, rid=None, text=None, desc=None, xpath=None):
        """按 资源id/文字/内容描述/xpath 定位元素，返回 bounds (x1,y1,x2,y2) 或 None"""
        xml = self.d.dump_hierarchy()
        self._run_dialog_watchers(xml)
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
                self._check_dialogs_after_action()
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
        self._check_dialogs_after_action()
        return self

    def input_text(self, rid, text):
        self._el(rid=rid).click()
        time.sleep(ACTION_DELAY)
        self.d.send_keys(text)
        time.sleep(ACTION_DELAY)
        self._check_dialogs_after_action()
        return self

    def clear_text(self, rid):
        self._el(rid=rid).click()
        time.sleep(ACTION_DELAY)
        self.d.clear_text()
        time.sleep(ACTION_DELAY)
        return self

    def read_rid(self, rid):
        """读取元素属性字典: text/checked/enabled/selected/clickable/bounds"""
        xml = self.d.dump_hierarchy()
        self._run_dialog_watchers(xml)
        for n in re.findall(r"<node[^>]*>", xml):
            if re.search(rf'resource-id="{re.escape(rid)}"', n):
                g = lambda k: (re.search(rf'{k}="([^"]*)"', n) or [None, None])[1] if re.search(rf'{k}="([^"]*)"', n) else None
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
                bounds = tuple(map(int, b.groups())) if b else None
                return {"text": g("text"), "checked": g("checked"),
                        "enabled": g("enabled"), "selected": g("selected"),
                        "clickable": g("clickable"), "bounds": bounds}
        return None

    def first_clickable(self, y_min, y_max):
        """在指定 y 区间找第一个可点击元素中心（按 y 从小到大）"""
        xml = self.d.dump_hierarchy()
        self._run_dialog_watchers(xml)
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
        self._run_dialog_watchers(xml)
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
        策略：每轮只 dump 一次 UI 树，在 XML 里字符串匹配弹窗词，
        命中才点击（短超时）——避免旧版逐词 click_exists(timeout=1.2)
        的累计等待（最坏 12 词 × 1.2s ≈ 14s）。
        注意: Android 运行时权限弹窗约 8 秒自动消失，必须"检测即点"。
        调用方在启动 App 后调用时，首轮先等 ACTION_DELAY（界面渲染/首帧未就绪
        时 dump 会误判"无弹窗"）。
        """
        # 首轮前统一等待：启动/转场后界面未渲染完时，dump 会误判无弹窗
        time.sleep(ACTION_DELAY)
        for _ in range(max_rounds):
            hit = False
            # 1) 先做一次轻量 dump，字符串匹配（微秒级），命中才真正点击
            xml = self.d.dump_hierarchy()
            words = self._dialog_words(policy)
            for w in words:
                if f'text="{w}"' not in xml:
                    continue
                if self.d(text=w).click_exists(timeout=0.3):
                    if verbose:
                        self.record("INFO",
                                    f"弹窗已{'同意' if policy=='allow' else '拒绝'}: {w}")
                    time.sleep(ACTION_DELAY)
                    hit = True
                    break
            if not hit:
                # 2) 词表未命中：疑似未知弹窗 → AI 兜底（首启阶段同样适用）
                self._handle_unknown_dialog(xml)
            if not hit:
                return True   # 无弹窗，已进入主界面
        return False

    # ── 弹窗自动点击：主流程驱动，单连接，零并发 dump ────────────────
    def start_watchdog(self, policy="allow", interval=0.3, verbose=True):
        """
        启用弹窗自动点击：注册 u2 原生 watcher，由主流程每次 dump 后
        （_run_dialog_watchers）触发检查并点击，复用同一份已 dump 的 XML。
        无独立线程、无第二个 u2 连接、无并发 dump。
        policy: "allow"=点同意/允许 | "deny"=点拒绝
        """
        self._wd_enabled = True
        self._register_dialog_watchers(policy)
        if verbose:
            print(f"🛡️  弹窗自动点击已启用 (policy={policy})")

    def watchdog_policy(self, policy):
        """动态切换弹窗策略（allow=点同意/允许，deny=点拒绝）"""
        if self._wd_enabled:
            self._register_dialog_watchers(policy)
        else:
            self._wd_policy = policy
        print(f"🛡️  弹窗策略切换: {policy}")
        return self

    def watchdog_pause(self):
        """临时暂停弹窗自动点击（用于手动处理弹窗验证场景）"""
        self._wd_enabled = False
        print("🛡️  弹窗自动点击已暂停")

    def watchdog_resume(self):
        """恢复弹窗自动点击"""
        self._wd_enabled = True
        print("🛡️  弹窗自动点击已恢复")

    def stop_watchdog(self):
        """停用弹窗自动点击（保留注册，_run_dialog_watchers 不再触发）"""
        self._wd_enabled = False
        print("🛡️  弹窗自动点击已停用")

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
        self._run_dialog_watchers(xml)
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
        total = pass_n = fail_n = 0
        for s in self.steps:
            lines.append(f"\n## {s['name']}")
            for r in s["results"]:
                total += 1
                if r["result"] == "PASS":
                    pass_n += 1
                elif r["result"] == "FAIL":
                    fail_n += 1
                mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️",
                        "BLOCKED": "⛔"}[r["result"]]
                lines.append(f"- {mark} {r['detail']}")
                if r.get("state"):
                    lines.append(f"  - 状态: {r['state']}")
                if r.get("evidence"):
                    lines.append(f"  - 证据: `{r['evidence']}`")
            for ev in s["evidences"]:
                lines.append(f"  - 证据: `{ev}`")
        lines.append(f"\n---\n**汇总**: ✅ {pass_n} 通过 / ❌ {fail_n} 失败 / 共 {total} 条断言")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n📄 报告已生成: {path}")
        # 完成用例记录入库
        if self._db is not None and self._db_case_id is not None:
            try:
                self._db.finish_case(
                    self._db_case_id, path,
                    f"✅ {pass_n} 通过 / ❌ {fail_n} 失败 / 共 {total} 条断言")
            except Exception:
                pass
        return path
