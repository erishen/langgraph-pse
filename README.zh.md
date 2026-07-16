<div align="right">
  <a href="README.md">🌐 English</a>
</div>

# LangGraph PSE

一个**任务无关的**、基于 [LangGraph](https://github.com/langchain-ai/langgraph) 的 **Planner–Specialist–Evaluator（PSE）** 多 Agent 框架。它把通用的「生成 → 程序化核查 → 自动修正」循环建模成一张带条件边的显式**状态图**。**新增任务只需在 `tasks/` 下放一个文件夹**、提供任务名 + 一个核查函数——核心图代码永不改动。

这是 [`crewai-pse`](../crewai-pse)、[`autogen-pse`](../autogen-pse)、[`llamaindex-pse`](../llamaindex-pse) 的 LangGraph 版本——同样的 PSE 理念，不同的编排原语：用 `StateGraph` + 条件边，而非 crew、群聊或事件工作流。

仓库当前内置**四个任务**，四者共同证明核心是真正可复用的，而非一次性实现：

- `crm-qa`——`personal-crm` 数据质量看门狗（确定性扫描 + 可选的经核查 LLM 报告）。
- `weekly-review`——`personal-crm` 每周关系复盘（确定性聚合 + 可选的经核查 LLM 报告）。
- `follow-up-draft`--`personal-crm` 跟进消息草拟（确定性候选 + 上下文聚合 + 可选的经核查 LLM 草稿）。
- `interview-questions`--技术面试题库生成（确定性规格 + 可选的经核查 LLM 题库；素材：编程语言 / 岗位、JD 文档、候选人简历）。

> [!NOTE]
> **成本。** 确定性模式（`make crm-qa`、`make weekly-review`）**零成本**——完全不调 LLM。`--llm` 报告模式的成本 = 一次生成 + 每轮修正一次调用；在 DeepSeek Chat 上一篇报告通常 **2 轮收敛**、远低于 **¥0.05**。免费的 **Agnes** 网关（`--provider agnes`）让 LLM 运行基本免费。每次运行都会打印轮数与程序化核查的通过/失败情况。

## 工作原理

框架提供一个可复用、**任务无关的 PSE 引擎**（`src/langgraph_pse`）；每个*任务*自带提示词、自带确定性数据层，以及一个 `verify_fn`。

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PSE 引擎  (src/langgraph_pse —— 任务无关)                                  │
│                                                                            │
│   build_graph(task, verify_fn, use_planner)                               │
│                                                                            │
│   START → [planner] → specialist → evaluator ─┬─(通过)──────────▶ END       │
│             (可选)                             └─(有问题)─▶ fix ─┐          │
│                                                     ▲            │         │
│                                                     └────────────┘         │
│                                                  条件边                     │
│                                                  (should_fix, 最多 N 轮)    │
│                                                                            │
│   evaluator = 程序化 verify_fn （第 1 轮附带 LLM 评审）                     │
│   fix       = LLM 调用，仅修正被标记的问题，并回注真实数据，杜绝编造        │
└──────────────────────────────────────────────────────────────────────────┘
            ▲
            │  每个任务通过  tasks/<task>/prompts/*.md + run.py (verify_fn)  挂载
┌───────────┴──────────────────────────────────────────────┐
│  tasks/crm-qa/              ← 任务 1：CRM 数据质量看门狗 │
│  tasks/weekly-review/       ← 任务 2：CRM 每周关系复盘   │
│  tasks/follow-up-draft/     ← 任务 3：CRM 跟进消息草拟   │
│  tasks/interview-questions/ ← 任务 4：技术面试题库生成   │
│  tasks/<你的任务>/          ← 自行添加；引擎保持不动     │
└──────────────────────────────────────────────────────────┘
```

1. **Planner（可选）**——通过沙箱化的 `read_file` 工具读取上下文，产出执行计划。按任务用 `use_planner` 开关。
2. **Specialist**——把计划（或原始任务输入）扩写为最终产物（一份报告）。
3. **Evaluator（合并关卡）**——每轮都跑，组合两道检查：
   - **程序化核查**：任务提供的 `verify_fn(state) -> (bad, ok)`。这**不是** LLM 裁判——确定性检查远比让模型给自己打分可靠（例如报告里的每个数字都保证与真实数据一致）。
   - **LLM 评审**（仅第 1 轮）：独立评审员标出幻觉、编造样本或空泛建议。
4. **Fix → Evaluator 循环**——LangGraph 的条件边最多重跑 `fix` `PSE_MAX_RETRIES` 次。修正时会把真实数据回注进提示词，让模型**改对数字**而不是**编造新数字**。

重试循环天然契合**条件边**——无需手写计数器、无需重新拉起 team。图本身即控制流。

### 任务无关的核心

`build_graph` 刻意与任何单一任务解耦。`evaluator` 和 `fix` 节点通过一个小助手 `_real_data(state)` 读取真实数据，它接受**任意**任务的数据键（crm-qa 用 `scan_result`，weekly-review 用 `review_data`，follow-up-draft 用 `draft_data`，interview-questions 复用 `scan_result`）。这意味着新任务只需注入自己的数据对象、原样复用图——四个内置任务走的正是这条路径，这就是核心可复用的证明。

## 目录结构

```
langgraph-pse/
├── src/langgraph_pse/        # 核心框架（任务无关）
│   ├── __init__.py           # 公共 API：build_graph()、create_model()
│   ├── config.py             # 从环境变量 / .env 读取配置
│   ├── model.py              # 带重试的 ChatOpenAI 客户端（deepseek / agnes）
│   ├── tools.py              # read_file + run_bash（沙箱）+ query_crm（只读）
│   ├── prompts.py            # 提示词加载器 → tasks/<task>/prompts/<name>.md
│   └── graph.py              # StateGraph：planner → specialist → evaluator → fix
├── tasks/                    # ← 扩展点：一个任务一个文件夹
│   ├── crm-qa/               # 任务 1：数据质量看门狗
│   │   ├── run.py            # 入口--确定性扫描（默认）+ 可选 LLM 报告
│   │   ├── qa_scan.py        # personal-crm /api/qa/report 的 HTTP 客户端（唯一真源）
│   │   └── prompts/{planner,specialist,evaluator}.md
│   ├── weekly-review/        # 任务 2：每周关系复盘
│   │   ├── run.py            # 入口--确定性聚合（默认）+ 可选 LLM 报告
│   │   ├── review_data.py    # 只读 SQLite 聚合（指标 + 变冷关系 + 待跟进）
│   │   └── prompts/{planner,specialist,evaluator}.md
│   ├── follow-up-draft/      # 任务 3：跟进消息草拟
│   │   ├── run.py            # 入口--确定性候选 / 上下文（默认）+ 可选 LLM 草稿
│   │   ├── draft_data.py     # 只读 SQLite：跟进候选 + 真实近期聊天上下文
│   │   └── prompts/{planner,specialist,evaluator}.md
│   └── interview-questions/  # 任务 4：技术面试题库生成
│       ├── run.py            # 入口--确定性规格（默认）+ 可选 LLM 题库
│       └── prompts/{planner,specialist,evaluator}.md
├── pyproject.toml
├── Makefile
└── .env.example
```

## 新增一个任务

由于引擎任务无关，你**永远不用改 `src/`**。新增名为 `my-task` 的任务：

**1. 建提示词文件夹**——`planner.md`、`specialist.md`、`evaluator.md`：

```
tasks/my-task/prompts/planner.md
tasks/my-task/prompts/specialist.md
tasks/my-task/prompts/evaluator.md
```

当你给 `build_graph` 传 `task="my-task"` 时，加载器会自动解析 `tasks/my-task/prompts/<name>.md`。

**2. 写确定性数据层 + `verify_fn`**——一个只读读取真实数据的普通函数，和一个返回 `(bad, ok)` 两个列表的核查器：

```python
def verify_fn(state) -> tuple[list[str], list[str]]:
    data = state["task_data"]["my_data"]   # 你注入的真实数据
    bad, ok = [], []
    # state["artifact"] 里的每条主张都必须命中 `data`
    ...
    return bad, ok   # bad 非空 → 触发一轮修正；bad 为空 → 通过
```

**3. 在 `tasks/my-task/run.py` 里接线：**

```python
from langgraph_pse import build_graph

graph = build_graph(task="my-task", verify_fn=verify_fn, use_planner=True)
result = graph.invoke({
    "task_input": "…",
    "task_data": {"my_data": my_deterministic_data},
})
print(result["artifact"])
```

**4.（可选）加 Makefile 目标**，沿用现有模式（`确定性` / `--provider deepseek` / `--provider agnes`）。

就这样。核心图、重试逻辑、沙箱全部原样复用。

## 安装

```bash
make install        # 或：uv sync
```

## 配置

复制 `.env.example` 为 `.env` 并填入你的值：

```bash
cp .env.example .env
```

跑 LLM 报告需要**要么**配好 `OPENAI_*`（DeepSeek 兼容 OpenAI 协议），**要么**配好 `AGNES_*`。二者都可通过 `--provider {deepseek,agnes}` 切换。

| 变量 | 必需 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | ✅* | LLM API key（OpenAI 兼容，如 DeepSeek） |
| `OPENAI_BASE_URL` | ✅* | LLM API base URL |
| `OPENAI_MODEL` | ✅* | 模型名（如 `deepseek-chat`） |
| `AGNES_KEY` | ✅† | 备选：Agnes API key（免费模型） |
| `AGNES_BASE_URL` | ✅† | 备选：Agnes base URL |
| `AGNES_MODEL` | ✅† | 备选：Agnes 模型名（如 `agnes-2.0-flash`） |
| `PSE_ROOT` | ✅ | `read_file` / `run_bash` 的沙箱根目录 |
| `CRM_DB_PATH` | ✅ | personal-crm 的 `crm.db` 路径（只读；两个任务都用） |
| `PSE_MAX_RETRIES` | | evaluator/fix 最大轮数（默认 `3`） |

\* 用 `--provider deepseek`（默认）时必需。  &nbsp; † 用 `--provider agnes` 时必需。

## 任务

### `crm-qa`——数据质量看门狗

扫描 `crm.db` 里已知的数据质量问题（重复 `wechat_id`、未改名、缺清洗字段、孤儿聊天、时区错位、空记录……）。加 `--llm` 会写出一份经核查的中文 QA 报告，其中数字**保证与扫描一致**（由 `verify_fn` 强制）。

```bash
# 仅确定性扫描（零成本，无需 API key）
make crm-qa
python tasks/crm-qa/run.py --db /path/to/crm.db

# 用 LLM 生成自然语言 QA 报告
make crm-qa-report            # --provider deepseek（默认）
make crm-qa-agnes             # --provider agnes
python tasks/crm-qa/run.py --llm --provider agnes
```

> **只看门、不自动修。** crm-qa 刻意只*报告*问题，绝不修改 `crm.db`。任何修复都保留为人工步骤，让模型永远无法改动生产数据。

### `weekly-review`——每周关系复盘

第二个任务，专为证明核心可复用而加。`review_data.py` 对 `crm.db` 做确定性只读聚合（13 项核心指标 + 「变冷关系」Top-N 表 + 待跟进样本 + 分组透视，零 LLM）。加 `--llm` 后由 PSE 三角色写出中文复盘，其中每个指标、联系人名、天数、跟进日期都必须与聚合精确一致——`verify_fn` 会拒绝任何编造的联系人或数字。

```bash
# 仅确定性聚合（零成本）
make weekly-review
python tasks/weekly-review/run.py --db /path/to/crm.db

# 用 LLM 生成自然语言复盘
make weekly-review-report     # --provider deepseek（默认）
make weekly-review-agnes      # --provider agnes
python tasks/weekly-review/run.py --llm --provider agnes
```

### `follow-up-draft`--跟进消息草拟

第三个任务，进一步验证核心可复用。`draft_data.py` 对 `crm.db` 做确定性只读查询，找出设了 `follow_up_date` 且已逾期或 7 天内到期的联系人，并附上每位候选人的**真实**近期聊天（方向 + 本地日期 + 内容）、最近互动日期与 `follow_up_note`。加 `--llm` 后由 PSE 三角色为每位候选人生成一条个性化微信跟进文案；`verify_fn` 强制每条草稿点名真实候选人、回显非空的 `follow_up_note`、且不得编造联系人--模型永远无法凭空捏造关系或共同经历。

```bash
# 仅确定性候选 + 上下文聚合（零成本）
make follow-up-draft
python tasks/follow-up-draft/run.py --db /path/to/crm.db

# 个性化跟进草稿（LLM）
make follow-up-draft-report     # --provider deepseek（默认）
make follow-up-draft-agnes      # --provider agnes
python tasks/follow-up-draft/run.py --llm --provider agnes
```

### `interview-questions`--技术面试题库生成

第四个任务，证明核心在 CRM 之外也可复用。与三个 CRM 任务不同，它**没有数据库**--出题素材是编程语言 / 岗位、JD 文档或候选人简历，外加从素材中提取的「考察主题清单」。结构性约束（题量 9、难度 3/3/3、编码题含代码块）一律由确定性后处理 `_normalize_artifact` 保证；`verify_fn` 仅保留内容层硬约束：每题主题必须来自声明清单--模型永远无法编造主题。这彻底消除了全局计数类约束导致的 retry 死循环。

```bash
# 仅确定性规格（零成本）
make interview-questions
make interview-questions SUBJECT=react-python
make interview-questions JD=work/docs/jobs/jd/kpmg.md
make interview-questions RESUME=work/docs/resume-pdf/zh-boss.pdf

# LLM 生成题库
make interview-questions-report  # --provider deepseek（默认）
make interview-questions-agnes   # --provider agnes
python tasks/interview-questions/run.py --resume work/docs/resume-pdf/zh-boss.md --llm --provider agnes
```

三个 CRM 任务都严格只读打开数据库（扫描/聚合用 `mode=ro&immutable=1`；`query_crm` 工具仅允许单条 `SELECT`）；`interview-questions` 无数据库，仅经 `read_file` 读取 JD / 简历文件。

## 关键设计决策

**用程序化核查而非纯 LLM 评估。** Evaluator 组合了 LLM 评审（第 1 轮）与确定性 `verify_fn`，且 `verify_fn` 是权威。确定性检查能抓住 LLM 可能「批准」的幻觉人名、错误计数、编造样本。

**宽松匹配，而非脆弱的表格解析。** `verify_fn` 只要报告里任意位置出现「检查名 + 正确的值」就判过——不强制某种 Markdown 表格排版。这避免了 LLM 用列表而非表格输出时被误判为「幻觉」（这是曾导致修正循环永不收敛的真实 bug）。

**真实数据回注修正提示。** 触发修正轮时，确定性数据对象会被回传给模型，让它**改对数字**而不是**编造貌似合理的替代**。修正提示还禁止为空样本编造行（应写「无」）。

**沙箱化、只读的数据访问。** `read_file` 只读 `PSE_ROOT` 下的文件；`run_bash` 拦截破坏性命令；`query_crm` 仅允许单条 `SELECT`；weekly-review 以 `mode=ro&immutable=1` 打开库；crm-qa 从不直读数据库，而是经 personal-crm 的 API 读取（检查项唯一真源在 personal-crm 后端）。模型永远无法改动生产数据。

## 与兄弟框架的关系

四者共享 **PSE 角色模型**与**验证→修正循环**，区别在编排：

| | `autogen-pse` | `crewai-pse` | `langgraph-pse` | `llamaindex-pse` |
|---|---|---|---|---|
| 编排 | AutoGen `RoundRobinGroupChat` | CrewAI `Sequential` | **LangGraph `StateGraph` + 条件边** | LlamaIndex `Workflow` + `@step` + Event |
| 重试循环 | 两段式直连 API + grep 核查 | `run.py` 里程序化核查 | `add_conditional_edges("evaluator", should_fix)` | Evaluator 返回 `FixEvent` / `StopEvent` |
| 核查步骤 | 对源码 grep | `run.py` 里正则/grep | 图中注入 `verify_fn` | 工作流中注入 `verify_fn` |
| RAG | 可选 | — | — | **内置**（`retriever`，源头接地） |
| 实际用途 | asset-lens → 下周投资建议 | 项目代码 → 中英文章 → WordPress | **CRM QA / 每周复盘 / 跟进草拟 + 面试题库** | 简历定制（RAG） |
| 最适合 | 便宜、高频草稿 | 更丰富的多 Agent 发布 | 需要显式状态控制 + 抗幻觉关卡的工作流 | RAG 接地生成 |

## 安全说明

- **无硬编码密钥。** 所有凭据从 `.env` 读取，且 `.env` 已 gitignore。
- **生成产物已 gitignore。** 含 PII 的报告（`qa_report.md`、`weekly_review.md`、`scan_*.json`）永不进版本库。
- **沙箱工具 + 只读库。** 见[关键设计决策](#关键设计决策)。
- **无网络暴露服务。** 本项目作为本地 CLI 运行。

## 许可证

MIT
