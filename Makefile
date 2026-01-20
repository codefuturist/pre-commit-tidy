.PHONY: install dev test lint format type-check pre-commit clean build publish

# Install production dependencies
install:
	pip install -e .

# Install development dependencies
dev:
	pip install -e ".[dev]"
	pre-commit install --hook-type commit-msg --hook-type pre-commit

# Run tests
test:
	pytest tests/ -v

# Run tests with coverage
coverage:
	pytest tests/ -v --cov=pre_commit_hooks --cov-report=term-missing --cov-report=html

# Run linting
lint:
	ruff check pre_commit_hooks/ tests/

# Format code
format:
	ruff format pre_commit_hooks/ tests/
	ruff check --fix pre_commit_hooks/ tests/

# Run type checking
type-check:
	mypy pre_commit_hooks/

# Run all pre-commit hooks
pre-commit:
	pre-commit run --all-files

# Run tox for multi-version testing
tox:
	tox

# Clean build artifacts
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .tox/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Build package
build: clean
	python -m build

# Publish to PyPI (requires authentication)
publish: build
	twine upload dist/*

# Version bump (patch)
bump-patch:
	cz bump --increment PATCH

# Version bump (minor)
bump-minor:
	cz bump --increment MINOR

# Version bump (major)
bump-major:
	cz bump --increment MAJOR

# Show current version
version:
	python -c "from pre_commit_hooks import __version__; print(__version__)"

# Full CI check (what CI runs)
ci: lint type-check test

# Help
help:
	@echo "Available targets:"
	@echo "  install     - Install production dependencies"
	@echo "  dev         - Install development dependencies and pre-commit hooks"
	@echo "  test        - Run tests"
	@echo "  coverage    - Run tests with coverage report"
	@echo "  lint        - Run linting"
	@echo "  format      - Format code"
	@echo "  type-check  - Run type checking"
	@echo "  pre-commit  - Run all pre-commit hooks"
	@echo "  tox         - Run tox for multi-version testing"
	@echo "  clean       - Clean build artifacts"
	@echo "  build       - Build package"
	@echo "  publish     - Publish to PyPI"
	@echo "  bump-patch  - Bump patch version"
	@echo "  bump-minor  - Bump minor version"
	@echo "  bump-major  - Bump major version"
	@echo "  version     - Show current version"
	@echo "  ci          - Full CI check"
