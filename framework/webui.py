#!/usr/bin/env python3
"""
本地 Web 前端：查看测试记录（SQLite）+ 编辑知识库（knowledge/*.md）。
零依赖：Python 标准库 http.server + 单页 HTML/JS。

启动:  python webui.py [--port 8900]
访问:  http://127.0.0.1:8900
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from db import (get_db, default_test_dir, is_artifact_path,  # noqa: E402
                safe_remove)
from run_case import extract_user_input_from_source  # noqa: E402


def _skill_dir() -> str:
    """skill 包根目录（framework 的上一层）。"""
    return os.path.dirname(HERE)


# ── 工作区路径解析 ─────────────────────────────────────────────────
# 凭据/数据存「工作区」而非 skill 包：skill 包是要分享给团队的，
# 绝不能把 apikey / db 打进分享包（sync_skill.ps1 也不同步 storage/）。
# 工作区路径解析复用 db.default_test_dir()，保证 Windows/Linux/macOS 一致。
def _workspace_dir() -> str:
    env = os.environ.get("DSH_WORKSPACE_DIR")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    try:
        return default_test_dir()
    except Exception:
        return os.path.join(os.path.expanduser("~"), "android-test-skills-data")


def resolve_cases_dir() -> str:
    """用例脚本目录：工作区优先（用户修改只动工作区副本）。
    环境变量 DSH_WORKSPACE_CASES 可覆盖（多工作区场景）。
    """
    env = os.environ.get("DSH_WORKSPACE_CASES")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    # 工作区 cases/（setup 时首次复制，后续只动工作区）
    ws_cases = os.path.join(_workspace_dir(), "cases")
    if os.path.isdir(ws_cases):
        return ws_cases
    # 兜底：skill 包 cases/（未跑过 setup 时）
    return os.path.join(_skill_dir(), "cases")


CASES_DIR = resolve_cases_dir()


def _vision_conf_path() -> str:
    return os.path.join(_workspace_dir(), "storage", "vision.json")


# 默认值与 vision.py 保持一致；留空表示该字段不覆盖（沿用 vision.py 内置默认）
# tap_strategy 是视觉定位策略（som/coordinate/auto），vision.py 白名单已收录
VISION_DEFAULTS = {"base_url": "", "model": "", "api_key": "", "tap_strategy": ""}


def _load_vision_conf() -> dict:
    conf = dict(VISION_DEFAULTS)
    try:
        with open(_vision_conf_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in VISION_DEFAULTS:
                conf[k] = str(data.get(k) or "")
    except (OSError, ValueError):
        pass
    return conf


def _mask(s: str) -> str:
    """apikey 脱敏回显：只留首 4 与末 4 位，中间打码。
    GET 接口绝不能返回明文 —— 否则任何能访问 127.0.0.1 的人都能读到密钥。
    """
    if not s:
        return ""
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 8) + s[-4:]


def _is_loopback(host: str) -> bool:
    """判定 host 是否为回环地址（127.0.0.1 / localhost / ::1）。
    非回环 = 局域网可访问 = 需要警告（Web UI 无认证机制）。
    """
    return host in ("127.0.0.1", "localhost", "::1")


def _save_vision_conf(base_url, model, api_key, tap_strategy="") -> tuple:
    """保存配置。api_key 为空时保留原值（避免用户只想改 model 却清空密钥）。
    tap_strategy 非法值归一为 auto（运行时 resolve_strategy 也会兑底）。"""
    conf = _load_vision_conf()
    conf["base_url"] = (base_url or "").strip()
    conf["model"] = (model or "").strip()
    if (api_key or "").strip():
        conf["api_key"] = api_key.strip()
    ts = (tap_strategy or "").strip()
    conf["tap_strategy"] = ts if ts in ("som", "coordinate", "auto") else "auto"
    fp = _vision_conf_path()
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)
    # 密钥文件收紧权限（Unix 有效；Windows 无 chmod，靠不进版本库 + 不进 skill 包保障）
    try:
        import stat
        os.chmod(fp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except Exception:
        pass
    return conf, fp


def _test_vision(base_url, model, api_key, timeout=30) -> tuple:
    """视觉模型连通性自检：发一个极小的 chat 请求，不落盘、不写日志。
    返回 (ok, message)。api_key 只在此处使用，绝不回显。
    """
    if not api_key:
        return False, "未配置 API Key（请先在上方填写并保存，或填入输入框后再测）"
    base = (base_url or "").rstrip("/") or "https://api.deepseek.com"
    if not base.startswith(("http://", "https://")):
        return False, f"Base URL 必须以 http:// 或 https:// 开头：{base!r}"
    # 超时钳制：避免 DNS 挂起把请求线程卡死（ThreadingHTTPServer 会累积线程）
    timeout = max(3, min(int(timeout or 30), 30))
    url = base + "/chat/completions"
    body = json.dumps({
        "model": model or "deepseek-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key})
    try:
        from vision import urlopen_with_ssl_fallback
        resp, ssl_skipped = urlopen_with_ssl_fallback(req, timeout)
        with resp:
            resp.read()
        if ssl_skipped:
            return True, (f"连接成功（{url}）——注意：SSL 证书校验失败已跳过"
                          "（疑似企业代理拦截），正式视觉请求同样会跳过校验")
        return True, f"连接成功（{url}）"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        return False, f"HTTP {e.code}: {detail or e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# 从用例源码里提取的元信息（用于列表展示：描述/用户原始输入、脚本名）
def _case_meta(fname: str, full: str) -> dict:
    name = fname[:-3] if fname.endswith(".py") else fname
    src = ""
    try:
        with open(full, encoding="utf-8") as f:
            src = f.read()
    except (OSError, UnicodeDecodeError):
        pass
    # USER_INPUT 常量（ast 解析，与 run_case.py 入库逻辑同源）
    desc = extract_user_input_from_source(src) or ""
    if not desc:
        # 退化：取模块 docstring 前几行
        dm = re.search(r'^"""(.*?)"""', src, re.S | re.M)
        if dm:
            desc = "\n".join(
                ln.strip() for ln in dm.group(1).strip().splitlines()[:4] if ln.strip())
    # 步骤数：粗数 t.step( 调用次数
    steps = len(re.findall(r'\.step\(\s*["\']', src))
    try:
        st = os.stat(full)
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0
    return {
        "name": fname, "title": name, "description": desc,
        "steps": steps, "size": size, "mtime": mtime, "path": full,
    }


def _safe_case_name(name: str):
    """防止路径穿越；接受 .py 文件名或 <包名目录>/<文件>.py 相对路径。
    用例按包名分目录存放（cases/com.zui.calendar/172.py），目录即命名空间。
    返回以 / 分隔的相对路径，非法返回 None。"""
    if not name:
        return None
    parts = re.split(r"[/\\]+", name)
    if not (1 <= len(parts) <= 2):
        return None
    for p in parts:
        # 空段（前导/尾随/连续分隔符）、. 与 .. 段、隐藏段、含首尾空白段一律拒绝
        if not p or p in (".", "..") or p.startswith(".") or p != p.strip():
            return None
    if not parts[-1].endswith(".py"):
        return None
    rel = "/".join(parts)
    # 双保险：规范化后必须仍位于 CASES_DIR 内
    full = os.path.normpath(os.path.join(CASES_DIR, rel))
    if os.path.commonpath([full, os.path.normpath(CASES_DIR)]) != os.path.normpath(CASES_DIR):
        return None
    return rel


# 新建知识卡的默认样式（_template.md 缺失时的兜底）
FALLBACK_TEMPLATE = """# App 知识卡：<显示名>

- **app**: com.example.app（必填，知识卡索引键 = 包名）
- **name**: 示例 App
- **最近验证**: （每次沿链路跑通后更新此日期）

## 前置条件

- 示例: 首次使用需先 `pm clear com.example.app`

## 导航入口

- 功能页A: 主页 → 点击XX → 点击YY

## 验证要点

- 示例: 保存后看列表是否出现新条目
"""

# 场景卡兜底骨架（scenarios/ 为空、没有样本可参照时使用）。
# 头部「键: 值 / - 列表」区域是 states.py 的机器解析区，保持朴素写法。
FALLBACK_SCENARIO = """# 场景卡：<场景名>

场景: <场景名>
判定方法: states.<方法名>()
判定命令: adb shell settings get <scope> <key>
判定说明: <什么值表示开启/关闭>

触发词:
- <场景名>

## 进入方式

- 步骤一

## 退出方式

- 步骤一

## 注意事项

- 判定要用上面的命令，不要用界面文字猜
"""


PROTECTED_KNOWLEDGE = {"_template.md", "_system.md"}
# 只读（连内容都不可改）：_template.md 是新建卡的样式来源，改坏会污染所有新卡
READONLY_KNOWLEDGE = {"_template.md"}
# 仅禁删、允许编辑补充：_system.md 是通用兜底经验，需要随使用持续积累
NODELETE_KNOWLEDGE = {"_system.md"}


def kb_flags(name):
    """返回知识卡的保护标记，供列表接口下发给前端。"""
    return {
        "protected": name in PROTECTED_KNOWLEDGE,          # 前端据此禁用删除
        "readonly": name in READONLY_KNOWLEDGE,            # 前端据此禁用编辑/保存
        "nodelete": name in NODELETE_KNOWLEDGE,            # 不可删除，但可编辑
    }


def _fill_template_fields(body: str, app: str) -> str:
    """按包名预填模板的 app / name 字段（MD 卡）。

    只替换 `- **app**: xxx` 这类列表行的值，保留其余内容。
    """
    if not app:
        return body

    def repl_value(field, value, text):
        out = []
        for line in text.split("\n"):
            m = re.match(r"^(\s*-\s*\*\*%s\*\*\s*:\s*)(.*)$" % re.escape(field), line)
            if m:
                out.append(m.group(1) + value)
            else:
                out.append(line)
        return "\n".join(out)

    body = repl_value("app", app, body)
    if not re.search(r"^\s*-\s*\*\*name\*\*", body, re.M):
        # 模板没有 name 行则不加，保持模板原样
        pass
    return body


def _replace_field_value(body: str, field: str, value: str) -> str:
    """把顶层「field: xxx」标量行的值换成 value（场景卡头部解析区）。"""
    out, done = [], False
    for line in body.split("\n"):
        m = re.match(r"^(%s\s*:\s*)(.*)$" % re.escape(field), line)
        if m and not done:
            out.append(m.group(1) + value)
            done = True
        else:
            out.append(line)
    return "\n".join(out)


def _clear_list_field(body: str, field: str, seed: str = "") -> str:
    """清空「field:」后面紧邻的 - 列表项，只留种子项。

    用于新建场景卡：样本卡的触发词属于原场景，不能直接继承，
    否则新卡会被旧场景的词误检索到。
    """
    lines = body.split("\n")
    out, i, done = [], 0, False
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(%s)\s*:\s*(.*)$" % re.escape(field), line)
        if m and not done:
            out.append("%s:" % m.group(1))
            done = True
            if seed:
                out.append("- %s" % seed)
            # 吃掉紧随的列表项
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("- "):
                j += 1
            # 空行也一并吃掉（列表块结束）
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            i = j
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _safe_kb_name(name: str):
    r"""防止路径穿越；只接受 .md 文件名。

    支持子目录相对路径（场景卡），如 "scenarios/sys.无限工作台.md"：
    - 每段都必须是安全的普通名字（不含 .. / 绝对路径 / 盘符）
    - 目录只允许出现在 knowledge/ 下已有的子目录里，避免任意建目录
    - 文件名仍受 [\w.\-] 白名单约束（\w 在 Python3 下含中文）
    """
    raw = (name or "").strip()
    # 统一分隔符，拒绝绝对路径与盘符
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        return None
    segs = re.split(r"[\\/]+", raw)
    # 出现 .. 直接拒绝（不能只是过滤掉 —— 过滤会让
    # "scenarios/../../evil.md" 静默变成 "scenarios/evil.md"）
    if any(s == ".." for s in segs):
        return None
    parts = [s for s in segs if s not in ("", ".")]
    if not parts or len(parts) > 2:          # 最多一层子目录
        return None
    fname = parts[-1]
    if not re.match(r"^[\w.\-]+\.md$", fname):
        return None
    if fname.startswith("."):
        return None
    if len(parts) == 1:
        return fname

    sub = parts[0]
    # 目录段同样走白名单，且必须是已存在的目录（不凭空创建）
    if not re.match(r"^[\w.\-]+$", sub) or sub.startswith("."):
        return None
    if not os.path.isdir(os.path.join(KNOWLEDGE_DIR, sub)):
        return None
    # 解析后必须仍在 KNOWLEDGE_DIR 内（双保险，防穿越）
    full = os.path.normpath(os.path.join(KNOWLEDGE_DIR, *parts))
    if os.path.commonpath([os.path.abspath(full),
                           os.path.abspath(KNOWLEDGE_DIR)]) != os.path.abspath(KNOWLEDGE_DIR):
        return None
    return "/".join(parts)


# 知识卡为 MD 格式：任何文本都合法，无需语法校验（pyyaml 已于 2026-09 移除）。
# 保存前仅做非空检查。
def _validate_md(text: str):
    """MD 卡保存校验：只拦空文件（误清空），其余内容不做语法判断。"""
    if not text.strip():
        return False, "内容为空：如需清空请直接删除文件"
    return True, None


PAGE = None

def resolve_knowledge_dir() -> str:
    """知识库目录：工作区优先（用户修改只动工作区副本）。
    环境变量 DSH_KNOWLEDGE_DIR 可覆盖。
    """
    env = os.environ.get("DSH_KNOWLEDGE_DIR")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    # 工作区 knowledge/
    ws_kb = os.path.join(_workspace_dir(), "knowledge")
    if os.path.isdir(ws_kb):
        return ws_kb
    # 兜底：skill 包 knowledge/
    return os.path.join(_skill_dir(), "knowledge")


KNOWLEDGE_DIR = resolve_knowledge_dir()


def _get_version() -> str:
    """读取 framework/VERSION 文件。缓存在进程内，只读一次。"""
    if hasattr(_get_version, "_cached"):
        return _get_version._cached
    try:
        with open(os.path.join(HERE, "VERSION"), encoding="utf-8") as f:
            _get_version._cached = f.read().strip() or "unknown"
    except OSError:
        _get_version._cached = "unknown"
    return _get_version._cached

def _load_page():
    """Lazy-load the HTML template (kept in webui.html to avoid JS escaping issues)."""
    global PAGE
    if PAGE is None:
        page_path = os.path.join(HERE, 'webui.html')
        with open(page_path, encoding='utf-8') as f:
            PAGE = f.read()
    return PAGE




class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    # ── 兜底：任何未捕获异常都必须变成 JSON 500，绝不能掐断连接 ──
    # BaseHTTPRequestHandler 不会捕获 handler 里的异常：异常穿出后连接被直接
    # 关掉，浏览器拿不到任何响应，前端只能报一句无头无脑的 "Failed to fetch"
    # —— 既看不到原因，也不知道操作到底成没成。所以每个动词都套一层：
    # 正常返回照旧，异常一律转成 {"error": ...} 500。
    def _guarded(self, fn):
        try:
            fn()
        except Exception as e:
            try:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, fp, code=200):
        """按扩展名返回文件（图片返回二进制，文本返回 UTF-8）。"""
        if not os.path.isfile(fp):
            self._text("文件不存在", 404)
            return
        ext = os.path.splitext(fp)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            ctype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "gif": "image/gif", "webp": "image/webp"}[ext.lstrip(".")]
            with open(fp, "rb") as f:
                body = f.read()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            try:
                with open(fp, encoding="utf-8") as f:
                    self._text(f.read(), code)
            except (OSError, UnicodeDecodeError):
                self._text("无法读取文件", 500)

    def do_GET(self):
        self._guarded(self._do_GET)

    def _do_GET(self):
        path = self.path.split("?")[0]
        db = get_db()
        if path == "/" or path == "/index.html":
            body = _load_page().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path in ("/codemirror.bundle.js", "/webui.js", "/webui.css"):
            # 本地静态资源（离线可用，不走 CDN）。
            # 从 webui.html 拆出，便于独立维护；改后需重启服务（PAGE 缓存）
            # 或强制刷新浏览器（外链文件有 1 天缓存）。
            ctype = "application/javascript; charset=utf-8" if path.endswith(
                ".js") else "text/css; charset=utf-8"
            fp = os.path.join(HERE, path.lstrip("/"))
            if os.path.isfile(fp):
                body = open(fp, "rb").read()
                # ETag 校验（内容 hash）：改文件后刷新即生效，
                # 避免单纯 max-age 缓存导致"改了没反应"
                import hashlib
                etag = '"%s"' % hashlib.md5(body).hexdigest()
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "static asset missing"}, 404)
        elif path == "/api/cases":
            self._json(db.list_cases(100))
        elif path == "/api/flaky":
            self._json(db.flaky_stats())
        elif path == "/api/dashboard":
            # Dashboard 汇总：统计卡 + flaky Top N + 按类别聚合
            all_stats = db.flaky_stats(min_runs=2)
            cases = db.list_cases(limit=500)
            total = len(cases)
            pass_n = sum(1 for c in cases if c["status"] == "PASS")
            fail_n = sum(1 for c in cases if c["status"] == "FAIL")
            pass_rate = (pass_n / total) if total else 0.0
            flaky_list = [s for s in all_stats if s["flaky"]]
            # flaky 按通过率距 0.5 越近越“flaky”排序
            flaky_list.sort(key=lambda s: abs(s["pass_rate"] - 0.5))
            flaky_top = flaky_list[:10]
            # 按类别（用例名 _ 前缀）聚合
            cat_map = {}
            for c in cases:
                name = (c.get("name") or "").replace(".py", "")
                idx = name.find("_")
                cat = name[:idx] if idx > 0 else (name or "未知")
                if cat not in cat_map:
                    cat_map[cat] = {"runs": 0, "pass": 0}
                cat_map[cat]["runs"] += 1
                if c["status"] == "PASS":
                    cat_map[cat]["pass"] += 1
            by_cat = sorted(
                [{"category": k, "runs": v["runs"],
                  "pass_rate": round(v["pass"] / v["runs"], 4) if v["runs"] else 0.0}
                 for k, v in cat_map.items()],
                key=lambda x: -x["runs"])
            self._json({
                "summary": {
                    "total": total, "pass": pass_n, "fail": fail_n,
                    "pass_rate": round(pass_rate, 4),
                    "flaky_count": len(flaky_list),
                },
                "flaky_top": flaky_top,
                "by_category": by_cat,
            })
        elif path == "/api/card-freshness":
            # 知识卡新鲜度：每包最近 PASS 时间与执行统计
            # 前端可据此标记过期卡片（超 N 天未验证）
            from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
            qs = _parse_qs(_urlparse(self.path).query or "")
            days = int(qs.get("days", ["30"])[0])
            from datetime import datetime as _dt, timedelta
            cutoff = (_dt.now() - timedelta(days=days)).isoformat(timespec="seconds")
            rows = db.card_freshness()
            for r in rows:
                r["stale"] = (
                    not r["last_pass_at"] or r["last_pass_at"] < cutoff
                )
            self._json(rows)
        elif path == "/api/vision":
            # 视觉模型配置（凭据存工作区，apikey 脱敏回显）
            conf = _load_vision_conf()
            self._json({
                "base_url": conf["base_url"],
                "model": conf["model"],
                "tap_strategy": conf.get("tap_strategy") or "auto",
                # 明文绝不外传；前端用 has_key 判断是否已配置
                "api_key_masked": _mask(conf["api_key"]),
                "has_key": bool(conf["api_key"]),
                "path": _vision_conf_path(),
                "env_available": bool(
                    os.environ.get("DEEPSEEK_API_KEY")
                    or _load_vision_conf()["api_key"]),
            })
        elif path == "/api/version":
            # 版本号：前端侧栏底部常驻，快速确认是否为最新版本
            self._json({"version": _get_version()})
        elif path.startswith("/api/cases/"):
            # /api/cases/<id>/history → 同 script_path 的历史序列
            if path.endswith("/history"):
                parts = path.rsplit("/", 2)  # [..., id, 'history']
                try:
                    cid = int(parts[-2])
                except (ValueError, IndexError):
                    self._json({"error": "bad id"}, 400)
                    return
                case = db.get_case(cid)
                if not case or not case.get("script_path"):
                    self._json([])
                    return
                self._json(db.case_history(case["script_path"]))
                return
            cid = int(path.rsplit("/", 1)[-1])
            case = db.get_case(cid)
            if case is None:
                self._json({"error": "not found"}, 404)
            else:
                # 补全 list_cases 里才有的 duration_seconds / status 字段
                try:
                    full = next((c for c in db.list_cases(limit=200) if c["id"] == cid), {})
                    case["duration_seconds"] = full.get("duration_seconds")
                    case["status"] = full.get("status") or "UNKNOWN"
                except Exception:
                    pass
                self._json(case)
        elif path == "/api/scripts":
            # 用例脚本列表。用例按被测 App 包名分目录（cases/<包名>/<编号>.py），
            # 递归收集；name 用相对路径（如 "com.zui.calendar/172.py"）展示与定位。
            # _ 开头（_flow.py / _set_time_tap.py / _template.py）是共享模块，
            # 不是可执行用例，不在列表展示 —— 与 run_case.py 的过滤规则一致。
            try:
                rows = []
                for root, dirs, files in os.walk(CASES_DIR):
                    dirs[:] = [d for d in dirs
                               if d != "__pycache__" and not d.startswith(".")]
                    for f in files:
                        if not f.endswith(".py") or f.startswith("_"):
                            continue
                        fp = os.path.join(root, f)
                        rel = os.path.relpath(fp, CASES_DIR).replace(os.sep, "/")
                        rows.append(_case_meta(rel, fp))
                rows.sort(key=lambda r: -r["mtime"])
                self._json(rows)
            except OSError as e:
                self._json({"error": str(e)}, 500)
        elif path.startswith("/api/scripts/"):
            # 读取单个用例源码（URL 中的中文是 percent-encoded，需先解码；
            # 子目录路径可能整体被 encodeURIComponent，斜杠为 %2F）
            from urllib.parse import unquote
            name = _safe_case_name(unquote(path[len("/api/scripts/"):]))
            fp = os.path.join(CASES_DIR, name) if name else None
            if not name or not fp or not os.path.isfile(fp):
                self._text("not found", 404)
            else:
                try:
                    with open(fp, encoding="utf-8") as f:
                        self._text(f.read())
                except (OSError, UnicodeDecodeError):
                    self._text("无法读取文件", 500)
        elif path == "/api/knowledge":
            # 递归列出 knowledge/ 下所有卡片（含 scenarios/ 子目录）。
            # name 用相对路径（如 "scenarios/sys.无限工作台.md"），
            # 读写接口据此定位，子目录里的场景卡也能在 UI 里编辑。
            files = []
            for root, dirs, names in os.walk(KNOWLEDGE_DIR):
                dirs[:] = [d for d in dirs
                           if d not in ("__pycache__",) and not d.startswith(".")]
                for f in names:
                    if not f.endswith(".md"):
                        continue
                    rel = os.path.relpath(os.path.join(root, f), KNOWLEDGE_DIR)
                    files.append(rel.replace(os.sep, "/"))
            files.sort()
            self._json([
                dict(name=f, **kb_flags(os.path.basename(f)))
                for f in files
            ])
        elif path == "/api/knowledge-template":
            # 新建知识卡的样式来源：优先 _template.md，缺失时回退内置样式。
            # 支持 ?app=<包名> 预填 app 字段。
            from urllib.parse import unquote, urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query or "")
            app = (qs.get("app") or [""])[0].strip()
            fp = os.path.join(KNOWLEDGE_DIR, "_template.md")
            if os.path.isfile(fp):
                with open(fp, encoding="utf-8") as f:
                    body = f.read()
            else:
                body = FALLBACK_TEMPLATE
            if app:
                body = _fill_template_fields(body, app)
            self._text(body)
        elif path == "/api/scenario-template":
            # 新建场景卡的样式来源：优先取 scenarios/ 下已有的一张卡作样本，
            # 缺失时回退内置骨架。用 ?scene=<场景名> 预填「场景」字段。
            #
            # 为什么要单独的模板：场景卡头部有机器解析区（states.py 读取
            # 判定命令/触发词），跟 App 卡的纯阅读结构不同。
            from urllib.parse import unquote, urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query or "")
            scene = (qs.get("scene") or [""])[0].strip()
            sdir = os.path.join(KNOWLEDGE_DIR, "scenarios")
            body = ""
            if os.path.isdir(sdir):
                samples = sorted(f for f in os.listdir(sdir)
                                 if f.endswith(".md"))
                if samples:
                    with open(os.path.join(sdir, samples[0]), encoding="utf-8") as f:
                        body = f.read()
            if not body:
                body = FALLBACK_SCENARIO
            if scene:
                # 场景名替换：只改「场景:」标量行的值
                body = _replace_field_value(body, "场景", scene)
                # 样本卡的触发词属于原场景，新建时清空，只留场景名本身，避免误检索
                body = _clear_list_field(body, "触发词", seed=scene)
            self._text(body)
        elif path.startswith("/api/knowledge/"):
            from urllib.parse import unquote
            name = _safe_kb_name(unquote(path.rsplit("/", 1)[-1]))
            if not name:
                self._text("not found", 404)
                return
            fp = os.path.join(KNOWLEDGE_DIR, name)
            if not os.path.isfile(fp):
                self._text("not found", 404)
            else:
                with open(fp, encoding="utf-8") as f:
                    self._text(f.read())
        elif path.startswith("/api/file") or path.startswith("/api/report"):
            # 最小权限：只放行运行产物目录（storage/screenshots、storage/reports）
            # 内的文件。即使只绑 127.0.0.1，也不暴露任意本地文件读取能力。
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            rp = q.get("path", [""])[0]
            if rp and is_artifact_path(rp) and os.path.isfile(rp):
                self._file(rp)
            else:
                self._text("文件不存在或不在产物目录内", 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        self._guarded(self._do_POST)

    def _do_POST(self):
        """只接收 JSON。目前用于保存视觉模型配置、批量删除、重建报告。"""
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        # 重建报告不需要请求体，别拿 body 校验为难它
        if path.endswith("/rebuild-report"):
            payload = {}
        else:
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except Exception:
                self._json({"error": "请求体不是合法 JSON"}, 400)
                return
        if path == "/api/vision":
            try:
                conf, fp = _save_vision_conf(
                    payload.get("base_url"),
                    payload.get("model"),
                    payload.get("api_key"),
                    payload.get("tap_strategy"),
                )
            except OSError as e:
                self._json({"error": f"写入失败：{e}"}, 500)
                return
            self._json({"ok": True, "path": fp,
                        "api_key_masked": _mask(conf["api_key"]),
                        "has_key": bool(conf["api_key"]),
                        "tap_strategy": conf.get("tap_strategy") or "auto"})
        elif path == "/api/vision/test":
            # 连通性自检：用当前配置发一个最小请求，不落盘
            conf = _load_vision_conf()
            key = (payload.get("api_key") or "").strip() or conf["api_key"]
            base = (payload.get("base_url") or conf["base_url"]).strip()
            model = (payload.get("model") or conf["model"]).strip()
            ok, msg = _test_vision(base, model, key)
            self._json({"ok": ok, "message": msg}, 200 if ok else 400)
        elif path == "/api/cases/delete-batch":
            # 批量删除测试记录（body: {"ids":[...]}），连带删报告与截图
            ids = payload.get("ids")
            if not isinstance(ids, list) or not ids:
                self._json({"error": "ids 必须是非空数组"}, 400)
                return
            if not all(isinstance(i, int) for i in ids):
                self._json({"error": "ids 必须全是整数"}, 400)
                return
            db = get_db()
            deleted, missing, removed_files = [], [], 0
            for cid in ids:
                if db.get_case(cid) is None:
                    missing.append(cid)
                    continue
                removed_files += db.delete_case(cid, remove_artifacts=True)
                deleted.append(cid)
            self._json({"ok": True, "deleted": deleted,
                        "missing": missing, "removed_files": removed_files})
        elif (path.startswith("/api/cases/")
              and path.endswith("/rebuild-report")):
            # 报告文件丢了不要紧：steps/results 明细都在库里，随时能重新渲染。
            # 报告只是这些数据的 Markdown 视图。
            try:
                cid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                self._json({"error": "非法 ID"}, 400)
                return
            db = get_db()
            if db.get_case(cid) is None:
                self._json({"error": "not found"}, 404)
                return
            from report_rebuild import rebuild_report_auto   # 延迟导入，冷启动更快
            p, summary = rebuild_report_auto(cid, db)
            self._json({"ok": True, "report_path": p, "summary": summary})
        else:
            self._json({"error": "not found"}, 404)

    def do_PUT(self):
        self._guarded(self._do_PUT)

    def _do_PUT(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/scripts/"):
            # 保存/新建用例脚本（URL 中文需解码；支持 <包名目录>/<文件>.py）
            from urllib.parse import unquote
            name = _safe_case_name(unquote(path[len("/api/scripts/"):]))
            if not name:
                self._json({"error": "非法文件名"}, 400)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            fp = os.path.join(CASES_DIR, name)
            os.makedirs(os.path.dirname(fp) or CASES_DIR, exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(body)
            self._json({"ok": True, "file": name})
        elif path.startswith("/api/knowledge/"):
            from urllib.parse import unquote
            name = _safe_kb_name(unquote(path.rsplit("/", 1)[-1]))
            if not name:
                self._json({"error": "非法文件名"}, 400)
                return
            # 只读卡禁止覆盖：_template.md 是新建卡的样式来源，改坏会污染所有新卡。
            # _system.md 只禁删、允许编辑补充（通用兜底经验需要持续积累）。
            if name in READONLY_KNOWLEDGE:
                self._json({"error": f"{name} 是样式模板，只读不可修改"}, 403)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            # MD 卡：只拦空文件，无语法校验（pyyaml 已移除）
            ok, err = _validate_md(body)
            if not ok:
                self._json({"error": err}, 400)
                return
            fp = os.path.join(KNOWLEDGE_DIR, name)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(body)
            self._json({"ok": True, "file": name})
        else:
            self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        self._guarded(self._do_DELETE)

    def _do_DELETE(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/scripts/"):
            # 删除用例脚本（URL 中文需解码；支持 <包名目录>/<文件>.py）
            from urllib.parse import unquote
            name = _safe_case_name(unquote(path[len("/api/scripts/"):]))
            fp = os.path.join(CASES_DIR, name) if name else None
            if not name or not fp or not os.path.isfile(fp):
                self._json({"error": "not found"}, 404)
                return
            # safe_remove：以「文件还在不在」判定成败。某些环境会把删除改走
            # 回收站（文件已删但仍抛异常），只有文件还在才算真失败。
            safe_remove(fp)
            if os.path.exists(fp):
                self._json({"error": f"文件删除失败（仍存在）: {fp}"}, 500)
            else:
                self._json({"ok": True, "deleted": name})
            return
        if path.startswith("/api/knowledge/"):
            # 删除知识卡（内置 _template/_system 卡受保护，不可删除）
            from urllib.parse import unquote
            name = _safe_kb_name(unquote(path.rsplit("/", 1)[-1]))
            if not name:
                self._json({"error": "非法文件名"}, 400)
                return
            # 内置卡均不可删除：_template 是样式源，_system 是通用兜底经验
            if name in PROTECTED_KNOWLEDGE:
                self._json({"error": f"{name} 是内置卡，不可删除"}, 403)
                return
            fp = os.path.join(KNOWLEDGE_DIR, name)
            if not os.path.isfile(fp):
                self._json({"error": "not found"}, 404)
                return
            safe_remove(fp)
            if os.path.exists(fp):
                self._json({"error": f"文件删除失败（仍存在）: {fp}"}, 500)
            else:
                self._json({"ok": True, "deleted": name})
            return
        if path.startswith("/api/cases/"):
            try:
                cid = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self._json({"error": "非法 ID"}, 400)
                return
            db = get_db()
            if db.get_case(cid) is None:
                self._json({"error": "not found"}, 404)
                return
            # 连带删除磁盘产物：报告（含时间戳备份）+ 截图（case_* 整目录，
            # 仍被其它记录引用时只删引用文件）
            removed = db.delete_case(cid, remove_artifacts=True)
            self._json({"ok": True, "deleted": cid, "removed_files": removed})
        else:
            self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser(description="Android GUI 测试台 Web 前端")
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    # 非回环地址警告：Web UI 无认证机制，暴露到不可信网络 = 裸奔
    if not _is_loopback(args.host):
        print("⚠️" * 8)
        print("⚠️  Web UI 绑定到非回环地址，局域网内任何人可：")
        print("⚠️  删除测试记录 / 查看并修改知识卡 / 保存视觉模型 API Key")
        print("⚠️  确认这是你的意图。无认证机制，切勿暴露到不可信网络。")
        print("⚠️" * 8)
    print(f"📊 测试台: http://{args.host}:{args.port}")
    print(f"   记录库: {get_db().path}")
    print(f"   知识库: {KNOWLEDGE_DIR}")
    print(f"   用例库: {CASES_DIR}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
