# tradechat-sre-demo — entry points. Targets are added step by step.
.DEFAULT_GOAL := help
.PHONY: help tools up down load cluster images

TOOLS := docker kind kubectl helm python3.12 git uv

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

tools: ## Check that every required tool is installed
	@for t in $(TOOLS); do \
		if command -v $$t >/dev/null; then echo "ok       $$t"; \
		else echo "MISSING  $$t  (brew install $$t)"; exit 1; fi; \
	done
	@docker info >/dev/null 2>&1 && echo "ok       docker daemon running" || (echo "MISSING  docker daemon (start Docker Desktop)"; exit 1)

up: ## Build images and start redis, chat-api, archiver (docker compose)
	docker compose up --build -d
	docker compose ps

down: ## Stop the stack (volumes kept: the archive must survive routine commands)
	docker compose down

load: ## Run simulated traders against localhost:8000 (e.g. make load ARGS="--duration 30")
	uv run loadgen/loadgen.py $(ARGS)

CLUSTER := tradechat

cluster: ## Create the kind cluster (1 control-plane + 2 workers)
	kind create cluster --name $(CLUSTER) --config deploy/kind-config.yaml

images: ## Build the app images and load them into the kind nodes (no registry needed)
	docker compose build
	kind load docker-image tradechat/chat-api:1.0.1 tradechat/archiver:1.0.1 --name $(CLUSTER)
