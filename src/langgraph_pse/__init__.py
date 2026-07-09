"""LangGraph PSE — Planner-Specialist-Evaluator 三角色 Agent 框架（LangGraph 实现）。

公开 API：
- build_graph(client=None, max_retries=3) — 构建编译后的 PSE 图
- create_model() — 创建带重试的 ChatOpenAI 客户端
- settings — 配置（环境变量）
"""

from .config import settings
from .model import create_model
from .graph import build_graph, PSEState

__all__ = ["build_graph", "create_model", "settings", "PSEState"]
