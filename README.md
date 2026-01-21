# pre-commit-tidy

Developer workflow tools for pre-commit hooks.

| Tool | Description |
|------|-------------|
| [**ai-fix**](docs/ai-fix.md) | AI-powered linting auto-fixer |
| [**tidy**](docs/tidy.md) | File organization with rule-based routing |
| [**remote-sync**](docs/remote-sync.md) | Multi-remote git synchronization |

## Install

```bash
pip install pre-commit-tidy
```

## Quick Start

### AI Fix - Auto-fix linting errors with AI

```bash
ai-fix --check              # Check for errors
ai-fix --fix                # Fix with confirmation
ai-fix --fix --auto         # Fix everything
ai-fix --list-models        # Show available models
```

Supports: GitHub Copilot CLI, Mistral Vibe, Ollama

### Tidy - Organize files

```bash
tidy --dry-run              # Preview changes
tidy                        # Move files to target
tidy --recursive            # Scan subdirectories
tidy --undo                 # Restore files
```

### Remote Sync - Keep remotes in sync

```bash
remote-sync --status        # Show sync status
remote-sync --push          # Push to all remotes
remote-sync --health-check  # Check connectivity
```

## Pre-commit Hooks

Add to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      # AI-powered auto-fix
      - id: ai-fix
        name: AI Fix
        entry: ai-fix --fix --auto
        language: system
        pass_filenames: false

      # File organization
      - id: tidy
        name: Tidy
        entry: tidy
        language: system
        pass_filenames: false

      # Multi-remote sync (post-commit)
      - id: remote-sync
        name: Sync
        entry: remote-sync --push
        language: system
        pass_filenames: false
        stages: [post-commit]
```

## Documentation

- [AI Fix](docs/ai-fix.md) - Models, configuration, batch processing
- [Tidy](docs/tidy.md) - Rules, duplicate handling, undo
- [Remote Sync](docs/remote-sync.md) - VPN, offline queue, rsync targets
