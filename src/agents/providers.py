"""模型传输适配边界；通用 JSON 网关协议，不绑定供应商 SDK。"""
import json
import os
import socket
import urllib.error
import urllib.request

from .core import AgentError, require


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
