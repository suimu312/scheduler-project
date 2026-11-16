"""模型传输适配边界；通用 JSON 网关协议，不绑定供应商 SDK。"""
import json
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .core import AgentError, require


def setting(name, default=""):
    value = os.environ.get(name)
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            pass
    return default


def parse_object(text):
    """仅接受完整 JSON 对象；兼容模型多余的 Markdown 围栏，不修补内容。"""
    require(isinstance(text, str), "E_SCHEMA_INVALID", "网关响应必须为 JSON 文本")
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentError("E_SCHEMA_INVALID", "模型未返回有效 JSON 对象") from exc
    require(isinstance(value, dict), "E_SCHEMA_INVALID", "模型必须返回对象")
    return value


class ChatCompletionsProvider:
    """A 的 Provider 协议实现：真实豆包/Chat Completions，不返回离线结果。"""
    def __init__(self, base_url, api_key, model, timeout=120):
        parsed = urlsplit(base_url)
        require(parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")), "E_CONFIG", "远程模型地址必须使用 HTTPS")
        require(parsed.netloc and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment, "E_CONFIG", "模型地址不能含凭据、查询参数或片段")
        require(bool(api_key and model), "E_CONFIG", "需要配置 API Key 和模型 ID")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model, self.timeout = api_key, model, timeout

    @classmethod
    def from_env(cls):
        ark = bool(setting("ARK_API_KEY") or setting("ARK_MODEL"))
        if ark:
            return cls(setting("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"), setting("ARK_API_KEY"), setting("ARK_MODEL"))
        require(bool(setting("LLM_BASE_URL") and setting("LLM_API_KEY") and setting("LLM_MODEL")), "E_CONFIG", "未找到模型配置，请配置 ARK_API_KEY/ARK_MODEL，或 LLM_BASE_URL/LLM_API_KEY/LLM_MODEL")
        return cls(setting("LLM_BASE_URL"), setting("LLM_API_KEY"), setting("LLM_MODEL"))

    def __call__(self, agent_id, prompt, stage):
        request = parse_object(prompt)
        body = {"model": self.model, "stream": False, "messages": [
            {"role": "system", "content": request["system"] + "\n请输出 json 对象。"},
            {"role": "user", "content": request["user"]}],
            "response_format": {"type": "json_object"}}
        if urlsplit(self.endpoint).hostname == "ark.cn-beijing.volces.com":
            body["thinking"] = {"type": "disabled"}
        req = urllib.request.Request(self.endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=min(self.timeout, request.get("timeout_seconds", self.timeout))) as response:
                payload = json.load(response)
            content = payload["choices"][0]["message"]["content"]
            # 在传输边界校验，不能把空回答或 reasoning_content 当作最终 JSON。
            parse_object(content)
            return content
        except urllib.error.HTTPError as exc:
            detail = {401: "API Key 无效或未授权", 403: "无模型访问权限", 404: "模型 ID 或 API 地址不存在", 429: "额度不足或请求受限"}.get(exc.code, "模型 HTTP 错误")
            raise AgentError("E_MODEL_UNAVAILABLE", f"{detail}（HTTP {exc.code}）") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("模型请求超时") from None
            raise AgentError("E_MODEL_UNAVAILABLE", "无法连接模型服务") from None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise AgentError("E_SCHEMA_INVALID", "模型响应缺 choices/message/content") from None


class SchedulerGatewayModel:
    """把 B 的结构化请求适配到 A 的 LLMGateway.call，而不是重复建设网关。"""
    def __init__(self, gateway, stage="P3"):
        self.gateway, self.stage = gateway, stage

    @classmethod
    def from_env(cls, stage="P3"):
        from src.scheduler.llm_gateway import LLMGateway
        return cls(LLMGateway(provider=ChatCompletionsProvider.from_env()), stage)

    def __call__(self, request, timeout=120):
        text = self.gateway.call(request["agent_id"], json.dumps({**request, "timeout_seconds": timeout}, ensure_ascii=False), self.stage)
        require(not (isinstance(text, str) and text.startswith("[offline:")), "E_CONFIG", "A 的网关仍为离线 provider，请先注入真实 provider")
        return parse_object(text)


def configured_model(stage="P3"):
    """保留旧的自定义网关；配置豆包时统一走 A 的网关。"""
    if os.environ.get("B_MODEL_ENDPOINT"):
        return HttpModel.from_env()
    return SchedulerGatewayModel.from_env(stage)


class HttpModel:
    """POST {agent_id, step, system, user}; 返回 {response: {...}}。"""
    def __init__(self, endpoint, token=None):
        require(endpoint.startswith(("http://127.0.0.1:", "http://localhost:", "https://")), "E_CONFIG", "远程模型网关必须使用 HTTPS")
        self.endpoint, self.token = endpoint, token

    @classmethod
    def from_env(cls):
        endpoint = os.environ.get("B_MODEL_ENDPOINT", "")
        require(endpoint, "E_CONFIG", "没有配置模型。注入 model 回调或设置 B_MODEL_ENDPOINT；离线演示见 README")
        return cls(endpoint, os.environ.get("B_MODEL_TOKEN"))

    def __call__(self, prompt, timeout=120):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(self.endpoint, data=json.dumps(prompt, ensure_ascii=False).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            require(isinstance(payload.get("response"), dict), "E_SCHEMA_INVALID", "网关必须返回 response 对象")
            return payload["response"]
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError("模型请求超时") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("模型请求超时") from exc
            raise AgentError("E_MODEL_UNAVAILABLE", "模型网关不可用") from exc
