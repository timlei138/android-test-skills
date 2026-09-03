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
import ssl
import urllib.request

MODEL = "deepseek-v4-flash-vision-exp"
BASE_URL = "https://api.deepseek.com"
CREDENTIALS_FILE = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")


# 工作区视觉配置（Web UI「视觉模型」页保存；工作区只存运行产物，不进 skill 包）
def _workspace_dir() -> str:
    env = os.environ.get("DSH_ANDROID_TEST_DIR")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    try:
        from db import default_test_dir
        return default_test_dir()
    except Exception:
        return os.path.join(os.path.expanduser("~"), "dsh-android-test")


VISION_CONF_FILE = os.path.join(_workspace_dir(), "storage", "vision.json")


def _load_vision_conf() -> dict:
    """读取工作区视觉配置（base_url / model / api_key）。缺失字段返回空串。"""
    out = {"base_url": "", "model": "", "api_key": ""}
    try:
        with open(VISION_CONF_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in out:
                out[k] = str(data.get(k) or "").strip()
    except (OSError, ValueError):
        pass
    return out


def _load_api_key():
    """凭据优先级：环境变量 > 工作区 storage/vision.json > ~/.dsh/.credentials.yaml。

    工作区配置是 Web UI「视觉模型」页写入的，跨平台（Windows/Linux/macOS）
    都落在同一个相对路径下，因此这里不再依赖任何平台特定写法。
    """
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    conf = _load_vision_conf()
    if conf["api_key"]:
        return conf["api_key"]
    try:
        with open(CREDENTIALS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    raise RuntimeError(
        "未配置视觉模型 API Key。可在 Web UI 左侧「视觉模型」页填写并保存"
        "（存于工作区 storage/vision.json），或设置环境变量 DEEPSEEK_API_KEY")


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


def _is_cert_verify_error(e) -> bool:
    """urllib.error.URLError 是否为证书校验失败（企业代理/自签证书常见）。"""
    reason = getattr(e, "reason", e)
    return isinstance(reason, ssl.SSLCertVerificationError) \
        or "certificate verify failed" in str(reason).lower()


def urlopen_with_ssl_fallback(req, timeout):
    """默认严格校验证书；CERTIFICATE_VERIFY_FAILED 时自动降级重试一次
    （跳过校验——典型场景：企业代理 SSL 拦截重签证书）。
    返回 (response, ssl_skipped)。设环境变量 DSH_SSL_VERIFY=1 可禁止降级。"""
    try:
        return urllib.request.urlopen(req, timeout=timeout), False
    except urllib.error.URLError as e:
        if not _is_cert_verify_error(e) or os.environ.get("DSH_SSL_VERIFY") == "1":
            raise
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        print("⚠️ [vision] SSL 证书校验失败，已跳过校验重试"
              "（疑似企业代理拦截；设 DSH_SSL_VERIFY=1 可禁用此降级）")
        return urllib.request.urlopen(req, timeout=timeout, context=ctx), True


class Vision:
    def __init__(self, api_key=None, model=None, base_url=None, timeout=90):
        # 未显式传入时，model / base_url 取工作区配置，否则回落到内置默认
        conf = _load_vision_conf()
        self.api_key = api_key or _load_api_key()
        self.model = model or conf["model"] or MODEL
        self.base_url = (base_url or conf["base_url"] or BASE_URL).rstrip("/")
        self.timeout = timeout

    def ask(self, prompt: str, image, max_tokens=1024, timeout=None) -> str:
        """通用视觉问答：prompt + 一张截图 → 文本结论。timeout 覆盖默认（秒）。"""
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _encode_image(image)}},
        ]
        return self._chat(content, max_tokens=max_tokens, timeout=timeout)

    def ask_json(self, prompt: str, image, fields: list[str], max_tokens=1024,
                 timeout=None) -> dict:
        """结构化视觉问答：要求模型只输出 JSON 对象，键为 fields。
        timeout 覆盖默认（秒）——高风险短等待场景（如弹窗 AI 决策）可收紧。"""
        schema = ", ".join(f'"{f}": 值' for f in fields)
        content = [
            {"type": "text", "text":
                f"{prompt}\n只输出一个 JSON 对象，不要任何其他文字或代码块标记。"
                f"字段: {{{schema}}}。"},
            {"type": "image_url", "image_url": {"url": _encode_image(image)}},
        ]
        text = self._chat(content, max_tokens=max_tokens, timeout=timeout)
        # 剥离可能的 ```json 围栏
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"视觉模型未返回合法 JSON: {text[:200]} ({e})")

    def _chat(self, content, max_tokens, timeout=None) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
        to = timeout if timeout is not None else self.timeout
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key})
        try:
            with urlopen_with_ssl_fallback(req, to)[0] as r:
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
