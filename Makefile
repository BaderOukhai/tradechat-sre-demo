# tradechat-sre-demo — entry points.
# Full local environment: make cluster images monitoring deploy, then make load.
.DEFAULT_GOAL := help
.PHONY: help tools up down load cluster cluster-delete images monitoring deploy urls bad-release rollback incident gitops

TOOLS := docker kind kubectl helm python3.12 git uv
CLUSTER := tradechat
# kube-prometheus-stack chart version, pinned for reproducibility
KPS_VERSION := 92.13.1
ARGOCD_VERSION := 10.11.1

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

tools: ## Check that every required tool is installed
	@for t in $(TOOLS); do \
		if command -v $$t >/dev/null; then echo "ok       $$t"; \
		else echo "MISSING  $$t  (brew install $$t)"; exit 1; fi; \
	done
	@docker info >/dev/null 2>&1 && echo "ok       docker daemon running" || (echo "MISSING  docker daemon (start Docker Desktop)"; exit 1)

# --- Local dev loop (docker compose) ---------------------------------------------------------

up: ## Build images and start redis, chat-api, archiver (docker compose)
	docker compose up --build -d
	docker compose ps

down: ## Stop the compose stack (volumes kept: the archive must survive routine commands)
	docker compose down

# --- Kubernetes (kind) -----------------------------------------------------------------------

cluster: ## Create the kind cluster (1 control-plane + 2 workers, ports 8000/3000/9090/9093)
	kind create cluster --name $(CLUSTER) --config deploy/kind-config.yaml

cluster-delete: ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER)

images: ## Build the app images and load them into the kind nodes (no registry needed)
	docker compose build
	kind load docker-image tradechat/chat-api:1.0.1 tradechat/archiver:1.0.1 --name $(CLUSTER)

monitoring: ## Install Prometheus, Alertmanager and Grafana (kube-prometheus-stack)
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
	helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
		--version $(KPS_VERSION) --namespace monitoring --create-namespace \
		-f deploy/monitoring/values.yaml --wait --timeout 5m

deploy: ## Install or upgrade the tradechat Helm release, wait until pods are ready
	helm upgrade --install tradechat deploy/helm/tradechat --wait --timeout 3m

urls: ## Print the local URLs and the Grafana admin password
	@echo "chat-api      http://localhost:8000"
	@echo "Grafana       http://localhost:3000/d/messaging-slo   (anonymous read-only)"
	@echo "Prometheus    http://localhost:9090"
	@echo "Alertmanager  http://localhost:9093"
	@printf "Grafana admin password: "; kubectl -n monitoring get secret monitoring-grafana \
		-o jsonpath='{.data.admin-password}' | base64 -d; echo

# --- Demo ------------------------------------------------------------------------------------

load: ## Run simulated traders against localhost:8000 (e.g. make load ARGS="--duration 30")
	uv run loadgen/loadgen.py $(ARGS)

bad-release: ## Deploy the simulated bad release v1.1 (+300 ms per message)
	helm upgrade tradechat deploy/helm/tradechat -f deploy/helm/values-bad-release.yaml --wait --timeout 3m

rollback: ## Roll the tradechat release back to the previous revision
	helm rollback tradechat --wait --timeout 3m

incident: ## Full incident drill with a timeline: bad release -> alert -> rollback -> recovery
	./scripts/incident.sh

# --- GitOps (optional) -----------------------------------------------------------------------

gitops: ## Hand the tradechat release over to Argo CD (needs the repo to be public; PVCs are kept)
	helm repo add argo https://argoproj.github.io/argo-helm --force-update
	helm upgrade --install argocd argo/argo-cd --version $(ARGOCD_VERSION) \
		--namespace argocd --create-namespace -f deploy/argocd/values.yaml --wait --timeout 5m
	helm uninstall tradechat --ignore-not-found
	kubectl apply -f deploy/argocd/application.yaml
