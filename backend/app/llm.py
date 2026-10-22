"""实验四 S4：OpenAI 兼容格式的大模型客户端（只用标准库，避免新增依赖）。

离线镜像里不能 pip 安装 SDK，所以用 urllib 直接调 `/chat/completions`。
任何失败都转成明确的 APIError，绝不使用模板文案冒充模型输出。
"""
import json
import urllib.error
import urllib.request

from .errors import APIError


def chat_json(system: str, user: str, settings, temperature: float = 0.3,
              max_tokens: int = 700) -> dict:
    """调用大模型并要求返回 JSON 对象；解析失败按 502 处理。"""
    if settings.llm_api_key is None:
        raise APIError(503, "LLM_NOT_CONFIGURED", "未配置大模型密钥，无法生成解释")
    payload = {
        "model": settings.llm_model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    request = urllib.request.Request(
        settings.llm_base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value(),
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.llm_timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise APIError(502, "LLM_UPSTREAM_ERROR",
                       f"大模型接口返回 {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise APIError(503, "LLM_UNREACHABLE", "大模型接口不可达或超时") from None
    except json.JSONDecodeError:
        raise APIError(502, "LLM_BAD_RESPONSE", "大模型返回不是合法 JSON") from None

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise APIError(502, "LLM_BAD_RESPONSE", "大模型返回结构异常") from None
    if isinstance(content, list):  # 部分兼容层会把 content 拆成片段
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        raise APIError(502, "LLM_BAD_RESPONSE", "大模型未按要求返回 JSON") from None
    if not isinstance(parsed, dict):
        raise APIError(502, "LLM_BAD_RESPONSE", "大模型返回的 JSON 不是对象") from None
    return parsed
