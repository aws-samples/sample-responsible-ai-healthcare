# AWS Responsible AI Healthcare Demo — Makefile
# Run `make help` to see available targets.

.DEFAULT_GOAL := help

.PHONY: help install install-dev test test-coverage lint format format-check \
        security-scan ash typecheck validate clean deploy deploy-agent destroy destroy-agent full-cleanup \
        terraform-fmt terraform-validate verify-live scan-all

# ---------- Help --------------------------------------------------------------

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------- Installation ------------------------------------------------------

install: ## Install production dependencies
	pip install -r requirements.txt

install-dev: ## Install production + dev dependencies
	pip install -r requirements.txt
	pip install -e ".[dev]"
	pre-commit install

# ---------- Testing -----------------------------------------------------------

test: ## Run tests with verbose output
	pytest tests/ -v

test-coverage: ## Run tests with coverage report
	pytest --cov=. --cov-report=term-missing --cov-report=html

# ---------- Code Quality ------------------------------------------------------

lint: ## Run linter (ruff check)
	ruff check .

format: ## Format code in-place (ruff format)
	ruff format .

format-check: ## Check code formatting without modifying files
	ruff format --check .

security-scan: ## Run security scans (bandit + pip-audit)
	bandit -c pyproject.toml -r .
	pip-audit

ash: ## Run ASH (Automated Security Helper) scan
	ash --source-dir .

typecheck: ## Run static type checking (mypy)
	mypy config.py src/lambda/ tests/

# ---------- Validation (pre-push gate) ----------------------------------------

validate: lint format-check test security-scan ash ## Run full validation suite (lint + format + test + security + ASH)
	@echo "All checks passed."

# ---------- Full scan report --------------------------------------------------

SCAN_OUT := .scan-output
# checkov runs in its own pinned, isolated environment: it pins an older botocore than this
# project needs, and a broken install runs no CKV2_* graph checks (scripts/scan_summary.py fails then).
CHECKOV := uvx --from checkov==3.2.497 checkov
KICS_IMAGE := registry.gitlab.com/security-products/kics:6
CDK_TEMPLATE := src/agent/agentcore/cdk/cdk.out/AgentCore-raihealthdiag-default.template.json

# Each scanner writes raw JSON to $(SCAN_OUT); a scanner that is missing or fails
# leaves no file and scripts/scan_summary.py reports it as NOT RUN (a failure).
scan-all: ## Run every security scanner (ASH, checkov, bandit, semgrep, GitLab KICS, Trivy, cfn-nag) and write one report
	@rm -rf $(SCAN_OUT) && mkdir -p $(SCAN_OUT)
	-ash --source-dir . --output-dir $(SCAN_OUT)/ash > $(SCAN_OUT)/ash.log 2>&1
	-$(CHECKOV) -d infra --framework terraform -o json --compact > $(SCAN_OUT)/checkov.json 2> $(SCAN_OUT)/checkov.log
	-bandit -c pyproject.toml -r . -f json -q -o $(SCAN_OUT)/bandit.json
	-semgrep --metrics=off --quiet --json -o $(SCAN_OUT)/semgrep.json \
		--config r/terraform.lang.security.iam --config p/terraform --config p/python --config p/typescript
	-TREE=$$(mktemp -d) && git ls-files -co --exclude-standard | grep -v '^\.agents/' | tar -cf - -T - | tar -xf - -C $$TREE \
		&& docker run --rm -v $$TREE:/code -e CI_PROJECT_DIR=/code $(KICS_IMAGE) /analyzer run > $(SCAN_OUT)/kics.log 2>&1 \
		; cp $$TREE/gl-sast-report.json $(SCAN_OUT)/gl-kics.json \
		; trivy fs --scanners misconfig,secret,vuln --ignorefile .trivyignore --format json -o $(SCAN_OUT)/trivy.json $$TREE > $(SCAN_OUT)/trivy.log 2>&1 \
		; rm -rf $$TREE
	-test -f $(CDK_TEMPLATE) && date -u -r $(CDK_TEMPLATE) +"%Y-%m-%d %H:%M UTC" > $(SCAN_OUT)/cfn-nag-template-mtime \
		&& cfn_nag_scan --output-format json --input-path $(CDK_TEMPLATE) > $(SCAN_OUT)/cfn-nag.json
	python scripts/scan_summary.py

# ---------- Live verification -------------------------------------------------

verify-live: ## Verify the deployed stack end to end (needs AWS credentials, AGENTCORE_RUNTIME_ID, EXPECTED_AGENT_GUARDRAIL_ID)
	@# A refresh can transiently read the us-west-2 replica bucket as deleted; a real change persists, so
	@# re-plan once and fail only if both plans report changes. The first plan is kept for diagnosis.
	@terraform -chdir=infra plan -detailed-exitcode -input=false -lock=false -no-color > .verify-plan.log 2>&1 \
		|| { echo "first terraform plan reported changes or an error (see .verify-plan.log); re-planning once"; \
		     terraform -chdir=infra plan -detailed-exitcode -input=false -lock=false > /dev/null \
		     || { echo "terraform plan reports drift or an error on two consecutive runs"; exit 1; }; }
	python scripts/verify_solution.py

# ---------- Terraform ---------------------------------------------------------

deploy: ## Deploy infrastructure via Terraform
	cd infra && terraform init && terraform apply -auto-approve

destroy: ## Destroy Terraform-managed infrastructure
	cd infra && terraform destroy -auto-approve

deploy-agent: ## Deploy the AgentCore runtime (CDK stack; needs GUARDRAIL_*, AR_POLICY_*, KNOWLEDGE_BASE_ID)
	cd src/agent/agentcore/cdk && npm ci && npm run build && ./node_modules/.bin/cdk deploy

destroy-agent: ## Destroy the AgentCore runtime (CDK stack)
	cd src/agent/agentcore/cdk && npm run build && AGENTCORE_TEARDOWN=1 ./node_modules/.bin/cdk destroy --force

full-cleanup: ## Full teardown — blockers, agent runtime, infra, leftovers, verification (see docs/cleanup.md)
	python scripts/cleanup.py --phase pre-clean $(CLEANUP_FLAGS)
	$(MAKE) destroy-agent
	$(MAKE) destroy || { python scripts/cleanup.py --phase unblock --force; $(MAKE) destroy; }
	python scripts/cleanup.py --phase post-clean --force
	python scripts/cleanup.py --phase verify

terraform-fmt: ## Format Terraform files
	terraform -chdir=infra fmt

terraform-validate: ## Validate Terraform configuration
	terraform -chdir=infra validate

# ---------- Cleanup -----------------------------------------------------------

clean: ## Remove build artifacts, caches, and temporary files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	@echo "Clean complete."
