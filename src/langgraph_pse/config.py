"""配置管理 — 从环境变量和 .env 加载。

所有凭证（API key 等）一律走环境变量，绝不硬编码。
"""

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")
    PSE_MAX_RETRIES: int = int(os.getenv("PSE_MAX_RETRIES", "3"))
    # 免费网关（与 DeepSeek 同 OpenAI 兼容协议，独立 key / base_url / 模型）
    FREE_KEY: str = os.getenv("FREE_KEY", "")
    FREE_BASE_URL: str = os.getenv("FREE_BASE_URL", "")
    FREE_MODEL: str = os.getenv("FREE_MODEL", "")


settings = Settings()
