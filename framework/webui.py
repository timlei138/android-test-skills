#!/usr/bin/env python3
"""
本地 Web 前端：查看测试记录（SQLite）+ 编辑知识库（knowledge/*.yaml）。
零依赖：Python 标准库 http.server + 单页 HTML/JS。

启动:  python webui.py [--port 8900]
访问:  http://127.0.0.1:8900
"""
import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from db import get_db, default_test_dir  # noqa: E402


def resolve_knowledge_dir() -> str:
    """知识库目录：环境变量 DSH_KNOWLEDGE_DIR > 工作区 framework/knowledge
    > 工作区 knowledge > 本包 knowledge。显式定位，避免从 skill 包启动时读错。"""
    env = os.environ.get("DSH_KNOWLEDGE_DIR")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    work = default_test_dir()
    for candidate in (
        os.path.join(work, "framework", "knowledge"),
        os.path.join(work, "knowledge"),
        os.path.join(HERE, "knowledge"),
        os.path.join(os.path.dirname(HERE), "knowledge"),
    ):
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(work, "framework", "knowledge")


KNOWLEDGE_DIR = resolve_knowledge_dir()

PAGE = None

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
        path = self.path.split("?")[0]
        db = get_db()
        if path == "/" or path == "/index.html":
            body = _load_page().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/cases":
            self._json(db.list_cases(100))
        elif path.startswith("/api/cases/"):
            cid = int(path.rsplit("/", 1)[-1])
            case = db.get_case(cid)
            if case is None:
                self._json({"error": "not found"}, 404)
            else:
                self._json(case)
        elif path == "/api/knowledge":
            self._json(sorted(os.listdir(KNOWLEDGE_DIR)))
        elif path.startswith("/api/knowledge/"):
            name = os.path.basename(path.rsplit("/", 1)[-1])
            fp = os.path.join(KNOWLEDGE_DIR, name)
            if not os.path.isfile(fp) or os.path.basename(fp) != name:
                self._text("not found", 404)
            else:
                with open(fp, encoding="utf-8") as f:
                    self._text(f.read())
        elif path.startswith("/api/file") or path.startswith("/api/report"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            rp = q.get("path", [""])[0]
            if rp and os.path.isfile(rp):
                self._file(rp)
            else:
                self._text("文件不存在", 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_PUT(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/knowledge/"):
            name = os.path.basename(path.rsplit("/", 1)[-1])
            if not re.match(r"^[\w.\-]+\.(yaml|yml)$", name):
                self._json({"error": "非法文件名"}, 400)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            fp = os.path.join(KNOWLEDGE_DIR, name)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(body)
            self._json({"ok": True, "file": name})
        else:
            self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser(description="Android GUI 测试台 Web 前端")
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"📊 测试台: http://{args.host}:{args.port}")
    print(f"   记录库: {get_db().path}")
    print(f"   知识库: {KNOWLEDGE_DIR}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
