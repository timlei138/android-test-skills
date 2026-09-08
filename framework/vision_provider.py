#!/usr/bin/env python3
"""视觉模型路由：用户配置（storage/vision.json）> Agent 注入。

为什么需要路由而不是直接用 vision.Vision：
- 生成期/调试期：Agent（导入本 SKILL 的宿主）可通过 TestCase(vision=...)
  注入自己的视觉模型对象，用户没配 vision.json 时视觉链路也能跑；
- run_case 独立执行：Agent 不在环，只能依赖用户配置；缺失时 ask/ask_json
  抛 RuntimeError，由调用方（如 tap_vision）catch 后降级 WARN，
  而不是把用例打成 ERROR——视觉链路是增强通道，不是断言本体。

Agent 视觉对象的建议契约：
    ask(prompt, image, temperature=0.0, timeout=None, **kwargs) -> str
    ask_json(prompt, image, fields, temperature=0.0, timeout=None, **kwargs) -> dict
旧式签名 ask(prompt, image) / ask_json(prompt, image, fields) 也可接入：
本模块用签名探测自动降级，不透传不支持的形参。
"""
import inspect
import os


class VisionProvider:
    """视觉调用统一入口。优先级：用户配置 > Agent 提供。"""

    def __init__(self, agent_vision=None, timeout=90):
        self.agent_vision = agent_vision   # Agent 注入的视觉模型对象
        self.timeout = timeout
        self._conf = {}                    # 用户配置缓存（model/tap_strategy 等）
        self._user_configured = self._check_user_config()
        self._user_vision = None

    # ── 用户配置判定（构造时一次性，避免每次调用读盘）────────────────
    def _check_user_config(self) -> bool:
        """本地 vLLM serving 可能只有 base_url 而无 api_key，
        因此 base_url 有效也算已配置。"""
        from vision import _load_vision_conf
        conf = _load_vision_conf()
        self._conf = conf
        has_key = bool(conf.get("api_key") or os.environ.get("DEEPSEEK_API_KEY"))
        has_endpoint = bool(conf.get("base_url"))
        return has_key or has_endpoint

    def available(self) -> bool:
        """当前是否有可用的视觉模型（用户配置或 Agent 注入任一）。"""
        return self._user_configured or self.agent_vision is not None

    # ── 路由 ────────────────────────────────────────────────────────
    def _user(self):
        if self._user_vision is None:
            from vision import Vision
            self._user_vision = Vision(timeout=self.timeout)
        return self._user_vision

    def ask(self, prompt, image, temperature=0.0, timeout=None, **kwargs) -> str:
        to = timeout if timeout is not None else self.timeout
        if self._user_configured:
            return self._user().ask(prompt, image,
                                    temperature=temperature, timeout=to, **kwargs)
        if self.agent_vision is not None:
            fn = self.agent_vision.ask
            return fn(prompt, image, **_extra_args(fn, temperature, to))
        raise RuntimeError("未配置视觉模型，且 Agent 未提供视觉能力")

    def ask_json(self, prompt, image, fields, temperature=0.0, timeout=None,
                 **kwargs) -> dict:
        to = timeout if timeout is not None else self.timeout
        if self._user_configured:
            return self._user().ask_json(prompt, image, fields,
                                         temperature=temperature, timeout=to, **kwargs)
        if self.agent_vision is not None:
            fn = self.agent_vision.ask_json
            return fn(prompt, image, fields,
                      **_extra_args(fn, temperature, to))
        raise RuntimeError("未配置视觉模型，且 Agent 未提供视觉能力")

    # ── 元信息（tap_vision 选策略用）────────────────────────────────
    def model_name(self) -> str:
        """当前视觉模型名。Agent 注入路径无 model 属性时返回 ""
        （resolve_strategy 按无模型名回落 som）。"""
        if self._user_configured:
            if self._conf.get("model"):
                return self._conf["model"]
            try:
                return str(getattr(self._user(), "model", "") or "")
            except Exception:
                return ""
        return str(getattr(self.agent_vision, "model", "") or "")

    def tap_strategy(self) -> str:
        """用户显式配置的 tap_strategy（vision.json）。未配置返回 ""（= auto）。
        Agent 注入路径无用户配置，恒返回 ""。"""
        if self._user_configured:
            return str(self._conf.get("tap_strategy") or "")
        return ""


def _extra_args(fn, temperature, timeout) -> dict:
    """签名探测：Agent 视觉对象支持 temperature/timeout（显式形参或有
    **kwargs 兜底）才透传，旧式签名不传——兼容层，避免把路由问题放大成异常。"""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    flexible = any(p.kind == inspect.Parameter.VAR_KEYWORD
                   for p in params.values())
    out = {}
    for k, v in (("temperature", temperature), ("timeout", timeout)):
        if k in params or flexible:
            out[k] = v
    return out
