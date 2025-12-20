.PHONY: typecheck lint format check test all

# Type checking with mypy
typecheck:
	mypy --explicit-package-bases forge/ main.py

# Linting with ruff (auto-fix)
lint:
	ruff check --fix forge/ main.py

# Linting without auto-fix (for CI)
lint-check:
	ruff check forge/ main.py

# Format code with ruff
format:
	ruff format forge/ main.py

# Run all checks (with auto-fix)
check: typecheck lint

# Run all checks without auto-fix (for CI)
check-ci: typecheck lint-check

# Run all checks and tests
all: check

# Help
help:
	@echo "Available targets:"
	@echo "  typecheck  - Run mypy type checker"
	@echo "  lint       - Run ruff linter with auto-fix"
	@echo "  lint-check - Run ruff linter without auto-fix (for CI)"
	@echo "  format     - Format code with ruff"
	@echo "  check      - Run typecheck and lint (with auto-fix)"
	@echo "  check-ci   - Run typecheck and lint-check (no auto-fix)"
	@echo "  all        - Run all checks"
