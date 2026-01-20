# pre-commit-tidy

[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A pre-commit hook for automated file organization. Move files from source directories to target directories based on configurable rules.

## Features

- 📁 **Flexible file movement** - Move files between directories based on extension filters
- 🔧 **Highly configurable** - JSON config file, environment variables, or CLI arguments
- 🎯 **Smart exclusions** - Exclude specific files or patterns from processing
- 📝 **Duplicate handling** - Rename, skip, or overwrite duplicate files
- 🏃 **Dry-run mode** - Preview changes before applying them
- 🎨 **Colorful output** - Clear, readable terminal output

## Installation

Add to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/codefuturist/pre-commit-tidy
    rev: v1.0.0
    hooks:
      - id: tidy
```

Then install:

```bash
pre-commit install
```

## Configuration

Create a `.tidyrc.json` file in your repository root:

```json
{
  "source_dir": ".",
  "target_dir": "00-inbox",
  "extensions": [".md", ".txt"],
  "exclude_files": ["readme.md", "changelog.md", "license.md"],
  "exclude_patterns": ["*.config.*"],
  "duplicate_strategy": "rename"
}
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `source_dir` | string | `.` | Source directory to scan for files |
| `target_dir` | string | `00-inbox` | Target directory to move files to |
| `extensions` | array | `[".md"]` | File extensions to process |
| `exclude_files` | array | `["readme.md", ...]` | Filenames to exclude (case-insensitive) |
| `exclude_patterns` | array | `[]` | Glob patterns to exclude |
| `duplicate_strategy` | string | `rename` | How to handle duplicates: `rename`, `skip`, or `overwrite` |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `TIDY_SOURCE_DIR` | Source directory |
| `TIDY_TARGET_DIR` | Target directory |
| `TIDY_EXTENSIONS` | Comma-separated extensions |
| `TIDY_EXCLUDE_FILES` | Comma-separated files to exclude |
| `TIDY_DRY_RUN` | Set to `true` for dry run |
| `TIDY_VERBOSE` | Set to `true` for verbose output |

### CLI Arguments

Pass arguments through pre-commit:

```yaml
hooks:
  - id: tidy
    args: [--source, drafts, --target, published, --verbose]
```

Or run directly:

```bash
tidy --dry-run --verbose
tidy --source drafts --target published
tidy --extensions .md,.txt,.rst
```

## Use Cases

### Knowledge Base Inbox

Automatically move new markdown files to an inbox folder:

```json
{
  "source_dir": ".",
  "target_dir": "00-inbox",
  "extensions": [".md"],
  "exclude_files": ["readme.md", "changelog.md"]
}
```

### Draft Publishing

Move completed drafts to a published folder:

```json
{
  "source_dir": "drafts",
  "target_dir": "published",
  "extensions": [".md", ".html"],
  "duplicate_strategy": "skip"
}
```

### Asset Organization

Organize downloaded assets:

```json
{
  "source_dir": "downloads",
  "target_dir": "assets/images",
  "extensions": [".png", ".jpg", ".gif", ".svg"],
  "duplicate_strategy": "rename"
}
```

## Development

```bash
# Clone the repository
git clone https://github.com/codefuturist/pre-commit-tidy.git
cd pre-commit-tidy

# Install in development mode
pip install -e .

# Run tests
python -m pytest

# Run linter
ruff check .
```

## License

MIT License - see [LICENSE](LICENSE) for details.
