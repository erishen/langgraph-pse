<div align="right">
  <a href="README.zh.md">🇨🇳 中文</a>
</div>

# LangGraph PSE

A **Planner-Specialist-Evaluator** multi-agent framework built on [LangGraph](https://github.com/langchain-ai/langgraph). It models a generic *generate → verify → fix* loop as an explicit **state graph** with conditional edges, so that any "produce something, then programmatically check it, and auto-fix if it fails" workflow can be wired up by supplying a task name and a verification function.

This is the LangGraph sibling of [`crewai-pse`](../crewai-pse) and [`autogen-pse`](../autogen-pse) — same PSE philosophy, different orchestration primitive: a `StateGraph` with conditional edges instead of an agent loop or a team.

## How It Works

```
START → [planner] → specialist → evaluator ─┬─(pass)─▶ END
                                          └─(issues)─▶ fix → evaluator (loop, max N)
```

1. **Planner (optional)** — an agent reads context via the sandboxed `read_file` tool and produces an outline.
2. **Specialist** — expands the outline (or the raw task input) into the final artifact (report, …).
3. **Evaluator (merged gate)** — runs every round and combines two checks:
   - **Programmatic verification** via a task-supplied `verify_fn(state) -> (bad, ok)`. This is *not* an LLM judge — deterministic checks are far more reliable than asking a model to grade its own output (e.g. it guarantees every number in the report matches the scan).
   - **LLM review** (first round only): an independent reviewer inspects the artifact against the real data and flags hallucinations, fabricated samples, or weak suggestions.
4. **Fix → Evaluator loop** — LangGraph's conditional edge re-runs `fix` (an LLM call that *removes/revises* the flagged issues) up to `PSE_MAX_RETRIES` times.

The retry loop is a natural fit for **conditional edges** — no manual loop counters, no re-invoking a team. The graph *is* the control flow.

## Why LangGraph here?

| | crewai-pse | langgraph-pse |
|---|---|---|
| Orchestration | `Crew` + `Process.sequential` | `StateGraph` + conditional edges |
| Retry loop | manual `for` loop in `run.py` | `add_conditional_edges("evaluator", should_fix)` |
| Tool use | CrewAI `Agent.tools` | LangGraph `create_agent` (v1 API) |
| Verify step | regex/grep in `run.py` | injected `verify_fn` in the graph |
| Review gate | — | dedicated **Evaluator** node (LLM review + programmatic check) |

## Project Structure

```
langgraph-pse/
├── src/langgraph_pse/        # Core framework (task-agnostic)
│   ├── __init__.py           # Public API: build_graph(), create_model()
│   ├── config.py             # Settings from environment / .env
│   ├── model.py              # Retrying ChatOpenAI client (deepseek / agnes)
│   ├── tools.py              # read_file (sandboxed) + run_bash (sandboxed) + query_crm (read-only)
│   ├── prompts.py            # Prompt loader (tasks/<task>/prompts/*.md)
│   └── graph.py              # StateGraph: planner → specialist → evaluator → fix
├── tasks/
│   └── crm-qa/               # Task: personal-crm data-quality watchdog
│       ├── run.py            # Entry — deterministic scan (default) + optional LLM report
│       ├── qa_scan.py        # Read-only SQLite QA scanner
│       └── prompts/
│           ├── planner.md    # Planner system prompt
│           ├── specialist.md # Specialist system prompt
│           └── evaluator.md  # Evaluator system prompt (LLM review rules)
├── pyproject.toml
├── Makefile
└── .env.example
```

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
| `CRM_DB_PATH` | ✅ | Path to personal-crm's `crm.db` (read-only) |
| `PSE_MAX_RETRIES` | | Max evaluator/fix rounds (default: `3`) |

\* required if `--provider deepseek` (the default).  &nbsp; † required if `--provider agnes`.

## Usage — crm-qa (data-quality watchdog)

```bash
# Deterministic scan only (zero cost, no API key needed)
make crm-qa
make crm-qa-scan
python tasks/crm-qa/run.py --db /path/to/crm.db

# Natural-language QA report via LLM (needs key)
make crm-qa-report            # --provider deepseek (default)
make crm-qa-agnes             # --provider agnes
python tasks/crm-qa/run.py --llm --provider agnes

# Pass extra flags (e.g. cap retries)
make crm-qa-agnes FLAGS="--max-retries 2"

# List every command
make help
```

The watchdog scans `crm.db` for known data-quality issues (duplicate `wechat_id`, un-renamed names, missing clean fields, orphan chats, timezone mismatches, empty records, …) and, with `--llm`, writes a verified Chinese QA report whose numbers are **guaranteed to match the scan** (enforced by `verify_fn`).

> **Design principle — watchdog only, no auto-repair.** crm-qa intentionally *reports* problems; it never modifies `crm.db`. All DB access is read-only (`mode=ro&immutable=1` for the scanner, single `SELECT` only for `query_crm`). Any repair stays a manual step so a model can never mutate production data.

## Building a new task

1. Create `tasks/<your-task>/prompts/{planner,specialist,evaluator}.md`.
2. Call `build_graph(task="<your-task>", verify_fn=..., use_planner=...)`.
3. `verify_fn(state) -> (bad, ok)` is your deterministic check; the graph loops `fix` until it passes or hits `max_retries`.

## Security Notes

- **No hardcoded secrets.** All credentials are read from `.env`, which is gitignored. Generated artifacts containing PII (`qa_report.md`, `scan_*.json`) are also gitignored.
- **Sandboxed tools.** `read_file` only reads under `PSE_ROOT`; `run_bash` blocks destructive commands (`rm -rf`, `dd`, `curl|sh`, …) and runs in `PSE_ROOT`.
- **Read-only DB access.** `query_crm` allows only single `SELECT` statements against `crm.db`; the QA scanner opens the DB in `mode=ro&immutable=1`.
- **No network-exposed service.** This project runs locally as a CLI.

## License

MIT
