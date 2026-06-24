# PrntBtlr — common dev tasks. Run `make help` for the list.

VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
PORT ?= 8080

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

$(VENV): ## Create the virtualenv
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

.PHONY: install
install: $(VENV) ## Install dev dependencies
	$(PIP) install -r requirements-dev.txt
	$(PIP) install ruff

.PHONY: run
run: ## Run the panel locally (PORT=8080 by default)
	PRNTBTLR_PORT=$(PORT) PRNTBTLR_DEBUG=1 $(PY) -m app.main

.PHONY: test
test: ## Run the test suite
	$(PY) -m pytest

.PHONY: lint
lint: ## Lint with ruff
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

.PHONY: format
format: ## Auto-format with ruff
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

.PHONY: check
check: lint test ## Lint + test (what CI runs)

.PHONY: docker
docker: ## Build the Docker image
	docker build -f deploy/Dockerfile -t prntbtlr:dev .

.PHONY: clean
clean: ## Remove caches and build artifacts
	rm -rf $(VENV) .pytest_cache **/__pycache__ *.egg-info
