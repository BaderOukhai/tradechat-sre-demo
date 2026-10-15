# tradechat-sre-demo — entry points. Targets are added step by step.
.DEFAULT_GOAL := help
.PHONY: help tools

TOOLS := docker kind kubectl helm python3.12 git uv

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

tools: ## Check that every required tool is installed
	@for t in $(TOOLS); do \
		if command -v $$t >/dev/null; then echo "ok       $$t"; \
		else echo "MISSING  $$t  (brew install $$t)"; exit 1; fi; \
	done
	@docker info >/dev/null 2>&1 && echo "ok       docker daemon running" || (echo "MISSING  docker daemon (start Docker Desktop)"; exit 1)
