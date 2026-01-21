# Copilot Instructions for pre-commit-tidy

## Project Overview

This is a fork/extension of [pre-commit](https://pre-commit.com/) that adds three standalone CLI tools for developer workflow automation:

| Tool | Config File | Purpose |
|------|-------------|---------|
| `tidy` | `.tidyrc.json` | Automated file organization with rule-based routing |
| `binary-track` | `.binariesrc.json` | Track locally-built binaries and detect staleness |
| `remote-sync` | `.remotesyncrc.json` | Keep multiple git remotes in sync |

## Architecture

### Core Modules (in `pre_commit/`)

Each tool follows the same architectural pattern:

1. **Self-contained module** - All logic in a single file (`tidy.py`, `binary_track.py`, `remote_sync.py`)
2. **Configuration hierarchy**: CLI args → Environment variables → Config file → Defaults
3. **Typed data structures** using `@dataclass` for runtime objects and `TypedDict` for JSON config schemas
4. **Entry points** defined in `setup.cfg` under `[options.entry_points]`

```python
# Pattern for config classes (see tidy.py:279, binary_track.py:168)
@dataclass
class ToolConfig:
    @classmethod
    def from_dict(cls, data: ConfigDict) -> ToolConfig:
        # Load from JSON config with defaults
```

### Language Support (`pre_commit/languages/`)

The original pre-commit language modules for running hooks in different language environments. Each module implements `run_hook`, `install_environment`, and language-specific setup.

## Development Workflow

### Setup
```bash
# Option 1: Using tox (recommended for testing)
tox --devenv venv && source venv/bin/activate

# Option 2: Using uv (fastest)
uv venv && source .venv/bin/activate
uv pip install -e . -r requirements-dev.txt

# Option 3: Using pip
pip install -e . && pip install -r requirements-dev.txt
```

### Testing
```bash
# Run tests (excludes slow language integration tests)
pytest tests

# Run specific test
pytest tests -k test_name

# Full suite with coverage
tox -e py
```

**Key testing patterns:**
- All tests run in `tmp_path` via `conftest.py` autouse fixture
- Test classes grouped by function (`TestShouldExclude`, `TestLoadConfigFile`)
- Mock external commands with `unittest.mock.patch`

### Type Checking & Linting
```bash
# Pre-commit hooks (mypy, ruff, etc.)
pre-commit run --all-files
```

The project uses strict mypy settings (see `setup.cfg [mypy]`) - all public functions require type annotations.

## Code Conventions

### Adding a New CLI Tool

1. Create `pre_commit/newtool.py` following the pattern:
   - Docstring with Usage/Options/Configuration sections
   - Enums for status/strategy options
   - `TypedDict` for JSON config schema
   - `@dataclass` for runtime config with `from_dict()` classmethod
   - `main()` function as entry point
2. Add entry point in `setup.cfg` under `[options.entry_points] console_scripts`
3. Add tests in `tests/test_newtool.py`

### Error Handling

Tools return exit codes: 0 (success), 1 (error/stale found), 2 (configuration error)

### Output Formatting

Use the `Logger` and `Colors` classes for consistent terminal output:
```python
logger = Logger(verbosity=1, dry_run=False)
logger.success("Operation completed")  # ✓ green
logger.warn("Check this")              # ⚠ yellow
logger.error("Failed")                 # ✗ red
```

## Testing Tips

- Tests auto-change to `tmp_path` - create test files/configs there
- Use `monkeypatch.setenv()` for environment variable tests
- Mock git commands when testing `binary_track` or `remote_sync`
- Check `tests/test_tidy.py` for comprehensive examples of testing config loading and file operations
