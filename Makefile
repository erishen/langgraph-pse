.PHONY: install lint clean crm-qa crm-qa-scan crm-qa-report crm-qa-agnes weekly-review weekly-review-report weekly-review-agnes follow-up-draft follow-up-draft-report follow-up-draft-agnes interview-questions interview-questions-report interview-questions-agnes help

PY := uv run python
TASK := tasks/crm-qa/run.py
WEEKLY := tasks/weekly-review/run.py
DRAFT := tasks/follow-up-draft/run.py
IQ := tasks/interview-questions/run.py

install: ## 安装依赖（uv sync）
	uv sync

lint: ## 代码检查
	uv run ruff check src/ tasks/

clean: ## 清理缓存/构建产物
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .langgraph/ dist/ *.egg-info

# 数据质量看门狗：调用 personal-crm 后端 GET /api/qa/report（单源，零成本，不改库）
# 用法: make crm-qa [API=http://127.0.0.1:8000] [FLAGS=...]
crm-qa: ## 调用 API 拉取并打印质量报告 findings
	$(PY) $(TASK) $(if $(API),--api-base-url $(API),) $(FLAGS)

crm-qa-scan: ## 显式调用（等价于 crm-qa）
	$(PY) $(TASK) $(if $(API),--api-base-url $(API),) $(FLAGS)

# 生成自然语言 QA 报告（LLM）
# 用法: make crm-qa-report [API=...] [FLAGS=...]   —— deepseek 默认
#       make crm-qa-agnes   [API=...] [FLAGS=...]   —— agnes 网关
crm-qa-report: ## LLM 自然语言报告（deepseek）
	$(PY) $(TASK) --llm --provider deepseek $(if $(API),--api-base-url $(API),) $(FLAGS)

crm-qa-agnes: ## LLM 自然语言报告（agnes）
	$(PY) $(TASK) --llm --provider agnes $(if $(API),--api-base-url $(API),) $(FLAGS)

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

# 跟进消息草拟：确定性只读聚合候选人+真实上下文（零成本，不改库）—— 验证通用核心的第三个任务
# 用法: make follow-up-draft [DB=/path/to/crm.db] [FLAGS=...]
follow-up-draft: ## 只读聚合待跟进候选人与上下文并打印
	$(PY) $(DRAFT) $(if $(DB),--db $(DB),) $(FLAGS)

# 生成个性化跟进草稿（LLM）
# 用法: make follow-up-draft-report [DB=...] [FLAGS=...]   —— deepseek 默认
#       make follow-up-draft-agnes   [DB=...] [FLAGS=...]   —— agnes 网关
follow-up-draft-report: ## LLM 个性化跟进草稿（deepseek）
	$(PY) $(DRAFT) --llm --provider deepseek $(if $(DB),--db $(DB),) $(FLAGS)

follow-up-draft-agnes: ## LLM 个性化跟进草稿（agnes）
	$(PY) $(DRAFT) --llm --provider agnes $(if $(DB),--db $(DB),) $(FLAGS)

# 面试题库生成：支持三种出题来源（编程语言/岗位、JD 文档、候选人简历）
# 用法（零成本，打印规格）:
#   make interview-questions                              # 默认 python 规格
#   make interview-questions SUBJECT=react-python         # 指定内置对象
#   make interview-questions JD=work/docs/jobs/jd/kpmg.md          # 按 JD 出题
#   make interview-questions RESUME=work/docs/resume-pdf/zh-boss.pdf  # 按简历出题（只读 .md）
# 生成（LLM）:
#   make interview-questions-report [SUBJECT=|JD=|RESUME=] [PROVIDER=deepseek] [FLAGS=...]
#   make interview-questions-agnes  [SUBJECT=|JD=|RESUME=]
interview-questions: ## 打印面试题库规格（编程语言/岗位/JD/简历）
	$(PY) $(IQ) $(if $(SUBJECT),--subject $(SUBJECT),) $(if $(JD),--jd $(JD),) $(if $(RESUME),--resume $(RESUME),) $(FLAGS)

interview-questions-report: ## LLM 生成面试题库（deepseek）
	$(PY) $(IQ) --llm --provider deepseek $(if $(SUBJECT),--subject $(SUBJECT),) $(if $(JD),--jd $(JD),) $(if $(RESUME),--resume $(RESUME),) $(FLAGS)

interview-questions-agnes: ## LLM 生成面试题库（agnes）
	$(PY) $(IQ) --llm --provider agnes $(if $(SUBJECT),--subject $(SUBJECT),) $(if $(JD),--jd $(JD),) $(if $(RESUME),--resume $(RESUME),) $(FLAGS)

help: ## 列出全部命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
