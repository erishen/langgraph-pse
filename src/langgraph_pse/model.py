"""LLM 客户端 — 带指数退避重试的 ChatOpenAI。

第三方网关（Agnes / DeepSeek）偶发故障：404、503、连接层 ConnectionReset。
LangChain 的 `max_retries` 已能覆盖大部分瞬时错误；这里再叠加单次超时，
避免连接挂死无限等待。

支持两种 provider（均 OpenAI 兼容协议）：
  - "deepseek"（默认）：用 OPENAI_* 变量
  - "agnes"：用 AGNES_* 变量
"""


from typing import Optional

from langchain_openai import ChatOpenAI

from .config import settings


def create_model(provider: str = "deepseek", max_tokens: Optional[int] = None) -> ChatOpenAI:
    """创建 OpenAI 兼容 ChatModel（含工具调用能力）。

    provider: "deepseek" | "agnes"
    max_tokens: 最大输出 token 数。None 表示使用模型默认值。
        长输出任务（如 interview-questions 生成 9 道题）建议显式设置较大值（如 16000）。
    """
    if provider == "agnes":
        api_key = settings.AGNES_KEY
        base_url = settings.AGNES_BASE_URL
        model = settings.AGNES_MODEL
        label = "AGNES"
    else:
        api_key = settings.OPENAI_API_KEY
        base_url = settings.OPENAI_BASE_URL
        model = settings.OPENAI_MODEL
        label = "OPENAI"

    if not api_key:
        raise RuntimeError(
            f"未设置 {label}_API_KEY / {label}_KEY。请在 .env 中配置（参考 .env.example）。"
        )
    if not model:
        raise RuntimeError(
            f"未设置 {label}_MODEL。请在 .env 中补充模型名（例如 AGNES_MODEL）。"
        )
    kwargs = dict(
        model=model,
        api_key=api_key,
        base_url=base_url or None,
        # LangChain 内置重试，覆盖瞬时网关错误
        max_retries=settings.PSE_MAX_RETRIES or 6,
        timeout=180,
        streaming=False,
    )
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return ChatOpenAI(**kwargs)
