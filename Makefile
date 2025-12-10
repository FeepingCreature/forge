.PHONY: typecheck lint format check test all

# Type checking with mypy
typecheck:
	mypy src/ main.py tools/

# Linting with ruff
lint:
	ruff check src/ main.py tools/

# Format code with ruff
format:
	ruff format src/ main.py tools/

# Run all checks
check: typecheck lint

# Run all checks and tests
all: check

# Help
help:
	@echo "Available targets:"
	@echo "  typecheck - Run mypy type checker"
	@echo "  lint      - Run ruff linter"
	@echo "  format    - Format code with ruff"
	@echo "  check     - Run typecheck and lint"
	@echo "  all       - Run all checks"
