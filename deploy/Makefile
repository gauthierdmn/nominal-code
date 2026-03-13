NAMESPACE      := nominal-code
REGISTRY       := ghcr.io/gauthierdmn
IMAGE_NAME     := nominal-code
IMAGE_TAG      ?= latest
IMAGE_REF       = $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
WEBHOOK_SECRET ?= test-secret
TIMEOUT        ?= 120
CONFIG_PATH    := $(abspath local/config.yaml)
APP_DIR        := $(abspath ../app)

serve: _check-serve-env ## Start the webhook server locally (no k8s)
	cd $(APP_DIR) && CONFIG_PATH=$(CONFIG_PATH) uv run nominal-code serve

deploy: ## Deploy to Kubernetes (pulls latest image from GHCR)
	@test -f k8s/secret.yaml || \
		{ echo "ERROR: k8s/secret.yaml not found."; \
		  echo "  cp k8s/secret.yaml.template k8s/secret.yaml"; \
		  echo "  Then fill in your real values."; exit 1; }
	kubectl apply -k k8s
	kubectl -n $(NAMESPACE) set image deployment/nominal-code-server server=$(IMAGE_REF)
	kubectl -n $(NAMESPACE) rollout restart deployment/nominal-code-server
	kubectl -n $(NAMESPACE) rollout status  deployment/nominal-code-server --timeout=$(TIMEOUT)s

deploy-ci: _ci-secret ## Deploy to Kubernetes for CI (IMAGE_TAG=sha to pin)
	kubectl apply -k ci
	kubectl -n $(NAMESPACE) set image deployment/nominal-code-server server=$(IMAGE_REF)
	kubectl -n $(NAMESPACE) rollout restart deployment/nominal-code-server
	kubectl -n $(NAMESPACE) rollout status  deployment/nominal-code-server --timeout=$(TIMEOUT)s

build: ## Build the container image
	docker build -f ../ci/Dockerfile -t $(IMAGE_REF) ..

teardown: ## Delete the k8s namespace and all resources
	kubectl delete namespace $(NAMESPACE) --ignore-not-found

logs: ## Tail server logs (k8s)
	kubectl -n $(NAMESPACE) logs -f -l app.kubernetes.io/component=server

status: ## Show pods, jobs, and services (k8s)
	kubectl -n $(NAMESPACE) get pods,jobs,svc

port-forward: ## Forward localhost:8080 to the k8s server service
	kubectl -n $(NAMESPACE) port-forward svc/nominal-code-server 8080:80

_check-serve-env:
	@test -f $(CONFIG_PATH) || { echo "ERROR: $(CONFIG_PATH) not found"; exit 1; }
	@test -n "$${ALLOWED_USERS}" || { echo "ERROR: ALLOWED_USERS is required"; exit 1; }
	@test -n "$${GOOGLE_API_KEY}" || test -n "$${ANTHROPIC_API_KEY}" || \
		{ echo "ERROR: GOOGLE_API_KEY or ANTHROPIC_API_KEY is required"; exit 1; }
	@( test -n "$${GITHUB_TOKEN}" || ( test -n "$${GITHUB_APP_ID}" && test -n "$${GITHUB_APP_PRIVATE_KEY}" ) ) || \
		{ echo "ERROR: GITHUB_TOKEN or GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY is required"; exit 1; }

_ci-secret:
	@test -n "$${TEST_GITHUB_TOKEN}" || { echo "ERROR: TEST_GITHUB_TOKEN is required"; exit 1; }
	@test -n "$${GOOGLE_API_KEY}"    || { echo "ERROR: GOOGLE_API_KEY is required";    exit 1; }
	kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	kubectl create secret generic nominal-code-secrets \
		--namespace=$(NAMESPACE) \
		--from-literal=GITHUB_TOKEN=$${TEST_GITHUB_TOKEN} \
		--from-literal=GITHUB_WEBHOOK_SECRET=$(WEBHOOK_SECRET) \
		--from-literal=GOOGLE_API_KEY=$${GOOGLE_API_KEY} \
		--from-literal=K8S_IMAGE=$(IMAGE_REF) \
		--dry-run=client -o yaml | kubectl apply -f -

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: serve build deploy deploy-ci teardown logs status port-forward help
