.PHONY: ci dist format lint pre-commit test typing

format: ## format Python source and tests
	uv run --group lint isort src tests
	uv run --group lint black --workers 1 --no-cache src tests

lint: ## check imports, Ruff, Flake8, and Black
	uv run --group lint isort --check --diff src tests
	uv run --group lint ruff check src tests
	uv run --group lint flake8 src tests
	uv run --group lint black --workers 1 --no-cache --check src tests

typing: ## type-check source and tests
	uv run --group typing mypy

test: ## run the unit test suite
	uv run --group test pytest

dist: ## build and validate the wheel and source distribution
	uv build
	uv run --group build twine check dist/*

pre-commit: ## install the repository's pre-commit hook
	uv run --group lint pre-commit install

ci: lint typing test dist ## run the same checks as CI
