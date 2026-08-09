<div align="right">
  <a href="README.zh.md">🇨🇳 中文</a>
</div>

# LangGraph PSE

A **task-agnostic** [LangGraph](https://github.com/langchain-ai/langgraph)-powered **Planner–Specialist–Evaluator (PSE)** multi-agent framework. It models a generic *generate → programmatically verify → auto-fix* loop as an explicit **state graph** with conditional edges. **Add any task by dropping a folder under `tasks/`** and supplying a task name + a verification function — the core graph never changes.

This is the LangGraph sibling of [`crewai-pse`](../crewai-pse), [`autogen-pse`](../autogen-pse) and [`llamaindex-pse`](../llamaindex-pse) — same PSE philosophy, different orchestration primitive: a `StateGraph` with conditional edges instead of a crew, a group-chat, or an event workflow.

The repository currently ships **four tasks**, which together prove the core is genuinely reusable rather than a one-off:

- `crm-qa` — a `personal-crm` data-quality watchdog (deterministic scan + optional verified LLM report).
- `weekly-review` — a `personal-crm` weekly relationship review (deterministic aggregation + optional verified LLM report).
- `follow-up-draft` — a `personal-crm` follow-up message drafter (deterministic candidate + context aggregation + optional verified LLM drafts).
- `interview-questions` - a tech interview question bank generator (deterministic spec + optional verified LLM questions; sources: a programming language/role, a JD doc, or a candidate résumé).

> [!NOTE]
> **Cost.** The deterministic modes (`make crm-qa`, `make weekly-review`) cost **zero** — they never call an LLM. The `--llm` report modes cost one generation plus one round per fix retry; on DeepSeek Chat a report typically converges in **2 rounds** for well under **¥0.05**. The free **Agnes** provider (`--provider agnes`) makes LLM runs effectively free. Every run prints the round count and pass/fail of the programmatic checks.

## How It Works

The framework provides a reusable, **task-agnostic PSE engine** (`src/langgraph_pse`); each *task* supplies its own prompts, its own deterministic data layer, and a `verify_fn`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PSE ENGINE  (src/langgraph_pse — task-agnostic)                          │
│                                                                            │
│   build_graph(task, verify_fn, use_planner)                               │
│                                                                            │
│   START → [planner] → specialist → evaluator ─┬─(pass)──────────▶ END      │
│             (optional)                         └─(issues)─▶ fix ─┐         │
│                                                     ▲            │         │
│                                                     └────────────┘         │
│                                                  conditional edge          │
│                                                  (should_fix, max N)       │
│                                                                            │
│   evaluator = programmatic verify_fn  (+ LLM review on round 1)            │
│   fix       = LLM call that removes/revises ONLY the flagged issues,       │
│               with the real data re-injected so it can't fabricate         │
└──────────────────────────────────────────────────────────────────────────┘
            ▲
            │  each task plugs in via  tasks/<task>/prompts/*.md + run.py (verify_fn)
┌───────────┴─────────────────────────────────────────────────────────────┐
│  tasks/crm-qa/              ← Task 1: CRM data-quality watchdog         │
│  tasks/weekly-review/       ← Task 2: CRM weekly relationship review    │
│  tasks/follow-up-draft/     ← Task 3: CRM follow-up message drafter    │
│  tasks/interview-questions/ ← Task 4: tech interview question bank     │
│  tasks/<your-task>/         ← add your own; the engine stays untouched │
└───────────────────────────────────────────────────────────────────────────┘
```

1. **Planner (optional)** — an agent reads context via the sandboxed `read_file` tool and produces an execution plan. Toggled per task with `use_planner`.
2. **Specialist** — expands the plan (or the raw task input) into the final artifact (a report).
3. **Evaluator (merged gate)** — runs every round and combines two checks:
   - **Programmatic verification** via the task-supplied `verify_fn(state) -> (bad, ok)`. This is *not* an LLM judge — deterministic checks are far more reliable than asking a model to grade its own output (e.g. every number in the report is guaranteed to match the real data).
   - **LLM review** (first round only): an independent reviewer flags hallucinations, fabricated samples, or weak suggestions.
4. **Fix → Evaluator loop** — LangGraph's conditional edge re-runs `fix` up to `PSE_MAX_RETRIES` times. The real data is re-injected into the fix prompt so the model corrects wrong numbers rather than inventing new ones.

The retry loop is a natural fit for **conditional edges** — no manual loop counters, no re-invoking a team. The graph *is* the control flow.

### The task-agnostic core

`build_graph` is deliberately decoupled from any single task. The `evaluator` and `fix` nodes read the real data through a small `_real_data(state)` helper that accepts **any** task's data key (e.g. `scan_result` for crm-qa, `review_data` for weekly-review, `draft_data` for follow-up-draft; `interview-questions` reuses `scan_result`). That means a new task injects its own data object and reuses the graph verbatim — the four shipped tasks exercise exactly this path, which is the proof the core is reusable.

## Project Structure

```
langgraph-pse/
├── src/langgraph_pse/        # Core framework (task-agnostic)
│   ├── __init__.py           # Public API: build_graph(), create_model()
│   ├── config.py             # Settings from environment / .env
│   ├── model.py              # Retrying ChatOpenAI client (deepseek / agnes)
│   ├── tools.py              # read_file + run_bash (sandboxed) + query_crm (read-only)
│   ├── prompts.py            # Prompt loader → tasks/<task>/prompts/<name>.md
│   └── graph.py              # StateGraph: planner → specialist → evaluator → fix
├── tasks/                    # ← extension point: one folder per task
│   ├── crm-qa/               # Task 1: data-quality watchdog
│   │   ├── run.py            # Entry - deterministic scan (default) + optional LLM report
│   │   ├── qa_scan.py        # HTTP client -> personal-crm /api/qa/report (single source of truth)
│   │   └── prompts/{planner,specialist,evaluator}.md
│   ├── weekly-review/        # Task 2: weekly relationship review
│   │   ├── run.py            # Entry - deterministic aggregation (default) + optional LLM report
│   │   ├── review_data.py    # Read-only SQLite aggregation (metrics + cooling + follow-ups)
│   │   └── prompts/{planner,specialist,evaluator}.md
│   ├── follow-up-draft/      # Task 3: follow-up message drafter
│   │   ├── run.py            # Entry - deterministic candidate/context (default) + optional LLM drafts
│   │   ├── draft_data.py     # Read-only SQLite: follow-up candidates + real recent chat context
│   │   └── prompts/{planner,specialist,evaluator}.md
│   └── interview-questions/  # Task 4: tech interview question bank
│       ├── run.py            # Entry - deterministic spec (default) + optional LLM questions
│       └── prompts/{planner,specialist,evaluator}.md
├── pyproject.toml
├── Makefile
└── .env.example
```

## Adding a New Task

Because the engine is task-agnostic, you **never edit `src/`**. To add a task named `my-task`:

**1. Create the prompt folder** — `planner.md`, `specialist.md`, and `evaluator.md`:

```
tasks/my-task/prompts/planner.md
tasks/my-task/prompts/specialist.md
tasks/my-task/prompts/evaluator.md
```

The loader resolves `tasks/my-task/prompts/<name>.md` automatically when you pass `task="my-task"` to `build_graph`.

**2. Write a deterministic data layer + `verify_fn`** — a plain function that reads your real data (read-only) and a checker that returns `(bad, ok)` lists:

```python
def verify_fn(state) -> tuple[list[str], list[str]]:
    data = state["task_data"]["my_data"]   # your injected real data
    bad, ok = [], []
    # every claim in state["artifact"] must match `data`
    ...
    return bad, ok   # bad → triggers a fix round; empty bad → pass
```

**3. Wire it in `tasks/my-task/run.py`:**

```python
from langgraph_pse import build_graph

graph = build_graph(task="my-task", verify_fn=verify_fn, use_planner=True)
result = graph.invoke({
    "task_input": "…",
    "task_data": {"my_data": my_deterministic_data},
})
print(result["artifact"])
```

**4. (Optional) Add Makefile targets** following the existing pattern (`deterministic` / `--provider deepseek` / `--provider agnes`).

That's it. The core graph, retry logic, and sandbox are reused as-is.

## Installation

```bash
make install        # or: uv sync
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

For the LLM report you need **either** the `OPENAI_*` set (DeepSeek is OpenAI-compatible) **or** the `AGNES_*` set. Both are supported via `--provider {deepseek,agnes}`.

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅* | LLM API key (OpenAI-compatible, e.g. DeepSeek) |
| `OPENAI_BASE_URL` | ✅* | LLM API base URL |
| `OPENAI_MODEL` | ✅* | Model name (e.g. `deepseek-chat`) |
| `AGNES_KEY` | ✅† | Alternative: Agnes API key (free model) |
| `AGNES_BASE_URL` | ✅† | Alternative: Agnes base URL |
| `AGNES_MODEL` | ✅† | Alternative: Agnes model name (e.g. `agnes-2.0-flash`) |
| `PSE_ROOT` | ✅ | Sandbox root for `read_file` / `run_bash` |
| `CRM_DB_PATH` | ✅ | Path to personal-crm's `crm.db` (read-only; used by both tasks) |
| `PSE_MAX_RETRIES` | | Max evaluator/fix rounds (default: `3`) |

\* required if `--provider deepseek` (the default).  &nbsp; † required if `--provider agnes`.

## Tasks

### `crm-qa` — data-quality watchdog

Scans `crm.db` for known data-quality issues (duplicate `wechat_id`, un-renamed names, missing clean fields, orphan chats, timezone mismatches, empty records, …). With `--llm` it writes a verified Chinese QA report whose numbers are **guaranteed to match the scan** (enforced by `verify_fn`).

```bash
# Deterministic scan only (zero cost, no API key needed)
make crm-qa
python tasks/crm-qa/run.py --db /path/to/crm.db

# Natural-language QA report via LLM
make crm-qa-report            # --provider deepseek (default)
make crm-qa-agnes             # --provider agnes
python tasks/crm-qa/run.py --llm --provider agnes
```

> **Watchdog only, no auto-repair.** crm-qa intentionally *reports* problems; it never modifies `crm.db`. Any repair stays a manual step so a model can never mutate production data.

### `weekly-review` — weekly relationship review

The second task, added to prove the core is reusable. `review_data.py` runs a deterministic, read-only aggregation over `crm.db` (13 headline metrics + a "cooling relationships" Top-N table + follow-up sample + group breakdown, zero LLM). With `--llm` the PSE trio writes a Chinese review whose every metric, contact name, day-count, and follow-up date must match the aggregation exactly — the `verify_fn` rejects any fabricated contact or number.

```bash
# Deterministic aggregation only (zero cost)
make weekly-review
python tasks/weekly-review/run.py --db /path/to/crm.db

# Natural-language review via LLM
make weekly-review-report     # --provider deepseek (default)
make weekly-review-agnes      # --provider agnes
python tasks/weekly-review/run.py --llm --provider agnes
```

### `follow-up-draft` — follow-up message drafter

The third task, added to further prove the core is reusable. `draft_data.py` runs a deterministic, read-only query over `crm.db` to find contacts that have a `follow_up_date` set and are overdue or due within 7 days, then attaches each candidate's **real** recent chat (direction + local date + content), last-interaction date, and `follow_up_note`. With `--llm` the PSE trio drafts one personalized WeChat follow-up message per candidate; the `verify_fn` enforces that every draft names a real candidate, echoes any non-empty `follow_up_note`, and produces no fabricated contacts — so the model can never invent a relationship or shared history.

```bash
# Deterministic candidate + context aggregation only (zero cost)
make follow-up-draft
python tasks/follow-up-draft/run.py --db /path/to/crm.db

# Personalized follow-up drafts via LLM
make follow-up-draft-report     # --provider deepseek (default)
make follow-up-draft-agnes      # --provider agnes
python tasks/follow-up-draft/run.py --llm --provider agnes
```

### `interview-questions` - tech interview question bank

The fourth task, added to prove the core is reusable beyond CRM data. Unlike the three CRM tasks, it has **no database** - its source material is a programming language/role, a JD document, or a candidate résumé, plus a "topic checklist" derived from that source. Structural constraints (9 questions, difficulty 3/3/3, coding questions must contain a code block) are enforced by deterministic post-processing (`_normalize_artifact`); the `verify_fn` keeps only the content-level hard constraint: every question's topic must come from the declared checklist - so the model can never fabricate a topic. This eliminates retry death-loops on global-count constraints.

```bash
# Deterministic spec only (zero cost)
make interview-questions
make interview-questions SUBJECT=react-python
make interview-questions JD=work/docs/jobs/jd/kpmg.md
make interview-questions RESUME=work/docs/resume-pdf/zh-boss.pdf

# LLM-generated question bank
make interview-questions-report   # --provider deepseek (default)
make interview-questions-agnes    # --provider agnes
python tasks/interview-questions/run.py --resume work/docs/resume-pdf/zh-boss.md --llm --provider agnes
```

The three CRM tasks read the DB strictly read-only. `crm-qa` calls personal-crm's `GET /api/qa/report` — the single source of truth for QA checks (no duplicate scanning); `weekly-review` aggregates directly via `mode=ro&immutable=1`; `query_crm` allows only a single `SELECT`.

## Key Design Decisions

**Programmatic verification instead of pure LLM evaluation.** The Evaluator combines an LLM review (round 1) with a deterministic `verify_fn`, and the `verify_fn` is the authority. Deterministic checks catch hallucinated names, wrong counts, and fabricated samples that an LLM might "approve."

**Lenient matching, not brittle table-parsing.** `verify_fn` accepts a claim when the check name and the correct value appear anywhere in the report — it does not demand a specific Markdown table layout. This avoids false "hallucination" flags when the LLM formats output as a list instead of a table (a real bug that once caused a fix loop to never converge).

**The real data is re-injected into the fix prompt.** When a fix round runs, the deterministic data object is passed back to the model so it corrects wrong numbers rather than inventing plausible-looking replacements. The fix prompt also forbids fabricating rows for empty samples (write "none" instead).

**Sandboxed, read-only data access.** `read_file` only reads under `PSE_ROOT`; `run_bash` blocks destructive commands; `query_crm` allows only single `SELECT` statements; `weekly-review` opens the DB in `mode=ro&immutable=1`. `crm-qa` never touches the DB directly — it reads QA results from personal-crm's API. A model can never mutate production data.

## Relation to Sibling Frameworks

All four share the **PSE role model** and a **verify→fix loop**, but differ in orchestration:

| | `autogen-pse` | `crewai-pse` | `langgraph-pse` | `llamaindex-pse` |
|---|---|---|---|---|
| Orchestration | AutoGen `RoundRobinGroupChat` | CrewAI `Sequential` | **LangGraph `StateGraph` + conditional edges** | LlamaIndex `Workflow` + `@step` + Event |
| Retry loop | direct two-stage API + grep-check | programmatic verify in `run.py` | `add_conditional_edges("evaluator", should_fix)` | Evaluator returns `FixEvent` / `StopEvent` |
| Verify step | grep against source | regex/grep in `run.py` | injected `verify_fn` in the graph | injected `verify_fn` in the workflow |
| RAG | optional | — | — | **built-in** (`retriever`, source-grounded) |
| Reference use | asset-lens → next-week investment advice | project code → bilingual article → WordPress | **CRM QA / weekly review / follow-up drafts + interview question bank** | résumé tailoring (RAG) |
| Best for | cheap, frequent drafts | richer multi-agent publishing | workflows needing explicit state control + anti-hallucination gates | RAG-grounded generation |

## Security Notes

- **No hardcoded secrets.** All credentials are read from `.env`, which is gitignored.
- **Generated artifacts are gitignored.** Reports containing PII (`qa_report.md`, `weekly_review.md`, `scan_*.json`) never enter version control.
- **Sandboxed tools & read-only DB.** See [Key Design Decisions](#key-design-decisions).
- **No network-exposed service.** This project runs locally as a CLI.

---

## Related Articles

- English: [LangGraph PSE: Design Decisions](https://erishen.cn/langgraph-pse-design-decisions-en/)
- 中文: [LangGraph PSE：设计决策](https://erishen.cn/langgraph-pse-design-decisions/)

## License

MIT
