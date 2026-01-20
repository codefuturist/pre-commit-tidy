# Adding New Hooks

This guide explains how to add new pre-commit hooks to this repository.

## Quick Start

1. **Create the hook file** in `pre_commit_hooks/`:
   ```bash
   touch pre_commit_hooks/my_new_hook.py
   ```

2. **Implement the hook** (see template below)

3. **Register in `.pre-commit-hooks.yaml`**

4. **Add console script** in `pyproject.toml`

5. **Write tests** in `tests/my_new_hook_test.py`

6. **Update README.md** with hook documentation

## Hook Template

```python
"""My new hook description."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the hook."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "filenames",
        nargs="*",
        help="Filenames to check",
    )
    parser.add_argument(
        "--flag",
        action="store_true",
        help="Optional flag",
    )
    args = parser.parse_args(argv)

    retval = 0
    for filename in args.filenames:
        # Your hook logic here
        pass

    return retval


if __name__ == "__main__":
    raise SystemExit(main())
```

## Register Hook in `.pre-commit-hooks.yaml`

Add an entry:

```yaml
- id: my-new-hook
  name: My New Hook
  description: Description of what this hook does
  entry: my-new-hook
  language: python
  types: [python]  # or [text], [file], etc.
```

## Add Console Script in `pyproject.toml`

In the `[project.scripts]` section:

```toml
[project.scripts]
tidy = "pre_commit_hooks.tidy:main"
my-new-hook = "pre_commit_hooks.my_new_hook:main"
```

## Test Template

```python
"""Tests for my_new_hook."""

from __future__ import annotations

import pytest

from pre_commit_hooks.my_new_hook import main


def test_my_new_hook():
    """Test basic functionality."""
    assert main([]) == 0
```

## Hook Types Reference

Common `types` values:
- `[python]` - Python files
- `[text]` - Text files
- `[file]` - All files
- `[yaml]` - YAML files
- `[json]` - JSON files
- `[markdown]` - Markdown files

Common `language` values:
- `python` - Python hooks (most common)
- `system` - System commands
- `script` - Shell scripts

## Hook Configuration Options

```yaml
- id: my-hook
  name: My Hook Name
  description: What it does
  entry: my-hook
  language: python
  types: [python]
  # Optional settings:
  files: ''                    # Regex pattern for files
  exclude: ''                  # Regex pattern to exclude
  pass_filenames: true         # Pass filenames as args
  always_run: false            # Run even if no files
  require_serial: false        # Run in serial, not parallel
  minimum_pre_commit_version: "2.9.0"
```

## Testing Your Hook

```bash
# Install in editable mode
pip install -e ".[dev]"

# Run tests
pytest tests/my_new_hook_test.py

# Test with pre-commit
pre-commit try-repo . my-new-hook --verbose --all-files
```

## Current Hooks

### tidy
- **File**: `pre_commit_hooks/tidy.py`
- **Purpose**: Automated file organization
- **Entry point**: `tidy`
- **Type**: Runs on all files

## Best Practices

1. **Use `from __future__ import annotations`** for forward compatibility
2. **Return 0 for success, non-zero for failure**
3. **Write comprehensive tests**
4. **Document in README.md**
5. **Follow the project's code style** (ruff, mypy)
6. **Add type hints** for all function signatures
7. **Keep hooks focused** - one responsibility per hook
