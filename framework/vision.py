#!/usr/bin/env python3
"""
视觉模型通道：颜色/布局/OCR 盲区检查走 deepseek-v4-flash-vision-exp。
独立模块，供 test_framework.py 懒加载调用。

用法:
    from vision import Vision
    v = Vision()                       # 自动从 ~/.dsh/.credentials.yaml 或 DEEPSEEK_API_KEY 取凭据
    v.ask("这个按钮是置灰还是可点?", image=截图路径)      # 返回文本结论
    v.ask_json("判断置灰状态", image=..., fields=["grayed"])  # 返回结构化 JSON
"""
import base64
import io
import json
import os
import urllib.request

MODEL = "deepseek-v4-flash-vision-exp"
BASE_URL = "https://api.deepseek.com"
CREDENTIALS_FILE = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")


def _load_api_key():
    """凭据来源：环境变量 > ~/.dsh/.credentials.yaml 的 refs.DEEPSEEK_API_KEY"""
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    try:
        with open(CREDENTIALS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    raise RuntimeError("未找到 DEEPSEEK_API_KEY（环境变量或 ~/.dsh/.credentials.yaml）")


def _encode_image(image) -> str:
    """image: 文件路径或已解码的 PNG 字节；返回 base64 data URI。"""
    if isinstance(image, str):
        with open(image, "rb") as f:
            raw = f.read()
    elif isinstance(image, (bytes, bytearray)):
        raw = bytes(image)
    else:
        raise TypeError("image 需为路径或 PNG 字节")
    return "data:image/png;base64," + base64.b64encode(raw).decode()


class Vision:
    def __init__(self, api_key=None, model=MODEL, base_url=BASE_URL, timeout=90):
        self.api_key = api_key or _load_api_key()
        self.model = model
        self.base_url = base_url
        self.timeout = timeout

    def ask(self, prompt: str, image, max_tokens=1024) -> str:
        """通用视觉问答：prompt + 一张截图 → 文本结论。"""
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _encode_image(image)}},
        ]
        return self._chat(content, max_tokens=max_tokens)

    def ask_json(self, prompt: str, image, fields: list[str], max_tokens=1024) -> dict:
        """结构化视觉问答：要求模型只输出 JSON 对象，键为 fields。"""
        schema = ", ".join(f'"{f}": 值' for f in fields)
        content = [
            {"type": "text", "text":
                f"{prompt}\n只输出一个 JSON 对象，不要任何其他文字或代码块标记。"
                f"字段: {{{schema}}}。"},
            {"type": "image_url", "image_url": {"url": _encode_image(image)}},
        ]
        text = self._chat(content, max_tokens=max_tokens)
        # 剥离可能的 ```json 围栏
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"视觉模型未返回合法 JSON: {text[:200]} ({e})")

    def _chat(self, content, max_tokens) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"视觉 API HTTP {e.code}: {e.read().decode()[:300]}")
        msg = resp["choices"][0]["message"]
        return (msg.get("content") or "").strip()


if __name__ == "__main__":
    import sys
    v = Vision()
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shot.png"
    q = sys.argv[2] if len(sys.argv) > 2 else "描述这张截图的主要内容"
    print(v.ask(q, path))
