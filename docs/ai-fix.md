# AI Fix - AI-Powered Linting Auto-Fixer

Automatically fix linting errors using AI providers (GitHub Copilot CLI, Mistral Vibe, Ollama). Features smart model selection based on error complexity, iterative fixing, and batch processing.

## CLI Options

```
Usage: ai-fix [options]

Options:
  --config PATH           Path to configuration file
  --check                 Check for errors without fixing
  --fix                   Attempt to fix errors using AI
  --auto                  Auto-apply fixes without prompting
  --dry-run               Preview fixes without applying
  --explain               Show explanations for fixes
  --provider PROVIDER     AI provider (copilot-cli|vibe|mistral|ollama)
  --model MODEL           Model to use (overrides smart selection)
  --model-simple MODEL    Model for simple errors
  --model-moderate MODEL  Model for moderate errors
  --model-complex MODEL   Model for complex errors
  --no-smart-models       Disable smart model selection
  --list-models           Show available models
  --linters LINTERS       Comma-separated linters to run
  --files FILES           Files to check (default: staged files)
  --timeout N             Timeout in seconds (default: 120)
  --max-iterations N      Maximum fix iterations (default: 3)
  --batch-size N          Override batch size for all complexities
  --single-issue          Process one issue at a time
  --verbose, -v           Show detailed output
  --quiet, -q             Suppress all output except errors
  --json                  Output results in JSON format
  --version               Show version number
```

## Smart Model Selection

AI Fix automatically selects the optimal model based on error complexity:

| Complexity | Example Errors | Copilot CLI | Vibe/Mistral | Ollama |
|------------|----------------|-------------|--------------|--------|
| **Simple** | Unused imports, formatting | claude-haiku-4.5 | devstral-small | qwen2.5-coder:7b |
| **Moderate** | Type hints, simple logic | claude-sonnet-4.5 | devstral-2 | codellama:13b |
| **Complex** | Security issues, bugs | claude-opus-4.5 | devstral-2 | qwen2.5-coder:32b |

Override with:
```bash
ai-fix --fix --model claude-sonnet-4.5           # Use for all errors
ai-fix --fix --model-complex claude-opus-4.5     # Override only complex
ai-fix --fix --no-smart-models                   # Use provider default
```

## Configuration

Create `.aifixrc.yaml` in your project root:

```yaml
ai_provider: copilot-cli  # or: vibe, mistral, ollama

providers:
  copilot-cli:
    timeout: 120
    smart_model_selection: true
    # model: claude-sonnet-4.5          # Override default
    # model_simple: claude-haiku-4.5    # Per-complexity
    # model_moderate: claude-sonnet-4.5
    # model_complex: claude-opus-4.5
  vibe:
    api_key_env: MISTRAL_API_KEY
    timeout: 120
  mistral:
    api_key_env: MISTRAL_API_KEY
  ollama:
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

behavior:
  batch_size_simple: 10      # Batch up to 10 simple errors
  batch_size_moderate: 3     # Batch up to 3 moderate errors
  batch_size_complex: 1      # Always one complex error at a time
  max_fix_iterations: 3      # Re-run linters up to 3 times
  rerun_after_batch: true    # Re-lint after each batch

fix_strategies:
  auto_fix:
    - "ruff:F401"           # Auto-fix unused imports
    - "ruff:I*"             # Auto-fix import sorting
  never_fix:
    - "ruff:S*"             # Never auto-fix security issues
```

## Supported Providers

| Provider | Install | Requirements |
|----------|---------|--------------|
| **copilot-cli** | `brew install copilot-cli` | GitHub Copilot subscription |
| **vibe** | `brew install mistralai/tap/vibe` | `MISTRAL_API_KEY` env var |
| **mistral** | (API only) | `MISTRAL_API_KEY` env var |
| **ollama** | `ollama serve` | Local Ollama running |

### Available Models

Run `ai-fix --list-models` to see all available models with quality/speed ratings.

**Copilot CLI:** claude-haiku-4.5, claude-sonnet-4, claude-sonnet-4.5, claude-opus-4.5, gpt-4.1, gpt-5-mini, gpt-5, gpt-5.1, gpt-5.2, gpt-5.1-codex, gpt-5.1-codex-mini, gpt-5.1-codex-max, gpt-5.2-codex, gemini-3-pro-preview

**Vibe/Mistral:** devstral-small, devstral-2, codestral-latest, mistral-small-latest, mistral-medium-latest, mistral-large-latest

**Ollama:** codellama:7b/13b/34b, deepseek-coder:6.7b/33b, qwen2.5-coder:7b/32b, llama3.2:3b, llama3.3:70b

## Supported Linters

- **Python**: Ruff, mypy, Pylint
- **JavaScript/TypeScript**: ESLint, tsc

## Pre-commit Hooks

```yaml
# Auto-fix all errors without prompting
- id: ai-fix
  name: AI Fix
  entry: ai-fix --fix --auto
  language: system
  pass_filenames: false

# Interactive mode (prompts for each fix)
- id: ai-fix
  name: AI Fix (Interactive)
  entry: ai-fix --fix
  language: system
  pass_filenames: false

# Check only (no fixes, just report)
- id: ai-fix-check
  name: AI Fix Check
  entry: ai-fix --check
  language: system
  pass_filenames: false

# With specific provider and model
- id: ai-fix
  name: AI Fix (Ollama)
  entry: ai-fix --fix --auto --provider ollama --model qwen2.5-coder:7b
  language: system
  pass_filenames: false
```

## Batch Processing

AI Fix groups errors by complexity and processes them in batches:

1. **Complex errors** processed first (one at a time)
2. **Moderate errors** in small batches (default: 3)
3. **Simple errors** in larger batches (default: 10)

After each batch, linters are re-run to check for new/resolved errors.

Use `--single-issue` to process one error at a time (useful for complex codebases).
