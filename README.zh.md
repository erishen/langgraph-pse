<div align="right">
  <a href="README.md">🌐 English</a>
</div>

# LangGraph PSE

基于 [LangGraph](https://github.com/langchain-ai/langgraph) 的 **Planner-Specialist-Verifier** 多 Agent 框架。它把通用的「生成 → 程序化核查 → 自动修正」循环建模成一张带条件边的显式**状态图**：任何「产出某物，再用程序化方式核查，不通过就自动修」的工作流，只要提供任务名 + 一个核查函数即可挂载。

这是 [`crewai-pse`](../crewai-pse) 和 [`autogen-pse`](../autogen-pse) 的 LangGraph 版本——同样的 PSE 理念，不同的编排原语：**用 `StateGraph` + 条件边**，而非 agent 循环或 team。

## 工作原理

```
START → [planner] → specialist → verify ─┬─(通过)─▶ END
                                         └─(有问题)─▶ fix → verify（循环，最多 N 轮）
```

1. **Planner（可选）** — ReAct agent 通过沙箱 `read_file` 读取上下文，产出提纲。
2. **Specialist** — 把提纲（或原始任务输入）展开为最终产物（文章 / 报告 / …）。
3. **Verify（程序化）** — 由任务注入的 `verify_fn(state) -> (bad, ok)` 做确定性核查。刻意**不做** LLM 裁判——确定性验证比让模型评判自己输出可靠得多。
4. **Fix → Verify 循环** — LangGraph 的条件边让 `fix`（删除被标记问题的 LLM 调用）最多重试 `PSE_MAX_RETRIES` 轮。

重试循环（Verify → Fix → Verify）天生适合**条件边**——不用手写循环计数、不用重复调用 team，图本身就是控制流。

## 为什么用 LangGraph？

| | crewai-pse | langgraph-pse |
|---|---|---|
| 编排 | `Crew` + `Process.sequential` | `StateGraph` + 条件边 |
| 重试循环 | `run.py` 里的手动 `for` 循环 | `add_conditional_edges("verify", should_fix)` |
| 工具调用 | CrewAI `Agent.tools` | LangGraph `create_react_agent` |
| 验证步骤 | `run.py` 里的正则/grep | 图内注入的 `verify_fn` |

## 项目结构

```
langgraph-pse/
├── src/langgraph_pse/        # 核心框架（任务无关）
│   ├── __init__.py           # 公开 API: build_graph(), create_model()
│   ├── config.py             # 从环境变量 / .env 读取配置
│   ├── model.py              # 带重试的 ChatOpenAI 客户端
│   ├── tools.py              # read_file（沙箱）+ run_bash（沙箱）+ query_crm（只读）
│   ├── prompts.py            # 提示词加载（tasks/<task>/prompts/*.md）
│   └── graph.py              # StateGraph: planner → specialist → verify → fix
├── tasks/
│   └── crm-qa/               # 任务：personal-crm 数据质量看门狗
│       ├── run.py            # 入口——确定性扫描（默认）+ 可选 LLM 报告
│       ├── qa_scan.py        # 只读 SQLite QA 扫描器
│       └── prompts/
│           └── specialist.md # crm-qa 任务的 Specialist 系统提示词
├── pyproject.toml
├── Makefile
└── .env.example
```

## 安装

```bash
make install        # 或: uv sync
```

## 配置

把 `.env.example` 复制为 `.env` 并填写：

```bash
cp .env.example .env
```

| 变量 | 必填 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | LLM API key（OpenAI 兼容） |
| `OPENAI_BASE_URL` | ✅ | LLM API base URL |
| `OPENAI_MODEL` | ✅ | 模型名（如 `gpt-4o`） |
| `PSE_ROOT` | ✅ | `read_file` / `run_bash` 沙箱根路径 |
| `CRM_DB_PATH` | ✅ | personal-crm 的 `crm.db` 路径（只读） |
| `PSE_MAX_RETRIES` | | 最大验证/修正轮数（默认 `3`） |
| `AGNES_KEY` | | 备选：Agnes API key（免费模型） |
| `AGNES_BASE_URL` | | 备选：Agnes base URL |

## 用法 — crm-qa（数据质量看门狗）

```bash
# 仅确定性扫描（零成本，无需 API key）
make crm-qa
python tasks/crm-qa/run.py --db /path/to/crm.db

# 额外用 LLM 生成自然语言 QA 报告（需 langgraph + key）
make crm-qa FLAGS=--llm
python tasks/crm-qa/run.py --llm
```

看门狗扫描 `crm.db` 的已知数据质量问题（重复 wechat_id、未改名、clean 字段缺失、孤儿聊天、时区错位、空记录等）；加 `--llm` 会写出一份**数字经过核查、保证与扫描一致**的中文 QA 报告。

## 新建一个任务

1. 创建 `tasks/<your-task>/prompts/{planner,specialist}.md`。
2. 调用 `build_graph(task="<your-task>", verify_fn=..., use_planner=...)`。
3. `verify_fn(state) -> (bad, ok)` 即你的确定性核查；图会循环 `fix` 直到通过或达到 `max_retries`。

## 安全说明

- **无硬编码密钥。** 所有凭证均从 `.env` 读取，`.env` 已 gitignore。
- **沙箱工具.** `read_file` 只能读 `PSE_ROOT` 内文件；`run_bash` 拦截破坏性命令（`rm -rf`、`dd`、`curl|sh` 等）并在 `PSE_ROOT` 内运行。
- **数据库只读.** `query_crm` 只允许对 `crm.db` 的单条 `SELECT`；QA 扫描器以 `mode=ro&immutable=1` 打开库。
- **无网络暴露服务.** 本项目仅作为本地 CLI 运行。

## 许可证

MIT
