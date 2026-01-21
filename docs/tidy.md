# Tidy - File Organization Tool

Automated file organization for repositories. Move files from source directories to target directories based on configurable rules.

## CLI Options

```
Usage: tidy [options]

Options:
  --config PATH           Path to configuration file
  --source DIR            Source directory (default: .)
  --target DIR            Target directory (default: 00-inbox)
  --extensions EXT        Comma-separated extensions (default: .md)
  --exclude-dirs DIRS     Comma-separated directories to exclude
  --recursive, -r         Recursively scan source directory
  --max-depth N           Maximum recursion depth (default: unlimited)
  --dedup-by-content      Detect duplicates by file content hash
  --undo                  Undo the last tidy operation
  --dry-run               Preview changes without moving files
  --verbose, -v           Show detailed output
  --quiet, -q             Suppress all output except errors
  --version               Show version number
  --help                  Show help message
```

## Configuration

Create a `.tidyrc.yaml` file in your project root:

```yaml
source_dir: "."
target_dir: "00-inbox"
extensions:
  - ".md"
  - ".txt"
exclude_files:
  - "readme.md"
  - "changelog.md"
exclude_patterns:
  - "*.config.*"
exclude_dirs:
  - "node_modules"
  - ".git"
  - "__pycache__"
duplicate_strategy: "rename"
dedup_by_content: false
recursive: false
max_depth: null
rules:
  - pattern: "*.test.md"
    target: "tests/"
  - pattern: "*.draft.*"
    target: "drafts/"
  - extensions:
      - ".png"
      - ".jpg"
    target: "assets/images/"
  - glob: "docs/**/*.md"
    target: "documentation/"
```

## Rule-based Routing

Tidy supports three rule formats for routing files to different targets:

| Format | Example | Description |
|--------|---------|-------------|
| **Pattern** | `{"pattern": "*.test.md", "target": "tests/"}` | Glob pattern matching on filename |
| **Extensions** | `{"extensions": [".png", ".jpg"], "target": "images/"}` | Match by file extension |
| **Glob** | `{"glob": "docs/**/*.md", "target": "docs-archive/"}` | Full path glob matching |

Rules are evaluated in order — first match wins.

## Duplicate Handling

| Strategy | Description |
|----------|-------------|
| `rename` | Add timestamp suffix to avoid conflicts (default) |
| `skip` | Skip files that already exist in target |
| `overwrite` | Overwrite existing files |

With `--dedup-by-content`, files are compared by SHA-256 hash to detect true duplicates regardless of filename.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TIDY_SOURCE_DIR` | Source directory |
| `TIDY_TARGET_DIR` | Target directory |
| `TIDY_EXTENSIONS` | Comma-separated extensions |
| `TIDY_EXCLUDE_FILES` | Comma-separated files to exclude |
| `TIDY_EXCLUDE_DIRS` | Comma-separated directories to exclude |
| `TIDY_DRY_RUN` | Set to `true` for dry run |
| `TIDY_VERBOSE` | Set to `true` for verbose output |
| `TIDY_RECURSIVE` | Set to `true` for recursive scanning |
| `TIDY_DEDUP_BY_CONTENT` | Set to `true` for content-based dedup |

## Undo Support

Tidy automatically creates a `.tidy-undo.json` manifest after each operation (overwrites previous). Use `tidy --undo` to restore files to their original locations.

## Pre-commit Hook

```yaml
# Basic - organize files to inbox
- id: tidy
  name: Tidy files
  entry: tidy
  language: system
  pass_filenames: false

# With options
- id: tidy
  name: Tidy files
  entry: tidy --recursive --dedup-by-content
  language: system
  pass_filenames: false
  args: ['--source', '.', '--target', '00-inbox', '--extensions', '.md,.txt']
```
