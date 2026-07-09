<div align="right">
  <a href="README.zh.md">🇨🇳 中文</a>
</div>

# LangGraph PSE

A **Planner-Specialist-Verifier** multi-agent framework built on [LangGraph](https://github.com/langchain-ai/langgraph). It models a generic *generate → verify → fix* loop as an explicit **state graph** with conditional edges, so that any "produce something, then programmatically check it, and auto-fix if it fails" workflow can be wired up by supplying a task name and a verification function.

This is the LangGraph sibling of [`crewai-pse`](../crewai-pse) and [`autogen-pse`](../autogen-pse) — same PSE philosophy, different orchestration primitive: a `StateGraph` with conditional edges instead of an agent loop or a team.

## How It Works

```
START → [planner] → specialist → verify ─┬─(pass)─▶ END
                                         └─(issues)─▶ fix → verify (loop, max N)
```

1. **Planner (optional)** — a ReAct agent reads context via the sandboxed `read_file` tool and produces an outline.
2. **Specialist** — expands the outline (or the raw task input) into the final artifact (article, report, …).
3. **Verify (programmatic)** — a task-supplied `verify_fn(state) -> (bad, ok)` performs deterministic checks. This is intentionally *not* an LLM judge — deterministic verification is far more reliable than asking a model to grade its own output.
4. **Fix → Verify loop** — LangGraph's conditional edge re-runs `fix` (an LLM call that *removes* the flagged issues) up to `PSE_MAX_RETRIES` times.

The retry loop is a natural fit for **conditional edges** — no manual loop counters, no re-invoking a team. The graph *is* the control flow.

## Why LangGraph here?

| | crewai-pse | langgraph-pse |
|---|---|---|
| Orchestration | `Crew` + `Process.sequential` | `StateGraph` + conditional edges |
| Retry loop | manual `for` loop in `run.py` | `add_conditional_edges("verify", should_fix)` |
| Tool use | CrewAI `Agent.tools` | LangGraph `create_react_agent` |
| Verify step | regex/grep in `run.py` | injected `verify_fn` in the graph |

## Project Structure

```
langgraph-pse/
├── src/langgraph_pse/        # Core framework (task-agnostic)
│   ├── __init__.py           # Public API: build_graph(), create_model()
│   ├── config.py             # Settings from environment / .env
│   ├── model.py              # Retrying ChatOpenAI client
│   ├── tools.py              # read_file (sandboxed) + run_bash (sandboxed) + query_crm (read-only)
│   ├── prompts.py            # Prompt loader (tasks/<task>/prompts/*.md)
│   └── graph.py              # StateGraph: planner → specialist → verify → fix
├── tasks/
│   └── crm-qa/               # Task: personal-crm data-quality watchdog
│       ├── run.py            # Entry — deterministic scan (default) + optional LLM report
│       ├── qa_scan.py        # Read-only SQLite QA scanner
│       └── prompts/
│           └── specialist.md # Specialist system prompt for the crm-qa task
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

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | LLM API key (OpenAI-compatible) |
| `OPENAI_BASE_URL` | ✅ | LLM API base URL |
| `OPENAI_MODEL` | ✅ | Model name (e.g. `gpt-4o`) |
| `PSE_ROOT` | ✅ | Sandbox root for `read_file` / `run_bash` |
| `CRM_DB_PATH` | ✅ | Path to personal-crm's `crm.db` (read-only) |
| `PSE_MAX_RETRIES` | | Max verify/fix rounds (default: `3`) |
| `AGNES_KEY` | | Alternative: Agnes API key (free model) |
| `AGNES_BASE_URL` | | Alternative: Agnes base URL |

## Usage — crm-qa (data-quality watchdog)

```bash
# Deterministic scan only (zero cost, no API key needed)
make crm-qa
python tasks/crm-qa/run.py --db /path/to/crm.db

# Also generate a natural-language QA report via LLM (needs langgraph + key)
make crm-qa FLAGS=--llm
python tasks/crm-qa/run.py --llm
```

The watchdog scans `crm.db` for known data-quality issues (duplicate wechat_id, un-renamed names, missing clean fields, orphan chats, timezone mismatches, empty records, …) and, with `--llm`, writes a verified Chinese QA report whose numbers are guaranteed to match the scan.

## Building a new task

1. Create `tasks/<your-task>/prompts/{planner,specialist}.md`.
2. Call `build_graph(task="<your-task>", verify_fn=..., use_planner=...)`.
3. `verify_fn(state) -> (bad, ok)` is your deterministic check; the graph loops `fix` until it passes or hits `max_retries`.

## Security Notes

- **No hardcoded secrets.** All credentials are read from `.env`, which is gitignored.
- **Sandboxed tools.** `read_file` only reads under `PSE_ROOT`; `run_bash` blocks destructive commands (`rm -rf`, `dd`, `curl|sh`, …) and runs in `PSE_ROOT`.
- **Read-only DB access.** `query_crm` allows only single `SELECT` statements against `crm.db`; the QA scanner opens the DB in `mode=ro&immutable=1`.
- **No network-exposed service.** This project runs locally as a CLI.

## License

MIT
