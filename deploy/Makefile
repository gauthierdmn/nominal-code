NAMESPACE      := nominal-code
IMAGE          := nominal-code
WEBHOOK_SECRET ?= test-secret
TIMEOUT        ?= 120

build: ## Build the image into minikube's Docker daemon
	eval $$(minikube docker-env) && \
	docker buildx build -f ../ci/Dockerfile -t $(IMAGE) --load ..

deploy: build ## Build + deploy the local overlay (fill in overlays/local/secret.yaml first)
	@test -f overlays/local/secret.yaml || \
		{ echo "ERROR: overlays/local/secret.yaml not found."; \
		  echo "  cp overlays/local/secret.yaml.template overlays/local/secret.yaml"; \
		  echo "  Then fill in your real values."; exit 1; }
	kubectl apply -k overlays/local
	kubectl -n $(NAMESPACE) rollout restart deployment/nominal-code-server
	kubectl -n $(NAMESPACE) rollout status  deployment/nominal-code-server --timeout=$(TIMEOUT)s

deploy-ci: build _ci-secret ## Build + deploy the CI overlay with secrets from env vars
	kubectl apply -k overlays/ci
	kubectl -n $(NAMESPACE) rollout restart deployment/nominal-code-server
	kubectl -n $(NAMESPACE) rollout status  deployment/nominal-code-server --timeout=$(TIMEOUT)s

teardown: ## Delete the namespace and all resources
	kubectl delete namespace $(NAMESPACE) --ignore-not-found

logs: ## Tail server logs
	kubectl -n $(NAMESPACE) logs -f -l app.kubernetes.io/component=server

status: ## Show pods, jobs, and services
	kubectl -n $(NAMESPACE) get pods,jobs,svc

port-forward: ## Forward localhost:8080 to the server service
	kubectl -n $(NAMESPACE) port-forward svc/nominal-code-server 8080:80

_ci-secret:
	@test -n "$${TEST_GITHUB_TOKEN}" || { echo "ERROR: TEST_GITHUB_TOKEN is required"; exit 1; }
	@test -n "$${GOOGLE_API_KEY}"    || { echo "ERROR: GOOGLE_API_KEY is required";    exit 1; }
	kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	kubectl create secret generic nominal-code-secrets \
		--namespace=$(NAMESPACE) \
		--from-literal=GITHUB_TOKEN=$${TEST_GITHUB_TOKEN} \
		--from-literal=GITHUB_WEBHOOK_SECRET=$(WEBHOOK_SECRET) \
		--from-literal=GOOGLE_API_KEY=$${GOOGLE_API_KEY} \
		--from-literal=AGENT_PROVIDER=google \
		--from-literal=AGENT_MODEL=gemini-2.5-flash-lite \
		--from-literal=AGENT_MAX_TURNS=1 \
		--dry-run=client -o yaml | kubectl apply -f -

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: build deploy deploy-ci teardown logs status port-forward help
