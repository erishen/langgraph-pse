.PHONY: install lint clean crm-qa crm-qa-scan crm-qa-report crm-qa-agnes weekly-review weekly-review-report weekly-review-agnes help

PY := uv run python
TASK := tasks/crm-qa/run.py
WEEKLY := tasks/weekly-review/run.py

install: ## 安装依赖（uv sync）
	uv sync

lint: ## 代码检查
	uv run ruff check src/ tasks/

clean: ## 清理缓存/构建产物
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .langgraph/ dist/ *.egg-info

# 数据质量看门狗：确定性只读扫描（零成本，不改库）
# 用法: make crm-qa [DB=/path/to/crm.db] [FLAGS=...]
crm-qa: ## 只读扫描 crm.db 并打印 findings
	$(PY) $(TASK) $(if $(DB),--db $(DB),) $(FLAGS)

crm-qa-scan: ## 显式只读扫描（等价于 crm-qa）
	$(PY) $(TASK) $(if $(DB),--db $(DB),) $(FLAGS)

# 生成自然语言 QA 报告（LLM）
# 用法: make crm-qa-report [DB=...] [FLAGS=...]   —— deepseek 默认
#       make crm-qa-agnes   [DB=...] [FLAGS=...]   —— agnes 网关
crm-qa-report: ## LLM 自然语言报告（deepseek）
	$(PY) $(TASK) --llm --provider deepseek $(if $(DB),--db $(DB),) $(FLAGS)

crm-qa-agnes: ## LLM 自然语言报告（agnes）
	$(PY) $(TASK) --llm --provider agnes $(if $(DB),--db $(DB),) $(FLAGS)

# 每周关系复盘：确定性只读聚合（零成本，不改库）—— 验证通用核心的第二个任务
# 用法: make weekly-review [DB=/path/to/crm.db] [FLAGS=...]
weekly-review: ## 只读聚合每周关系复盘并打印
	$(PY) $(WEEKLY) $(if $(DB),--db $(DB),) $(FLAGS)

# 生成自然语言复盘（LLM）
# 用法: make weekly-review-report [DB=...] [FLAGS=...]   —— deepseek 默认
#       make weekly-review-agnes   [DB=...] [FLAGS=...]   —— agnes 网关
weekly-review-report: ## LLM 自然语言复盘（deepseek）
	$(PY) $(WEEKLY) --llm --provider deepseek $(if $(DB),--db $(DB),) $(FLAGS)

weekly-review-agnes: ## LLM 自然语言复盘（agnes）
	$(PY) $(WEEKLY) --llm --provider agnes $(if $(DB),--db $(DB),) $(FLAGS)

help: ## 列出全部命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
