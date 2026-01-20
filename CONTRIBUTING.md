# Contributing to pre-commit-tidy

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing.

## Development Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/codefuturist/pre-commit-tidy.git
   cd pre-commit-tidy
   ```

1. **Create a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

1. **Install in development mode**

   ```bash
   pip install -e ".[dev]"
   ```

1. **Install pre-commit hooks**

   ```bash
   pre-commit install
   ```

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=tidy --cov-report=html

# Run specific test
pytest tests/test_tidy.py::test_should_exclude_by_filename
```

## Code Style

This project uses:

- **Ruff** for linting and formatting
- **mypy** for type checking

Run all checks:

```bash
pre-commit run --all-files
```

## Submitting Changes

1. Fork the repository
1. Create a feature branch: `git checkout -b feature/my-feature`
1. Make your changes
1. Run tests: `pytest`
1. Run linting: `pre-commit run --all-files`
1. Commit with conventional commits: `git commit -m "feat: add new feature"`
1. Push and create a Pull Request

## Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New features
- `fix:` Bug fixes
- `docs:` Documentation changes
- `test:` Test additions or modifications
- `refactor:` Code refactoring
- `chore:` Maintenance tasks

## Releasing

Releases are automated via GitHub Actions when a new tag is pushed:

```bash
git tag -a v1.1.0 -m "Release v1.1.0"
git push origin v1.1.0
```

This triggers the CI pipeline which publishes to PyPI.
