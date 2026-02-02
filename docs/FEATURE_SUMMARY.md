# Pre-Commit-Tidy Feature Summary

## 🎯 Overview

This is an extended fork of pre-commit that adds four standalone CLI tools for developer workflow automation:

| Tool | Config File | Purpose |
|------|-------------|---------|
| `tidy` | `.tidyrc.yaml` | Automated file organization with rule-based routing |
| `binary-track` | `.binariesrc.yaml` | Track locally-built binaries and detect staleness |
| `remote-sync` | `.remotesyncrc.yaml` | Keep multiple git remotes in sync |
| `ai-fix` | `.aifixrc.yaml` | **NEW** AI-powered linting error auto-fixer |

---

# AI Fix - Intelligent Pre-Commit Hook

## 🤖 Overview

AI Fix is an intelligent pre-commit hook that captures linting errors from multiple tools, aggregates them, and uses AI providers (GitHub Copilot CLI, Mistral, Ollama) to automatically suggest and apply fixes.

## ✨ Key Features

### Core Functionality
| Feature | Status | Description |
|---------|--------|-------------|
| **Multi-Linter Support** | ✅ Complete | Ruff, mypy, ESLint, Pylint, TypeScript |
| **AI Provider Chain** | ✅ Complete | Copilot CLI → Mistral → Ollama fallback |
| **Unified Error Schema** | ✅ Complete | Consistent error format across all linters |
| **Smart Deduplication** | ✅ Complete | Remove duplicate errors at same location |
| **Priority Sorting** | ✅ Complete | Security → Type → Lint → Style |
| **Fix Caching** | ✅ Complete | Cache successful fixes for similar errors |

### Fix Strategies
| Strategy | Description |
|----------|-------------|
| **auto_fix** | Apply fixes automatically (formatting, imports) |
| **prompt_fix** | Show diff and ask for confirmation |
| **never_fix** | Never auto-fix (security issues) |

### User Experience
| Feature | Status | Description |
|---------|--------|-------------|
| **Interactive Mode** | ✅ Complete | Approve/Skip/Edit/Quit per fix |
| **Dry-Run Mode** | ✅ Complete | Preview fixes without applying |
| **Explain Mode** | ✅ Complete | Show explanations for fixes |
| **JSON Output** | ✅ Complete | Machine-readable output for CI |
| **Colored Diffs** | ✅ Complete | Beautiful terminal output |

## 📊 Statistics

- **Lines of Code**: ~1,900 in `ai_fix.py`
- **Unit Tests**: 30 comprehensive tests
- **Linter Parsers**: 5 (Ruff, mypy, ESLint, Pylint, TSC)
- **AI Providers**: 3 (Copilot CLI, Mistral, Ollama)

## 🚀 Usage Examples

### Basic Usage
```bash
# Check for errors only
ai-fix --check

# Auto-fix with confirmation
ai-fix --fix

# Auto-fix everything without prompting
ai-fix --fix --auto

# Preview fixes without applying
ai-fix --fix --dry-run

# Show fix explanations
ai-fix --fix --explain

# Use specific AI provider
ai-fix --fix --provider ollama

# Use specific model (overrides smart selection)
ai-fix --fix --model claude-sonnet-4.5

# List available models
ai-fix --list-models
```

### Smart Model Selection
By default, ai-fix automatically selects the best model based on error complexity:

| Complexity | Provider | Model | Why |
|------------|----------|-------|-----|
| **Simple** (unused imports, formatting) | copilot-cli | claude-haiku-4.5 | Fast & cheap |
| **Simple** | vibe/mistral | codestral-latest | Code-optimized |
| **Simple** | ollama | qwen2.5-coder:7b | Fast local |
| **Moderate** (type hints) | copilot-cli | claude-sonnet-4 | Balanced |
| **Moderate** | ollama | codellama:13b | Good quality |
| **Complex** (security, logic) | copilot-cli | claude-sonnet-4.5 | Best quality |
| **Complex** | ollama | qwen2.5-coder:32b | Most capable |

You can override per complexity level:
```bash
ai-fix --fix --model-simple claude-haiku-4.5 --model-complex claude-opus-4.5
```

Or disable smart selection entirely:
```bash
ai-fix --fix --model claude-sonnet-4  # Use for all errors
ai-fix --fix --no-smart-models        # Use provider default for all
```

### Pre-Commit Integration
```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: ai-fix
        name: AI Auto-fix
        entry: ai-fix --fix
        language: system
        pass_filenames: false
        stages: [pre-commit]
```

### Configuration File
```yaml
# .aifixrc.yaml
ai_provider: copilot-cli

providers:
  copilot-cli:
    enabled: true
    timeout: 120
    smart_model_selection: true  # Enable smart model selection (default)
    # Override specific complexity levels:
    # model_simple: claude-haiku-4.5
    # model_moderate: claude-sonnet-4
    # model_complex: claude-sonnet-4.5
    # Or set a single model for all:
    # model: claude-sonnet-4
  vibe:
    enabled: true
    api_key_env: MISTRAL_API_KEY
    timeout: 120
  mistral:
    enabled: true
    api_key_env: MISTRAL_API_KEY
  ollama:
    enabled: true
    host: http://localhost:11434

linters:
  ruff:
    enabled: true
    args: ["check", "--output-format=json"]
  mypy:
    enabled: true
  eslint:
    enabled: true
    args: ["--format=json"]

fix_strategies:
  auto_fix:
    - "ruff:*"              # Auto-fix all ruff errors
    - "eslint:import/*"     # Auto-fix import ordering
    - "eslint:prettier/*"   # Auto-fix formatting
  prompt_fix:
    - "mypy:*"              # Prompt for type errors
    - "eslint:*"            # Prompt for other eslint
  never_fix:
    - "security:*"          # Never auto-fix security

behavior:
  batch_by_file: true       # Process all errors per file together
  dedupe_errors: true       # Remove duplicate errors
  context_lines: 5          # Lines of context for AI
  validate_fixes: true      # Re-run linter after fix
  max_fix_iterations: 3     # Prevent infinite loops

cache:
  enabled: true
  cache_dir: .ai-fix-cache
  ttl_hours: 168            # 1 week
```

## 🔧 Supported Linters

### Python
- **Ruff**: Fast Python linter (JSON output)
- **mypy**: Static type checker
- **Pylint**: Comprehensive Python linter

### JavaScript/TypeScript
- **ESLint**: Pluggable JS/TS linter (JSON output)
- **tsc**: TypeScript compiler errors

### Auto-Detection
Linters are automatically detected based on project files:
- `pyproject.toml`, `setup.py` → Enable Ruff, mypy
- `package.json` → Enable ESLint
- `tsconfig.json` → Enable tsc

## 🤖 AI Providers

### GitHub Copilot CLI (Recommended)
```bash
# Install: brew install copilot-cli OR npm install -g @github/copilot
ai-fix --fix --provider copilot-cli
ai-fix --fix --provider copilot-cli --model claude-sonnet-4  # Different model
```

### Mistral Vibe CLI
```bash
# Install: brew install mistralai/tap/vibe
# Requires: MISTRAL_API_KEY environment variable
export MISTRAL_API_KEY=your-key
ai-fix --fix --provider vibe
```

### Mistral API
```bash
# Requires: MISTRAL_API_KEY environment variable
export MISTRAL_API_KEY=your-key
ai-fix --fix --provider mistral --model mistral-large-latest
```

### Ollama (Local)
```bash
# Requires: Ollama running locally
ollama run codellama:13b
ai-fix --fix --provider ollama --model codellama:13b
```

## 📈 Fix Flow

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ ruff check  │───▶│ Parse JSON  │───▶│             │
└─────────────┘    └─────────────┘    │             │
                                      │  Aggregate  │    ┌──────────┐    ┌──────────┐
┌─────────────┐    ┌─────────────┐    │   & Dedupe  │───▶│  AI Fix  │───▶│  Apply   │
│ mypy        │───▶│ Parse text  │───▶│   & Sort    │    └──────────┘    └──────────┘
└─────────────┘    └─────────────┘    │             │           │
                                      │             │           ▼
┌─────────────┐    ┌─────────────┐    │             │    ┌──────────┐
│ eslint      │───▶│ Parse JSON  │───▶│             │    │ Validate │
└─────────────┘    └─────────────┘    └─────────────┘    └──────────┘
```

## 🎨 Example Output

```
Checking 5 file(s)...

Found 3 error(s)

error ruff:E501 src/main.py:42:89
  Line too long (120 > 88 characters)
  → Fix suggested by GitHub Copilot

--- src/main.py
@@ -40,3 +40,4 @@
-    result = some_very_long_function_call(argument1, argument2, argument3, argument4)
+    result = some_very_long_function_call(
+        argument1, argument2, argument3, argument4
+    )

[A]pply  [S]kip  [E]dit  [Q]uit? a
  → Applied

Summary
✓ Fixed: 3
ℹ Skipped: 0
✗ Failed: 0
```

## 🧪 Testing

```bash
# Run all ai-fix tests
pytest tests/test_ai_fix.py -v

# 30 tests covering:
# - LintError schema and matching
# - All linter parsers (Ruff, mypy, ESLint, Pylint, tsc)
# - Configuration loading (YAML, JSON, env vars)
# - Error deduplication and sorting
# - Fix strategy determination
# - Logger and Colors
# - AIFixRunner modes
```

## 📁 Files

- `pre_commit/ai_fix.py` - Main implementation (~1,900 lines)
- `tests/test_ai_fix.py` - Comprehensive tests (~600 lines)
- Entry point: `ai-fix` in `setup.cfg`

---

# Remote Sync Feature - Implementation Summary

## 🎯 Overview

Successfully implemented a comprehensive **multi-remote synchronization** feature for the pre-commit framework, enabling developers to automatically keep multiple git remotes in sync with intelligent VPN support for remotes behind firewalls.

## ✨ Key Features Implemented

### Core Functionality
| Feature | Status | Description |
|---------|--------|-------------|
| **Multi-Remote Push** | ✅ Complete | Push to multiple remotes in parallel or sequentially |
| **Health Checks** | ✅ Complete | Validate remote connectivity with latency reporting |
| **Sync Dashboard** | ✅ Complete | Visual status showing ahead/behind/diverged states |
| **Divergence Detection** | ✅ Complete | Warn when remotes have diverged from each other |
| **Branch Filtering** | ✅ Complete | Configure which branches sync to which remotes |
| **Force Push Protection** | ✅ Complete | Block/warn/allow force pushes per remote |
| **Offline Queue** | ✅ Complete | Queue failed pushes for later retry |
| **Auto-Discovery** | ✅ Complete | Automatically detect configured git remotes |

### Advanced Features
| Feature | Status | Description |
|---------|--------|-------------|
| **VPN Auto-Connect** | ✅ Complete | Automatically connect VPN for private remotes |
| **Smart VPN Detection** | ✅ Complete | Only connect VPN if remote is unreachable |
| **Named VPN Configs** | ✅ Complete | Reusable VPN configurations |
| **Inline VPN Config** | ✅ Complete | Per-remote VPN setup |
| **Parallel Push** | ✅ Complete | Push to multiple remotes simultaneously |
| **Retry with Backoff** | ✅ Complete | Exponential backoff for failed pushes |
| **Dry-Run Mode** | ✅ Complete | Preview actions without executing |

## 📊 Statistics

- **Lines of Code**: ~1,400 in `remote_sync.py`
- **Unit Tests**: 67 comprehensive tests (100% passing)
- **Test Coverage**: All major features covered
- **Documentation**: Complete with real-world examples
- **VPN Examples**: 5 different VPN providers

## 🚀 Usage Examples

### Basic Usage
```bash
# Show sync status for all remotes
remote-sync --status

# Push to all configured remotes
remote-sync --push

# Check remote health
remote-sync --health-check

# Preview what would be pushed
remote-sync --push --dry-run
```

### Configuration File
```json
{
  "remotes": {
    "origin": {
      "priority": 1,
      "branches": ["*"],
      "force_push": "block"
    },
    "github-mirror": {
      "priority": 2,
      "branches": ["main", "develop"]
    },
    "internal-server": {
      "priority": 3,
      "branches": ["main"],
      "vpn": "corporate"
    }
  },
  "vpn": {
    "corporate": {
      "connect_cmd": "networksetup -connectpppoeservice 'VPN'",
      "disconnect_cmd": "networksetup -disconnectpppoeservice 'VPN'",
      "check_cmd": "scutil --nc status 'VPN' | grep Connected"
    }
  },
  "parallel": true,
  "offline_queue": true
}
```

## 🧪 Testing Results

### Real-World Testing
- ✅ Tested with 2 GitHub remotes
- ✅ Parallel push: ~2.34s for 2 remotes
- ✅ Health check: ~1.6s for 2 remotes
- ✅ Status check: < 0.5s
- ✅ All features verified working

### Unit Testing
```
67 tests passed:
  ✅ Configuration loading (file, env, CLI)
  ✅ Remote operations (push, fetch, health check)
  ✅ Queue management (save, load, process)
  ✅ VPN operations (connect, disconnect, check)
  ✅ Branch pattern matching
  ✅ Divergence detection
  ✅ CLI argument parsing
```

## 🔒 VPN Support Examples

### macOS Built-in VPN
```json
{
  "vpn": {
    "corporate": {
      "connect_cmd": "networksetup -connectpppoeservice 'My VPN'",
      "disconnect_cmd": "networksetup -disconnectpppoeservice 'My VPN'",
      "check_cmd": "scutil --nc status 'My VPN' | grep -q Connected"
    }
  }
}
```

### OpenVPN
```json
{
  "vpn": {
    "office": {
      "connect_cmd": "sudo openvpn --config /etc/openvpn/office.conf --daemon",
      "disconnect_cmd": "sudo killall openvpn",
      "check_cmd": "pgrep openvpn"
    }
  }
}
```

### WireGuard
```json
{
  "vpn": {
    "wg": {
      "connect_cmd": "wg-quick up wg0",
      "disconnect_cmd": "wg-quick down wg0",
      "check_cmd": "wg show wg0"
    }
  }
}
```

### SSH Tunnel
```json
{
  "vpn": {
    "tunnel": {
      "connect_cmd": "ssh -f -N -D 1080 jumphost",
      "disconnect_cmd": "pkill -f 'ssh.*jumphost'",
      "check_cmd": "pgrep -f 'ssh.*jumphost'"
    }
  }
}
```

## 📁 Files Created/Modified

### New Files
- `pre_commit/remote_sync.py` - Main implementation (~1,400 lines)
- `tests/test_remote_sync.py` - Comprehensive tests (~670 lines)
- `.remotesyncrc.json` - Example configuration
- `TESTING_RESULTS.md` - Real-world test results
- `FEATURE_SUMMARY.md` - This document

### Modified Files
- `README.md` - Added remote-sync documentation
- `setup.cfg` - Added `remote-sync` CLI entry point

## 🎨 User Experience

### Beautiful Output
```
Sync Status Dashboard
  Branch: develop

  ✓ origin (priority: 1)
    State: in sync
    Local:  fd61fb67
    Remote: fd61fb67

  ↑ github (priority: 2)
    State: ahead by 1 commit(s)
    Local:  fd61fb67
    Remote: d4b75047

Push Results
  ✓ origin/develop
    Successfully pushed develop to origin
    Duration: 0.84s

  ✓ github/develop
    Successfully pushed develop to github
    Duration: 1.45s
    🔒 VPN: corporate

  Summary: 2 succeeded, 0 failed
```

## 🏆 Best Practices Followed

- ✅ Modern Python (3.10+) with type hints
- ✅ Dataclasses for configuration
- ✅ Enums for status/state types
- ✅ Comprehensive error handling
- ✅ Thread-safe VPN connection tracking
- ✅ Context managers for resource cleanup
- ✅ Parallel execution with ThreadPoolExecutor
- ✅ Exponential backoff with jitter
- ✅ Colored terminal output
- ✅ Dry-run support throughout
- ✅ Configuration file hierarchy (file → env → CLI)

## 💡 Use Cases

1. **Mirror Repositories**: Automatically sync to GitHub, GitLab, Bitbucket
2. **Backup Strategy**: Push to backup remotes on commit
3. **Private Networks**: Use VPN to access internal git servers
4. **Team Sync**: Ensure all team members' forks stay in sync
5. **CI/CD**: Integrate into deployment pipelines
6. **Geo-Distribution**: Sync to remotes in different regions

## 🔮 Future Enhancements (Optional)

- [ ] Pre-commit hook integration for automatic sync
- [ ] Webhook triggers for push events
- [ ] Remote selection by group
- [ ] Custom push strategies per remote
- [ ] Conflict resolution strategies
- [ ] Web UI for configuration
- [ ] Metrics and analytics

## 📝 Documentation

Complete documentation available in:
- `README.md` - User guide and examples
- `TESTING_RESULTS.md` - Real-world test results
- Inline docstrings - Full API documentation
- CLI help - `remote-sync --help`

## 🎉 Conclusion

The multi-remote sync feature is **production-ready** and provides significant value to developers managing multiple git remotes. The VPN support makes it especially useful for teams with private infrastructure.

**Installation**: `pip install -e .`
**Usage**: `remote-sync --help`
**Repository**: https://github.com/codefuturist/pre-commit
