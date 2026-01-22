"""AI Fix - Intelligent pre-commit hook that uses AI to automatically
fix linting errors.

Captures errors from multiple linters (Ruff, ESLint, mypy, etc.),
aggregates them, and uses AI providers (Copilot CLI, Vibe, Mistral,
Ollama) to suggest and apply fixes.

Usage:
    ai-fix [options]
    ai-fix --check                    # Check for errors without fixing
    ai-fix --fix                      # Auto-fix safe errors, prompt for others
    ai-fix --fix --auto               # Auto-fix all errors without prompting
    ai-fix --explain                  # Show explanations for fixes

Options:
    --config PATH           Path to configuration file (default: .aifixrc.yaml)
    --check                 Run linters and report errors (no fixes)
    --fix                   Attempt to fix errors using AI
    --auto                  Auto-apply fixes without prompting (use with --fix)
    --dry-run               Preview fixes without applying them
    --explain               Show explanations for each fix
    --provider PROVIDER     AI provider to use (copilot-cli|vibe|mistral|ollama)
    --model MODEL           Model to use for the provider
    --linters LINTERS       Comma-separated linters to run (default: auto-detect)
    --files FILES           Files to check (default: staged files)
    --max-retries N         Maximum fix attempts per error (default: 2)
    --timeout N             Timeout in seconds for AI calls (default: 30)
    --verbose               Show detailed output
    --quiet                 Suppress all output except errors
    --json                  Output results in JSON format
    --version               Show version number
    --help                  Show this help message

Configuration:
    Create a .aifixrc.yaml file in your project root:

    {
        "ai_provider": "copilot-cli",
        "providers": {
            "copilot-cli": {
                "enabled": true,
                "timeout": 120
            },
            "vibe": {
                "enabled": true,
                "api_key_env": "MISTRAL_API_KEY",
                "timeout": 120
            },
            "mistral": {
                "enabled": false,
                "api_key_env": "MISTRAL_API_KEY",
                "model": "mistral-large-latest",
                "timeout": 60
            },
            "ollama": {
                "enabled": false,
                "model": "codellama:13b",
                "host": "http://localhost:11434",
                "timeout": 120
            }
        },
        "linters": {
            "ruff": {
                "enabled": true,
                "auto_detect": true,
                "args": ["check", "--output-format=json"],
                "fix_args": ["check", "--fix", "--output-format=json"]
            },
            "mypy": {
                "enabled": true,
                "auto_detect": true,
                "args": ["--output=json"]
            },
            "eslint": {
                "enabled": true,
                "auto_detect": true,
                "args": ["--format=json"]
            },
            "pylint": {
                "enabled": false,
                "args": ["--output-format=json"]
            },
            "tsc": {
                "enabled": true,
                "auto_detect": true,
                "args": ["--noEmit"]
            }
        },
        "fix_strategies": {
            "auto_fix": [
                "ruff:*",
                "eslint:import/*",
                "eslint:prettier/*"
            ],
            "prompt_fix": [
                "mypy:*",
                "eslint:*"
            ],
            "never_fix": [
                "security:*",
                "eslint:no-explicit-any"
            ]
        },
        "behavior": {
            "batch_by_file": true,
            "dedupe_errors": true,
            "priority_order": ["security", "type", "lint", "style"],
            "max_errors_per_file": 50,
            "max_total_errors": 200,
            "context_lines": 5,
            "validate_fixes": true,
            "max_fix_iterations": 3
        },
        "cache": {
            "enabled": true,
            "cache_dir": ".ai-fix-cache",
            "ttl_hours": 168
        },
        "binaries": {
            "git": "/usr/local/bin/git",
            "ruff": "/path/to/ruff",
            "mypy": "/path/to/mypy",
            "eslint": "/path/to/eslint",
            "pylint": "/path/to/pylint",
            "tsc": "/path/to/tsc",
            "npx": "/path/to/npx",
            "copilot": "/path/to/copilot",
            "gh": "/path/to/gh",
            "vibe": "/path/to/vibe",
            "ollama": "/path/to/ollama"
        }
    }

    Custom Binary Paths:
    - All binaries can be overridden with absolute paths
    - Useful when binaries are in non-standard locations
    - Supports: git, ruff, mypy, eslint, pylint, tsc, npx, copilot,
      gh, vibe, ollama

    Auto-detection:
    - Linters are auto-detected based on project files
      (package.json, pyproject.toml, etc.)
    - AI provider falls back through the chain:
      copilot-cli → mistral → ollama

    Fix Strategies:
    - "auto_fix": Apply fixes automatically without prompting
    - "prompt_fix": Show diff and ask for confirmation
    - "never_fix": Never auto-fix, only report

    Pattern Matching:
    - "ruff:*" matches all ruff errors
    - "eslint:import/*" matches eslint import-related errors
    - "mypy:arg-type" matches specific mypy error code

Environment Variables:
    AI_FIX_PROVIDER         Default AI provider
    AI_FIX_DRY_RUN          Set to 'true' for dry run
    AI_FIX_VERBOSE          Set to 'true' for verbose output
    AI_FIX_AUTO             Set to 'true' for auto-fix mode
    MISTRAL_API_KEY         API key for Mistral
    OLLAMA_HOST             Host URL for Ollama

Pre-commit Integration:
    Add to .pre-commit-config.yaml:

    repos:
      - repo: local
        hooks:
          - id: ai-fix
            name: AI Auto-fix
            entry: ai-fix --fix
            language: system
            pass_filenames: false
            stages: [pre-commit]

    For capturing output from other hooks:

    repos:
      - repo: local
        hooks:
          - id: lint-capture
            name: Capture lint errors
            entry: bash -c 'ruff check --output-format=json > /tmp/ai-fix-lint.json || true'
            pass_filenames: false

          - id: ai-fix
            name: AI Auto-fix
            entry: ai-fix --fix --input /tmp/ai-fix-lint.json
            language: system
            pass_filenames: false

Examples:
    ai-fix --check                          # Check for errors only
    ai-fix --fix --dry-run                  # Preview fixes
    ai-fix --fix --auto                     # Auto-fix everything
    ai-fix --fix --provider ollama          # Use local Ollama
    ai-fix --fix --linters ruff,mypy        # Only run specific linters
    ai-fix --explain                        # Show fix explanations
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from enum import Enum
from pathlib import Path
from typing import Any
from typing import cast
from typing import TypedDict

import yaml

# Version
__version__ = '2.0.0'

# Default configuration file names
CONFIG_FILE_NAMES = [
    '.aifixrc.yaml',
    '.aifixrc.yml',
    '.aifixrc.json',
    'ai-fix.config.yaml',
]

# Supported linters for log file detection
SUPPORTED_LINTERS = ['ruff', 'mypy', 'eslint', 'pylint', 'flake8', 'biome']


def get_state_home() -> Path:
    """Get XDG_STATE_HOME directory following freedesktop.org spec.

    $XDG_STATE_HOME is specifically designed for:
    - Action history (logs, history, recently used files)
    - Current state that should persist between restarts

    Returns:
        Path to state home directory
    """
    state_home = os.environ.get('XDG_STATE_HOME')
    if state_home:
        return Path(state_home)
    return Path.home() / '.local' / 'state'


def get_repo_root() -> Path | None:
    """Get the root directory of the current git repository.

    Returns:
        Path to repo root, or None if not in a git repo
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_repo_hash(repo_root: Path | None = None) -> str:
    """Generate a short hash for the repository path.

    This provides per-repo isolation without polluting the repo itself.

    Args:
        repo_root: Repository root path (auto-detected if None)

    Returns:
        12-character hash string identifying the repo
    """
    if repo_root is None:
        repo_root = get_repo_root()

    if repo_root:
        # Use resolved path for consistent hashing
        path_str = str(repo_root.resolve())
    else:
        # Fallback to current directory for non-git projects
        path_str = str(Path.cwd().resolve())

    return hashlib.sha256(path_str.encode()).hexdigest()[:12]


def get_log_dir(repo_root: Path | None = None) -> Path:
    """Get the log directory for pre-commit linter output.

    Follows XDG Base Directory Specification:
    - Uses $XDG_STATE_HOME/pre-commit/logs/{repo_hash}/
    - Falls back to ~/.local/state/pre-commit/logs/{repo_hash}/

    This provides:
    - Per-repo isolation (no cross-repo contamination)
    - Standards compliance (XDG spec)
    - No repository pollution
    - Persistence across reboots
    - Easy discoverability

    Args:
        repo_root: Repository root path (auto-detected if None)

    Returns:
        Path to log directory for this repository

    Example:
        ~/.local/state/pre-commit/logs/a1b2c3d4e5f6/
    """
    base = get_state_home() / 'pre-commit' / 'logs'
    repo_hash = get_repo_hash(repo_root)
    return base / repo_hash


def get_linter_log_path(linter: str, repo_root: Path | None = None) -> Path:
    """Get the log file path for a specific linter.

    Args:
        linter: Linter name (ruff, mypy, eslint, etc.)
        repo_root: Repository root path (auto-detected if None)

    Returns:
        Path to the linter's JSON log file
    """
    return get_log_dir(repo_root) / f'{linter}.json'


def ensure_log_dir(repo_root: Path | None = None) -> Path:
    """Ensure the log directory exists and return its path.

    Args:
        repo_root: Repository root path (auto-detected if None)

    Returns:
        Path to the created/existing log directory
    """
    log_dir = get_log_dir(repo_root)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


# Legacy input methods removed - use load_errors_from_log_files() instead


def load_errors_from_log_files(
    log_files: list[Path] | None = None,
    log_dir: Path | None = None,
    repo_root: Path | None = None,
) -> list[LintError]:
    """Load errors from pre-commit log_file outputs.

    This reads JSON log files created by pre-commit's log_file feature.
    Each linter writes its JSON output directly to its log file.

    Log files are stored in XDG-compliant location:
        $XDG_STATE_HOME/pre-commit/logs/{repo_hash}/{linter}.json

    Args:
        log_files: Explicit list of log files to read
        log_dir: Override directory containing log files
        repo_root: Repository root (auto-detected if None)

    Returns:
        List of LintError objects
    """
    errors: list[LintError] = []

    # Determine which files to read
    files_to_read: list[tuple[str, Path]] = []

    if log_files:
        # Explicit list of files provided
        for lf in log_files:
            linter = _infer_linter_from_filename(lf.stem)
            files_to_read.append((linter, lf))
    else:
        # Auto-discover log files in the log directory
        search_dir = log_dir or get_log_dir(repo_root)

        if search_dir.exists():
            for filepath in search_dir.glob('*.json'):
                linter = _infer_linter_from_filename(filepath.stem)
                files_to_read.append((linter, filepath))

    # Read each log file
    for linter, filepath in files_to_read:
        if not filepath.exists():
            continue

        try:
            content = filepath.read_text().strip()
            if not content:
                continue

            # Try to parse as JSON array
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                # Log file might have non-JSON header/footer from pre-commit
                # Try to extract JSON array
                json_match = re.search(r'\[[\s\S]*\]', content)
                if json_match:
                    data = json.loads(json_match.group())
                else:
                    logger.warning(f'Could not parse JSON from {filepath}')
                    continue

            # Parse based on linter type
            if linter == 'ruff':
                errors.extend(_parse_ruff_json(data))
            elif linter == 'mypy':
                errors.extend(_parse_mypy_json(data))
            elif linter == 'eslint':
                errors.extend(_parse_eslint_json(data))
            elif linter == 'biome':
                errors.extend(_parse_biome_json(data))
            else:
                errors.extend(_parse_generic_json(linter, data))

            logger.debug(f'Loaded {len(data) if isinstance(data, list) else 1} items from {filepath}')

        except Exception as e:
            logger.warning(f'Failed to load log file {filepath}: {e}')
            continue

    return errors


def _infer_linter_from_filename(stem: str) -> str:
    """Infer linter name from log file stem."""
    stem_lower = stem.lower()
    for linter in SUPPORTED_LINTERS:
        if linter in stem_lower:
            return linter
    return stem_lower


def clear_log_files(
    log_dir: Path | None = None,
    repo_root: Path | None = None,
) -> Path:
    """Clear all linter log files for the current repository.

    Used by a "clear" hook at the start of pre-commit to ensure fresh logs.

    Args:
        log_dir: Override directory to clear
        repo_root: Repository root (auto-detected if None)

    Returns:
        Path to the (cleared and recreated) log directory
    """
    target_dir = log_dir or get_log_dir(repo_root)

    if target_dir.exists():
        for filepath in target_dir.glob('*.json'):
            try:
                filepath.unlink()
            except OSError:
                pass

    # Ensure directory exists for subsequent writes
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _parse_biome_json(data: list[dict[str, Any]] | dict[str, Any]) -> list[LintError]:
    """Parse Biome JSON output."""
    errors = []
    # Biome outputs diagnostics in a specific format
    if isinstance(data, dict):
        data = data.get('diagnostics', [])
    if not isinstance(data, list):
        return errors

    for item in data:
        if not isinstance(item, dict):
            continue
        location = item.get('location', {})
        errors.append(LintError(
            linter='biome',
            file=location.get('path', {}).get('file', ''),
            line=location.get('span', [0])[0] if location.get('span') else 0,
            column=0,
            code=item.get('category', ''),
            message=item.get('message', ''),
            severity=Severity.ERROR if item.get('severity') == 'error' else Severity.WARNING,
        ))
    return errors


def _parse_ruff_json(data: list[dict[str, Any]]) -> list[LintError]:
    """Parse ruff JSON output."""
    errors = []
    if not isinstance(data, list):
        return errors

    for item in data:
        if not isinstance(item, dict):
            continue
        errors.append(LintError(
            linter='ruff',
            file=item.get('filename', ''),
            line=item.get('location', {}).get('row', 0),
            column=item.get('location', {}).get('column', 0),
            code=item.get('code', ''),
            message=item.get('message', ''),
            severity=Severity.ERROR,
            suggestion=item.get('fix', {}).get('message') if item.get('fix') else None,
        ))
    return errors


def _parse_eslint_json(data: list[dict[str, Any]]) -> list[LintError]:
    """Parse ESLint JSON output."""
    errors = []
    if not isinstance(data, list):
        return errors

    for file_result in data:
        if not isinstance(file_result, dict):
            continue
        filepath = file_result.get('filePath', '')
        for msg in file_result.get('messages', []):
            severity = Severity.ERROR if msg.get('severity', 2) == 2 else Severity.WARNING
            errors.append(LintError(
                linter='eslint',
                file=filepath,
                line=msg.get('line', 0),
                column=msg.get('column', 0),
                code=msg.get('ruleId', ''),
                message=msg.get('message', ''),
                severity=severity,
                suggestion=msg.get('fix', {}).get('text') if msg.get('fix') else None,
            ))
    return errors


def _parse_mypy_json(data: list[dict[str, Any]]) -> list[LintError]:
    """Parse mypy JSON output."""
    errors = []
    if not isinstance(data, list):
        return errors

    for item in data:
        if not isinstance(item, dict):
            continue
        severity_str = item.get('severity', 'error')
        severity = Severity.ERROR if severity_str == 'error' else Severity.WARNING
        errors.append(LintError(
            linter='mypy',
            file=item.get('file', ''),
            line=item.get('line', 0),
            column=item.get('column', 0),
            code=item.get('code', 'error'),
            message=item.get('message', ''),
            severity=severity,
        ))
    return errors


def _parse_generic_json(linter: str, data: Any) -> list[LintError]:
    """Parse generic linter JSON output."""
    errors = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                errors.append(LintError(
                    linter=linter,
                    file=item.get('file', item.get('filename', item.get('path', ''))),
                    line=item.get('line', item.get('row', 0)),
                    column=item.get('column', item.get('col', 0)),
                    code=item.get('code', item.get('rule', item.get('ruleId', ''))),
                    message=item.get('message', item.get('msg', '')),
                    severity=Severity.ERROR,
                ))
    return errors


# Legacy functions removed - use load_errors_from_log_files() instead


# =============================================================================
# Enums and Constants
# =============================================================================


class Severity(Enum):
    """Error severity levels."""
    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'
    HINT = 'hint'


class FixStrategy(Enum):
    """How to handle different error types."""
    AUTO = 'auto'           # Apply automatically
    PROMPT = 'prompt'       # Ask user for confirmation
    NEVER = 'never'         # Never auto-fix
    SKIP = 'skip'           # Skip this error


class FixResult(Enum):
    """Result of attempting to fix an error."""
    FIXED = 'fixed'
    SKIPPED = 'skipped'
    FAILED = 'failed'
    REJECTED = 'rejected'   # User rejected the fix
    NO_FIX = 'no_fix'       # AI couldn't suggest a fix


class AIProvider(Enum):
    """Supported AI providers."""
    COPILOT_CLI = 'copilot-cli'
    VIBE = 'vibe'
    MISTRAL = 'mistral'
    OLLAMA = 'ollama'
    MOCK = 'mock'  # For testing


class ErrorComplexity(Enum):
    """Complexity level of an error for batching decisions."""
    SIMPLE = 'simple'       # Formatting, imports, unused vars - batch many
    MODERATE = 'moderate'   # Type hints, simple logic - batch few
    COMPLEX = 'complex'     # Security, complex logic - one at a time


# =============================================================================
# Model Configuration - Smart Defaults
# =============================================================================

# Available models by provider with metadata
# Format: {model_id: {'speed': 'fast'|'medium'|'slow', 'quality': 'low'|'medium'|'high', 'cost': 'low'|'medium'|'high'}}
COPILOT_MODELS = {
    'claude-haiku-4.5': {'speed': 'fast', 'quality': 'medium', 'cost': 'low'},
    'claude-sonnet-4': {'speed': 'medium', 'quality': 'high', 'cost': 'medium'},
    'claude-sonnet-4.5': {'speed': 'medium', 'quality': 'high', 'cost': 'medium'},
    'claude-opus-4.5': {'speed': 'slow', 'quality': 'highest', 'cost': 'high'},
    'gpt-4.1': {'speed': 'fast', 'quality': 'medium', 'cost': 'low'},
    'gpt-5-mini': {'speed': 'fast', 'quality': 'medium', 'cost': 'low'},
    'gpt-5': {'speed': 'medium', 'quality': 'high', 'cost': 'medium'},
    'gpt-5.1': {'speed': 'medium', 'quality': 'high', 'cost': 'medium'},
    'gpt-5.2': {'speed': 'medium', 'quality': 'high', 'cost': 'medium'},
    'gpt-5.1-codex': {'speed': 'medium', 'quality': 'high', 'cost': 'medium'},
    'gpt-5.1-codex-mini': {'speed': 'fast', 'quality': 'medium', 'cost': 'low'},
    'gpt-5.1-codex-max': {'speed': 'slow', 'quality': 'highest', 'cost': 'high'},
    'gpt-5.2-codex': {'speed': 'medium', 'quality': 'high', 'cost': 'medium'},
    'gemini-3-pro-preview': {'speed': 'medium', 'quality': 'high', 'cost': 'medium'},
}

VIBE_MODELS = {
    'devstral-small': {'speed': 'fast', 'quality': 'medium', 'cost': 'low'},
    'devstral-2': {'speed': 'medium', 'quality': 'highest', 'cost': 'medium'},
    'codestral-latest': {'speed': 'fast', 'quality': 'high', 'cost': 'medium'},
    'mistral-small-latest': {'speed': 'fast', 'quality': 'medium', 'cost': 'low'},
    'mistral-medium-latest': {'speed': 'medium', 'quality': 'medium', 'cost': 'medium'},
    'mistral-large-latest': {'speed': 'medium', 'quality': 'high', 'cost': 'medium'},
    'open-codestral-mamba': {'speed': 'fast', 'quality': 'medium', 'cost': 'low'},
}

MISTRAL_MODELS = VIBE_MODELS.copy()  # Same models available via API

OLLAMA_MODELS = {
    'codellama:7b': {'speed': 'fast', 'quality': 'medium', 'cost': 'free'},
    'codellama:13b': {'speed': 'medium', 'quality': 'high', 'cost': 'free'},
    'codellama:34b': {'speed': 'slow', 'quality': 'highest', 'cost': 'free'},
    'deepseek-coder:6.7b': {'speed': 'fast', 'quality': 'medium', 'cost': 'free'},
    'deepseek-coder:33b': {'speed': 'slow', 'quality': 'high', 'cost': 'free'},
    'qwen2.5-coder:7b': {'speed': 'fast', 'quality': 'high', 'cost': 'free'},
    'qwen2.5-coder:32b': {'speed': 'slow', 'quality': 'highest', 'cost': 'free'},
    'llama3.2:3b': {'speed': 'fast', 'quality': 'low', 'cost': 'free'},
    'llama3.3:70b': {'speed': 'slow', 'quality': 'highest', 'cost': 'free'},
}

# Smart model selection based on error complexity
# These are optimized defaults: fast models for simple fixes, powerful for complex
MODEL_DEFAULTS_BY_COMPLEXITY = {
    'copilot-cli': {
        ErrorComplexity.SIMPLE: 'claude-haiku-4.5',    # Fast, cheap, good enough
        ErrorComplexity.MODERATE: 'claude-sonnet-4.5',  # High quality
        ErrorComplexity.COMPLEX: 'claude-opus-4.5',     # Best quality
    },
    'vibe': {
        ErrorComplexity.SIMPLE: 'devstral-small',      # Fast for simple tasks
        ErrorComplexity.MODERATE: 'devstral-2',        # Balanced
        ErrorComplexity.COMPLEX: 'devstral-2',         # Best Mistral coding model
    },
    'mistral': {
        ErrorComplexity.SIMPLE: 'devstral-small',
        ErrorComplexity.MODERATE: 'devstral-2',
        ErrorComplexity.COMPLEX: 'devstral-2',
    },
    'ollama': {
        ErrorComplexity.SIMPLE: 'qwen2.5-coder:7b',    # Fast local model
        ErrorComplexity.MODERATE: 'codellama:13b',
        ErrorComplexity.COMPLEX: 'qwen2.5-coder:32b',  # Best local model
    },
}

# Default model per provider (used when complexity-based selection is disabled)
DEFAULT_MODELS = {
    'copilot-cli': 'claude-sonnet-4.5',
    'vibe': 'devstral-2',
    'mistral': 'devstral-2',
    'ollama': 'qwen2.5-coder:7b',
}

# Provider priority order for auto-detection
PROVIDER_PRIORITY = ['copilot-cli', 'vibe', 'ollama', 'mistral']

# Category priorities (lower = higher priority)
CATEGORY_PRIORITY = {
    'security': 0,
    'type': 1,
    'error': 2,
    'lint': 3,
    'style': 4,
    'format': 5,
}

# Complexity classification by error patterns
# Errors matching these patterns are classified accordingly
COMPLEXITY_PATTERNS: dict[ErrorComplexity, list[str]] = {
    ErrorComplexity.SIMPLE: [
        'ruff:I*',      # isort imports
        'ruff:F401',    # unused imports
        'ruff:F841',    # unused variables
        'ruff:W*',      # warnings (whitespace, etc.)
        'ruff:E501',    # line too long
        'ruff:UP*',     # pyupgrade
        'eslint:import/*',
        'eslint:prettier/*',
        'eslint:@typescript-eslint/no-unused-vars',
    ],
    ErrorComplexity.MODERATE: [
        'ruff:E*',      # other errors
        'ruff:F*',      # pyflakes
        'mypy:*',       # type errors
        'tsc:*',        # typescript errors
        'eslint:*',     # other eslint
        'pylint:*',     # pylint
    ],
    ErrorComplexity.COMPLEX: [
        'ruff:S*',      # security (bandit)
        'ruff:B*',      # bugbear
        'ruff:C9*',     # complexity
        'security:*',   # any security category
        'eslint:security/*',
    ],
}


# =============================================================================
# TypedDict Configuration Schemas
# =============================================================================


class ProviderConfigDict(TypedDict, total=False):
    """Configuration for a single AI provider."""
    enabled: bool
    api_key_env: str
    model: str  # Default model (overrides smart selection)
    model_simple: str  # Model for simple errors
    model_moderate: str  # Model for moderate errors
    model_complex: str  # Model for complex errors
    smart_model_selection: bool  # Use different models by complexity
    host: str
    timeout: int


class ProvidersConfigDict(TypedDict, total=False):
    """Configuration for all AI providers."""
    copilot_cli: ProviderConfigDict
    vibe: ProviderConfigDict
    mistral: ProviderConfigDict
    ollama: ProviderConfigDict


class LinterConfigDict(TypedDict, total=False):
    """Configuration for a single linter."""
    enabled: bool
    auto_detect: bool
    command: str
    args: list[str]
    fix_args: list[str]
    error_pattern: str
    json_output: bool


class LintersConfigDict(TypedDict, total=False):
    """Configuration for all linters."""
    ruff: LinterConfigDict
    mypy: LinterConfigDict
    eslint: LinterConfigDict
    pylint: LinterConfigDict
    tsc: LinterConfigDict


class FixStrategiesDict(TypedDict, total=False):
    """Fix strategy configuration."""
    auto_fix: list[str]
    prompt_fix: list[str]
    never_fix: list[str]


class BehaviorConfigDict(TypedDict, total=False):
    """Behavior configuration."""
    batch_by_file: bool
    dedupe_errors: bool
    priority_order: list[str]
    max_errors_per_file: int
    max_total_errors: int
    context_lines: int
    validate_fixes: bool
    max_fix_iterations: int
    # Batching settings
    batch_size_simple: int      # Max errors per batch for simple issues
    batch_size_moderate: int    # Max errors per batch for moderate issues
    batch_size_complex: int     # Max errors per batch for complex issues (usually 1)
    rerun_after_batch: bool     # Re-run linters after each batch


class CacheConfigDict(TypedDict, total=False):
    """Cache configuration."""
    enabled: bool
    cache_dir: str
    ttl_hours: int


class BinariesConfigDict(TypedDict, total=False):
    """Custom binary paths configuration."""
    git: str
    ruff: str
    mypy: str
    eslint: str
    pylint: str
    tsc: str
    npx: str
    copilot: str
    gh: str
    vibe: str
    ollama: str


class AIFixConfigDict(TypedDict, total=False):
    """Root configuration schema."""
    ai_provider: str
    providers: ProvidersConfigDict
    linters: LintersConfigDict
    fix_strategies: FixStrategiesDict
    behavior: BehaviorConfigDict
    cache: CacheConfigDict
    binaries: BinariesConfigDict


# =============================================================================
# Output Formatting
# =============================================================================


class Colors:
    """ANSI color codes for terminal output."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'

    @staticmethod
    def disable() -> None:
        """Disable colors for non-TTY output."""
        Colors.RESET = ''
        Colors.BOLD = ''
        Colors.DIM = ''
        Colors.RED = ''
        Colors.GREEN = ''
        Colors.YELLOW = ''
        Colors.BLUE = ''
        Colors.MAGENTA = ''
        Colors.CYAN = ''
        Colors.WHITE = ''
        Colors.BG_RED = ''
        Colors.BG_GREEN = ''
        Colors.BG_YELLOW = ''


class Logger:
    """Structured logger with verbosity levels and JSON output support."""

    def __init__(
        self,
        verbose: bool = False,
        quiet: bool = False,
        json_output: bool = False,
        show_ai_output: bool = False,
    ) -> None:
        self.verbose = verbose
        self.quiet = quiet
        self.json_output = json_output
        self.show_ai_output = show_ai_output
        self._json_buffer: list[dict[str, Any]] = []

    def _print(self, message: str, force: bool = False) -> None:
        """Print message unless quiet mode is enabled."""
        if not self.quiet or force:
            print(message)

    def info(self, message: str) -> None:
        """Print info message."""
        if self.json_output:
            self._json_buffer.append({'level': 'info', 'message': message})
        else:
            self._print(f'{Colors.BLUE}ℹ{Colors.RESET} {message}')

    def success(self, message: str) -> None:
        """Print success message."""
        if self.json_output:
            self._json_buffer.append({'level': 'success', 'message': message})
        else:
            self._print(f'{Colors.GREEN}✓{Colors.RESET} {message}')

    def warning(self, message: str) -> None:
        """Print warning message."""
        if self.json_output:
            self._json_buffer.append({'level': 'warning', 'message': message})
        else:
            self._print(f'{Colors.YELLOW}⚠{Colors.RESET} {message}')

    def error(self, message: str) -> None:
        """Print error message."""
        if self.json_output:
            self._json_buffer.append({'level': 'error', 'message': message})
        else:
            self._print(f'{Colors.RED}✗{Colors.RESET} {message}', force=True)

    def debug(self, message: str) -> None:
        """Print debug message (only in verbose mode)."""
        if self.verbose:
            if self.json_output:
                self._json_buffer.append({'level': 'debug', 'message': message})
            else:
                self._print(f'{Colors.DIM}  {message}{Colors.RESET}')

    def ai_output(self, message: str) -> None:
        """Print full AI output (only when show_ai_output is enabled)."""
        if self.show_ai_output:
            if self.json_output:
                self._json_buffer.append({'level': 'ai_output', 'message': message})
            else:
                self._print(f'\n{Colors.CYAN}━━━ AI Response ━━━{Colors.RESET}')
                self._print(message)
                self._print(f'{Colors.CYAN}━━━━━━━━━━━━━━━━━━{Colors.RESET}\n')

    def header(self, message: str) -> None:
        """Print header message."""
        if self.json_output:
            self._json_buffer.append({'level': 'header', 'message': message})
        else:
            self._print(f'\n{Colors.BOLD}{message}{Colors.RESET}')

    def lint_error(self, error: 'LintError') -> None:
        """Print a lint error in a formatted way."""
        if self.json_output:
            self._json_buffer.append({
                'level': 'lint_error',
                'error': error.to_dict(),
            })
        else:
            severity_color = {
                Severity.ERROR: Colors.RED,
                Severity.WARNING: Colors.YELLOW,
                Severity.INFO: Colors.BLUE,
                Severity.HINT: Colors.DIM,
            }.get(error.severity, Colors.WHITE)

            location = f'{error.file}:{error.line}'
            if error.column:
                location += f':{error.column}'

            self._print(
                f'{severity_color}{error.severity.value}{Colors.RESET} '
                f'{Colors.CYAN}{error.linter}{Colors.RESET}:'
                f'{Colors.YELLOW}{error.code}{Colors.RESET} '
                f'{Colors.DIM}{location}{Colors.RESET}',
            )
            self._print(f'  {error.message}')
            if error.suggestion and self.verbose:
                self._print(f'  {Colors.DIM}→ {error.suggestion}{Colors.RESET}')

    def diff(self, old: str, new: str, filename: str) -> None:
        """Print a colored diff."""
        if self.json_output:
            self._json_buffer.append({
                'level': 'diff',
                'filename': filename,
                'old': old,
                'new': new,
            })
        else:
            self._print(f'\n{Colors.BOLD}--- {filename}{Colors.RESET}')
            old_lines = old.splitlines(keepends=True)
            new_lines = new.splitlines(keepends=True)

            import difflib
            diff = difflib.unified_diff(old_lines, new_lines, lineterm='')
            for line in diff:
                if line.startswith('+') and not line.startswith('+++'):
                    self._print(f'{Colors.GREEN}{line.rstrip()}{Colors.RESET}')
                elif line.startswith('-') and not line.startswith('---'):
                    self._print(f'{Colors.RED}{line.rstrip()}{Colors.RESET}')
                elif line.startswith('@@'):
                    self._print(f'{Colors.CYAN}{line.rstrip()}{Colors.RESET}')
                else:
                    self._print(line.rstrip())

    def flush_json(self) -> None:
        """Flush JSON buffer to stdout."""
        if self.json_output and self._json_buffer:
            print(json.dumps(self._json_buffer, indent=2))
            self._json_buffer = []


# Global logger instance
logger = Logger()


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class LintError:
    """Unified lint error from any linter."""
    linter: str
    file: str
    line: int
    column: int | None
    code: str
    message: str
    severity: Severity
    category: str = 'lint'
    suggestion: str | None = None
    context: str = ''
    context_start_line: int = 0
    fix_hint: str | None = None
    raw_output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            'linter': self.linter,
            'file': self.file,
            'line': self.line,
            'column': self.column,
            'code': self.code,
            'message': self.message,
            'severity': self.severity.value,
            'category': self.category,
            'suggestion': self.suggestion,
            'context': self.context,
            'fix_hint': self.fix_hint,
        }

    @property
    def location_key(self) -> str:
        """Unique key for deduplication by location."""
        return f'{self.file}:{self.line}:{self.column or 0}'

    @property
    def priority(self) -> int:
        """Get priority for sorting (lower = higher priority)."""
        return CATEGORY_PRIORITY.get(self.category, 99)

    @property
    def complexity(self) -> ErrorComplexity:
        """Classify error complexity for batching decisions."""
        # Check patterns from most specific (complex) to least (simple)
        for complexity in [ErrorComplexity.COMPLEX, ErrorComplexity.SIMPLE]:
            for pattern in COMPLEXITY_PATTERNS.get(complexity, []):
                if self.matches_pattern(pattern):
                    return complexity

        # Also check category for security
        if self.category == 'security':
            return ErrorComplexity.COMPLEX

        # Default to moderate
        return ErrorComplexity.MODERATE

    def matches_pattern(self, pattern: str) -> bool:
        """Check if error matches a pattern like 'ruff:*' or 'eslint:import/*'."""
        if pattern == '*':
            return True

        parts = pattern.split(':')
        if len(parts) == 1:
            # Just linter name
            return self.linter.lower() == parts[0].lower()

        linter_pattern, code_pattern = parts[0], ':'.join(parts[1:])

        # Check linter
        if linter_pattern != '*' and self.linter.lower() != linter_pattern.lower():
            return False

        # Check code pattern (supports wildcards)
        if code_pattern == '*':
            return True

        import fnmatch
        return fnmatch.fnmatch(self.code.lower(), code_pattern.lower())


@dataclass
class FixAttempt:
    """Record of a fix attempt."""
    error: LintError
    original_content: str
    fixed_content: str | None
    result: FixResult
    explanation: str = ''
    provider: str = ''
    duration_ms: int = 0


@dataclass
class IterationProgress:
    """Track progress across fix iterations."""
    iteration: int = 0
    total_found: int = 0
    total_fixed: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    errors_by_complexity: dict[ErrorComplexity, int] = field(default_factory=dict)

    def add_batch_result(self, fixed: int, failed: int, skipped: int) -> None:
        """Add results from a batch."""
        self.total_fixed += fixed
        self.total_failed += failed
        self.total_skipped += skipped

    def should_continue(self, max_iterations: int) -> bool:
        """Check if we should continue iterating."""
        if self.iteration >= max_iterations:
            return False
        # Continue if we fixed something last iteration (might have revealed new issues)
        # or if there are still errors we haven't tried to fix
        return True

    def summary(self) -> str:
        """Get a summary string."""
        return (
            f'Iteration {self.iteration}: '
            f'{self.total_fixed} fixed, {self.total_failed} failed, '
            f'{self.total_skipped} skipped'
        )


@dataclass
class ProviderConfig:
    """Runtime configuration for an AI provider."""
    enabled: bool = True
    api_key_env: str = ''
    model: str = ''  # Default model (overrides smart selection if set)
    model_simple: str = ''  # Model for simple errors
    model_moderate: str = ''  # Model for moderate errors
    model_complex: str = ''  # Model for complex errors
    smart_model_selection: bool = True  # Use different models by complexity
    host: str = ''
    timeout: int = 120  # Increased default timeout

    @classmethod
    def from_dict(cls, data: ProviderConfigDict) -> ProviderConfig:
        """Create from dictionary."""
        return cls(
            enabled=data.get('enabled', True),
            api_key_env=data.get('api_key_env', ''),
            model=data.get('model', ''),
            model_simple=data.get('model_simple', ''),
            model_moderate=data.get('model_moderate', ''),
            model_complex=data.get('model_complex', ''),
            smart_model_selection=data.get('smart_model_selection', True),
            host=data.get('host', ''),
            timeout=data.get('timeout', 120),
        )

    def get_model_for_complexity(
        self, provider_name: str, complexity: ErrorComplexity,
    ) -> str:
        """Get the best model for the given error complexity."""
        # User-specified model always takes precedence
        if self.model:
            return self.model

        # Check complexity-specific model settings
        if complexity == ErrorComplexity.SIMPLE and self.model_simple:
            return self.model_simple
        if complexity == ErrorComplexity.MODERATE and self.model_moderate:
            return self.model_moderate
        if complexity == ErrorComplexity.COMPLEX and self.model_complex:
            return self.model_complex

        # Smart model selection based on complexity
        if self.smart_model_selection and provider_name in MODEL_DEFAULTS_BY_COMPLEXITY:
            return MODEL_DEFAULTS_BY_COMPLEXITY[provider_name].get(
                complexity, DEFAULT_MODELS.get(provider_name, ''),
            )

        # Fall back to default model for provider
        return DEFAULT_MODELS.get(provider_name, '')


@dataclass
class LinterRuntimeConfig:
    """Runtime configuration for a linter."""
    enabled: bool = True
    auto_detect: bool = True
    command: str = ''
    args: list[str] = field(default_factory=list)
    fix_args: list[str] = field(default_factory=list)
    json_output: bool = True

    @classmethod
    def from_dict(cls, data: LinterConfigDict) -> LinterRuntimeConfig:
        """Create from dictionary."""
        return cls(
            enabled=data.get('enabled', True),
            auto_detect=data.get('auto_detect', True),
            command=data.get('command', ''),
            args=data.get('args', []),
            fix_args=data.get('fix_args', []),
            json_output=data.get('json_output', True),
        )


@dataclass
class BehaviorConfig:
    """Runtime behavior configuration."""
    batch_by_file: bool = True
    dedupe_errors: bool = True
    priority_order: list[str] = field(
        default_factory=lambda: ['security', 'type', 'lint', 'style'],
    )
    max_errors_per_file: int = 50
    max_total_errors: int = 200
    context_lines: int = 5
    validate_fixes: bool = True
    max_fix_iterations: int = 3
    # Batching settings
    batch_size_simple: int = 1      # Simple issues: one at a time (default)
    batch_size_moderate: int = 1    # Moderate issues: one at a time (default)
    batch_size_complex: int = 1     # Complex issues: one at a time
    rerun_after_batch: bool = True  # Re-run linters after each batch

    @classmethod
    def from_dict(cls, data: BehaviorConfigDict) -> BehaviorConfig:
        """Create from dictionary."""
        return cls(
            batch_by_file=data.get('batch_by_file', True),
            dedupe_errors=data.get('dedupe_errors', True),
            priority_order=data.get(
                'priority_order', ['security', 'type', 'lint', 'style'],
            ),
            max_errors_per_file=data.get('max_errors_per_file', 50),
            max_total_errors=data.get('max_total_errors', 200),
            context_lines=data.get('context_lines', 5),
            validate_fixes=data.get('validate_fixes', True),
            max_fix_iterations=data.get('max_fix_iterations', 3),
            batch_size_simple=data.get('batch_size_simple', 1),
            batch_size_moderate=data.get('batch_size_moderate', 1),
            batch_size_complex=data.get('batch_size_complex', 1),
            rerun_after_batch=data.get('rerun_after_batch', True),
        )


@dataclass
class CacheConfig:
    """Runtime cache configuration."""
    enabled: bool = True
    cache_dir: str = '.ai-fix-cache'
    ttl_hours: int = 168  # 1 week

    @classmethod
    def from_dict(cls, data: CacheConfigDict) -> CacheConfig:
        """Create from dictionary."""
        return cls(
            enabled=data.get('enabled', True),
            cache_dir=data.get('cache_dir', '.ai-fix-cache'),
            ttl_hours=data.get('ttl_hours', 168),
        )


# Default binary paths
DEFAULT_BINARIES: dict[str, str] = {
    'git': 'git',
    'ruff': 'ruff',
    'mypy': 'mypy',
    'eslint': 'eslint',
    'pylint': 'pylint',
    'tsc': 'tsc',
    'npx': 'npx',
    'copilot': 'copilot',
    'gh': 'gh',
    'vibe': 'vibe',
    'ollama': 'ollama',
}

# Global config for binary path lookups
_global_aifix_config: AIFixConfig | None = None


def set_global_aifix_config(config: AIFixConfig) -> None:
    """Set the global AI fix config instance."""
    global _global_aifix_config
    _global_aifix_config = config


def get_binary(name: str) -> str:
    """Get the configured path for a binary.

    Args:
        name: Binary name (git, ruff, mypy, eslint, pylint, tsc, npx, copilot, gh, vibe, ollama)

    Returns:
        Configured path or default binary name
    """
    if _global_aifix_config:
        return _global_aifix_config.binaries.get(name, DEFAULT_BINARIES.get(name, name))
    return DEFAULT_BINARIES.get(name, name)


def is_binary_available(name: str) -> bool:
    """Check if a binary is available at the configured path.

    Args:
        name: Binary name to check

    Returns:
        True if binary is available
    """
    binary_path = get_binary(name)
    # If it's an absolute path, check if it exists and is executable
    if os.path.isabs(binary_path):
        return os.path.isfile(binary_path) and os.access(binary_path, os.X_OK)
    # Otherwise use shutil.which
    return shutil.which(binary_path) is not None


@dataclass
class AIFixConfig:
    """Complete runtime configuration."""
    ai_provider: str = 'copilot-cli'
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    linters: dict[str, LinterRuntimeConfig] = field(default_factory=dict)
    auto_fix_patterns: list[str] = field(default_factory=list)
    prompt_fix_patterns: list[str] = field(default_factory=list)
    never_fix_patterns: list[str] = field(default_factory=list)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    binaries: dict[str, str] = field(default_factory=lambda: DEFAULT_BINARIES.copy())
    root_dir: Path = field(default_factory=Path.cwd)

    @classmethod
    def from_dict(cls, data: AIFixConfigDict, root_dir: Path | None = None) -> AIFixConfig:
        """Create from dictionary with defaults."""
        root = root_dir or Path.cwd()

        # Parse providers
        providers: dict[str, ProviderConfig] = {}
        providers_data = data.get('providers', {})
        for name in ['copilot-cli', 'mistral', 'ollama']:
            key = name.replace('-', '_')
            if key in providers_data:
                providers[name] = ProviderConfig.from_dict(providers_data[key])  # type: ignore[literal-required]
            else:
                providers[name] = ProviderConfig()

        # Parse linters
        linters: dict[str, LinterRuntimeConfig] = {}
        linters_data = data.get('linters', {})
        for name in ['ruff', 'mypy', 'eslint', 'pylint', 'tsc']:
            if name in linters_data:
                linters[name] = LinterRuntimeConfig.from_dict(linters_data[name])  # type: ignore[literal-required]
            else:
                linters[name] = LinterRuntimeConfig()

        # Parse fix strategies
        strategies = data.get('fix_strategies', {})

        # Parse custom binary paths
        binaries = DEFAULT_BINARIES.copy()
        if 'binaries' in data:
            binaries.update(data['binaries'])  # type: ignore[arg-type]

        return cls(
            ai_provider=data.get('ai_provider', 'copilot-cli'),
            providers=providers,
            linters=linters,
            auto_fix_patterns=strategies.get('auto_fix', []),
            prompt_fix_patterns=strategies.get('prompt_fix', ['*']),
            never_fix_patterns=strategies.get('never_fix', ['security:*']),
            behavior=BehaviorConfig.from_dict(data.get('behavior', {})),
            cache=CacheConfig.from_dict(data.get('cache', {})),
            binaries=binaries,
            root_dir=root,
        )


# =============================================================================
# Linter Parsers
# =============================================================================


class LinterParser(ABC):
    """Base class for linter output parsers."""

    name: str = ''

    @abstractmethod
    def parse(self, output: str, files: list[str] | None = None) -> list[LintError]:
        """Parse linter output into LintError objects."""

    @abstractmethod
    def get_command(self, files: list[str], config: LinterRuntimeConfig) -> list[str]:
        """Get command to run linter."""

    def is_available(self) -> bool:
        """Check if linter is available at the configured path."""
        return is_binary_available(self.name)


class RuffParser(LinterParser):
    """Parser for Ruff linter output."""

    name = 'ruff'

    def parse(self, output: str, files: list[str] | None = None) -> list[LintError]:
        """Parse Ruff JSON output."""
        errors: list[LintError] = []
        if not output.strip():
            return errors

        try:
            data = json.loads(output)
            for item in data:
                severity = Severity.ERROR
                if item.get('fix') is not None:
                    severity = Severity.WARNING  # Fixable issues are warnings

                errors.append(LintError(
                    linter='ruff',
                    file=item.get('filename', ''),
                    line=item.get('location', {}).get('row', 0),
                    column=item.get('location', {}).get('column'),
                    code=item.get('code', ''),
                    message=item.get('message', ''),
                    severity=severity,
                    category=self._categorize(item.get('code', '')),
                    suggestion=item.get('fix', {}).get('message') if item.get('fix') else None,
                    fix_hint=item.get('fix', {}).get('edits') if item.get('fix') else None,
                    raw_output=item,
                ))
        except json.JSONDecodeError:
            logger.debug(f'Failed to parse Ruff JSON output: {output[:200]}')

        return errors

    def _categorize(self, code: str) -> str:
        """Categorize Ruff error code."""
        if code.startswith(('S', 'B')):  # Security, Bugbear
            return 'security'
        if code.startswith(('E', 'W', 'F')):  # Errors, Warnings, Pyflakes
            return 'lint'
        if code.startswith(('I', 'UP')):  # isort, pyupgrade
            return 'style'
        return 'lint'

    def get_command(self, files: list[str], config: LinterRuntimeConfig) -> list[str]:
        """Get Ruff command."""
        cmd = [config.command or get_binary('ruff')]
        args = config.args or ['check', '--output-format=json']
        cmd.extend(args)
        cmd.extend(files)
        return cmd


class MypyParser(LinterParser):
    """Parser for mypy output."""

    name = 'mypy'

    def parse(self, output: str, files: list[str] | None = None) -> list[LintError]:
        """Parse mypy output (JSON or text)."""
        errors: list[LintError] = []
        if not output.strip():
            return errors

        # Try JSON first
        try:
            for line in output.strip().split('\n'):
                if not line.strip():
                    continue
                data = json.loads(line)
                errors.append(LintError(
                    linter='mypy',
                    file=data.get('file', ''),
                    line=data.get('line', 0),
                    column=data.get('column'),
                    code=data.get('code', 'error'),
                    message=data.get('message', ''),
                    severity=self._parse_severity(data.get('severity', 'error')),
                    category='type',
                    raw_output=data,
                ))
            return errors
        except json.JSONDecodeError:
            pass

        # Fall back to text parsing
        pattern = re.compile(
            r'^(?P<file>[^:]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*'
            r'(?P<severity>error|warning|note):\s*(?P<message>.+?)(?:\s+\[(?P<code>[^\]]+)\])?$',
        )

        for line in output.strip().split('\n'):
            match = pattern.match(line.strip())
            if match:
                errors.append(LintError(
                    linter='mypy',
                    file=match.group('file'),
                    line=int(match.group('line')),
                    column=int(match.group('col')) if match.group('col') else None,
                    code=match.group('code') or 'error',
                    message=match.group('message'),
                    severity=self._parse_severity(match.group('severity')),
                    category='type',
                ))

        return errors

    def _parse_severity(self, severity: str) -> Severity:
        """Parse severity string."""
        return {
            'error': Severity.ERROR,
            'warning': Severity.WARNING,
            'note': Severity.INFO,
        }.get(severity.lower(), Severity.ERROR)

    def get_command(self, files: list[str], config: LinterRuntimeConfig) -> list[str]:
        """Get mypy command."""
        cmd = [config.command or get_binary('mypy')]
        args = config.args or []
        cmd.extend(args)
        cmd.extend(files)
        return cmd


class ESLintParser(LinterParser):
    """Parser for ESLint output."""

    name = 'eslint'

    def parse(self, output: str, files: list[str] | None = None) -> list[LintError]:
        """Parse ESLint JSON output."""
        errors: list[LintError] = []
        if not output.strip():
            return errors

        try:
            data = json.loads(output)
            for file_result in data:
                filepath = file_result.get('filePath', '')
                for msg in file_result.get('messages', []):
                    errors.append(LintError(
                        linter='eslint',
                        file=filepath,
                        line=msg.get('line', 0),
                        column=msg.get('column'),
                        code=msg.get('ruleId', 'parse-error') or 'parse-error',
                        message=msg.get('message', ''),
                        severity=Severity.ERROR if msg.get('severity') == 2 else Severity.WARNING,
                        category=self._categorize(msg.get('ruleId', '')),
                        suggestion=msg.get('suggestions', [{}])[0].get('desc') if msg.get('suggestions') else None,
                        raw_output=msg,
                    ))
        except json.JSONDecodeError:
            logger.debug(f'Failed to parse ESLint JSON output: {output[:200]}')

        return errors

    def _categorize(self, rule_id: str) -> str:
        """Categorize ESLint rule."""
        if not rule_id:
            return 'lint'
        if 'security' in rule_id or rule_id.startswith('security/'):
            return 'security'
        if rule_id.startswith(('import/', '@typescript-eslint/type')):
            return 'type'
        if rule_id.startswith(('prettier/', 'format')):
            return 'format'
        return 'lint'

    def get_command(self, files: list[str], config: LinterRuntimeConfig) -> list[str]:
        """Get ESLint command."""
        # Check for direct eslint command or use npx
        if config.command:
            cmd = [config.command]
        else:
            cmd = [get_binary('npx'), 'eslint']
        args = config.args or ['--format=json']
        cmd.extend(args)
        cmd.extend(files)
        return cmd


class PylintParser(LinterParser):
    """Parser for Pylint output."""

    name = 'pylint'

    def parse(self, output: str, files: list[str] | None = None) -> list[LintError]:
        """Parse Pylint JSON output."""
        errors: list[LintError] = []
        if not output.strip():
            return errors

        try:
            data = json.loads(output)
            for item in data:
                errors.append(LintError(
                    linter='pylint',
                    file=item.get('path', ''),
                    line=item.get('line', 0),
                    column=item.get('column'),
                    code=item.get('message-id', ''),
                    message=item.get('message', ''),
                    severity=self._parse_severity(item.get('type', 'error')),
                    category=self._categorize(item.get('type', '')),
                    raw_output=item,
                ))
        except json.JSONDecodeError:
            logger.debug(f'Failed to parse Pylint JSON output: {output[:200]}')

        return errors

    def _parse_severity(self, type_str: str) -> Severity:
        """Parse Pylint message type to severity."""
        return {
            'error': Severity.ERROR,
            'warning': Severity.WARNING,
            'convention': Severity.INFO,
            'refactor': Severity.INFO,
            'fatal': Severity.ERROR,
        }.get(type_str.lower(), Severity.WARNING)

    def _categorize(self, type_str: str) -> str:
        """Categorize Pylint message."""
        if type_str in ('error', 'fatal'):
            return 'error'
        if type_str == 'warning':
            return 'lint'
        return 'style'

    def get_command(self, files: list[str], config: LinterRuntimeConfig) -> list[str]:
        """Get Pylint command."""
        cmd = [config.command or get_binary('pylint')]
        args = config.args or ['--output-format=json']
        cmd.extend(args)
        cmd.extend(files)
        return cmd


class TypeScriptParser(LinterParser):
    """Parser for TypeScript compiler output."""

    name = 'tsc'

    def parse(self, output: str, files: list[str] | None = None) -> list[LintError]:
        """Parse tsc output."""
        errors: list[LintError] = []
        if not output.strip():
            return errors

        # Pattern: file.ts(line,col): error TS2345: message
        pattern = re.compile(
            r'^(?P<file>[^(]+)\((?P<line>\d+),(?P<col>\d+)\):\s*'
            r'(?P<severity>error|warning)\s+(?P<code>TS\d+):\s*(?P<message>.+)$',
        )

        for line in output.strip().split('\n'):
            match = pattern.match(line.strip())
            if match:
                errors.append(LintError(
                    linter='tsc',
                    file=match.group('file'),
                    line=int(match.group('line')),
                    column=int(match.group('col')),
                    code=match.group('code'),
                    message=match.group('message'),
                    severity=Severity.ERROR if match.group('severity') == 'error' else Severity.WARNING,
                    category='type',
                ))

        return errors

    def get_command(self, files: list[str], config: LinterRuntimeConfig) -> list[str]:
        """Get tsc command."""
        if config.command:
            cmd = [config.command]
        else:
            cmd = [get_binary('npx'), 'tsc']
        args = config.args or ['--noEmit']
        cmd.extend(args)
        # tsc doesn't take file arguments when checking the project
        return cmd


# Registry of parsers
LINTER_PARSERS: dict[str, type[LinterParser]] = {
    'ruff': RuffParser,
    'mypy': MypyParser,
    'eslint': ESLintParser,
    'pylint': PylintParser,
    'tsc': TypeScriptParser,
}


# =============================================================================
# AI Providers
# =============================================================================


class AIProviderBase(ABC):
    """Base class for AI providers."""

    name: str = ''

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    def generate_fix(
        self,
        error: LintError,
        file_content: str,
        context: str,
        complexity: ErrorComplexity | None = None,
    ) -> tuple[str | None, str]:
        """
        Generate a fix for the given error.

        Args:
            error: The lint error to fix
            file_content: Full content of the file
            context: Code context around the error
            complexity: Error complexity for model selection

        Returns:
            Tuple of (fixed_content or None, explanation)
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is available."""

    def get_model(self, complexity: ErrorComplexity | None = None) -> str:
        """Get the model to use based on complexity."""
        if complexity is None:
            complexity = ErrorComplexity.MODERATE
        return self.config.get_model_for_complexity(self.name, complexity)

    def fix_file_directly(
        self,
        filepath: Path,
        errors: list[LintError],
        complexity: ErrorComplexity | None = None,
    ) -> tuple[bool, str]:
        """Have the AI directly edit the file to fix errors.

        This is an optional method that providers can implement if they
        support direct file editing. The default implementation returns False.

        Args:
            filepath: Path to the file to fix
            errors: List of lint errors to fix
            complexity: Error complexity for model selection

        Returns:
            tuple[bool, str]: (success, explanation)
        """
        return False, 'Direct file editing not supported by this provider'

    def supports_direct_edit(self) -> bool:
        """Check if this provider supports direct file editing."""
        return False

    def build_prompt(self, error: LintError, file_content: str, context: str) -> str:
        """Build a prompt for the AI."""
        return f"""Fix the following linting error in the code.

**Error:**
- Linter: {error.linter}
- Code: {error.code}
- Message: {error.message}
- File: {error.file}
- Line: {error.line}
{f'- Suggestion: {error.suggestion}' if error.suggestion else ''}

**Code context around line {error.line}:**
```
{context}
```

**Instructions:**
1. Fix ONLY the specific error mentioned above
2. Make minimal changes - do not refactor or change unrelated code
3. Preserve the original code style and formatting
4. IMPORTANT: Return EXACTLY the same code block shown above, with ONLY the error fixed
5. Do NOT add extra functions, imports, or code not in the original context
6. Do NOT return partial snippets - return the COMPLETE context block with the fix applied

**Fixed code (return the complete fixed context block):**
```
"""


class CopilotCLIProvider(AIProviderBase):
    """GitHub Copilot CLI provider (new copilot-cli tool).

    Uses the new GitHub Copilot CLI (https://github.com/github/copilot-cli)
    which can be installed via:
        brew install copilot-cli
        npm install -g @github/copilot

    Available models: claude-haiku-4.5, claude-sonnet-4, claude-sonnet-4.5,
    claude-opus-4.5, gpt-4.1, gpt-5-mini, gpt-5, gpt-5.1, gpt-5.2,
    gpt-5.1-codex, gpt-5.1-codex-mini, gpt-5.1-codex-max, gpt-5.2-codex,
    gemini-3-pro-preview

    Falls back to the legacy 'gh copilot' if new CLI is not available.
    """

    name = 'copilot-cli'

    def is_available(self) -> bool:
        """Check if Copilot CLI is available at configured path."""
        # Prefer new copilot CLI, fall back to gh copilot
        return is_binary_available('copilot') or is_binary_available('gh')

    def supports_direct_edit(self) -> bool:
        """Copilot CLI supports direct file editing via its tools."""
        return is_binary_available('copilot')

    def _get_copilot_command(self) -> list[str]:
        """Get the appropriate copilot command."""
        if is_binary_available('copilot'):
            return [get_binary('copilot')]
        return [get_binary('gh'), 'copilot']

    def generate_fix(
        self,
        error: LintError,
        file_content: str,
        context: str,
        complexity: ErrorComplexity | None = None,
    ) -> tuple[str | None, str]:
        """Generate fix using Copilot CLI."""
        prompt = self._build_fix_prompt(error, context)
        model = self.get_model(complexity)

        # Try new copilot CLI first (with --prompt for non-interactive mode)
        if is_binary_available('copilot'):
            return self._run_new_copilot(prompt, error, model)

        # Fall back to legacy gh copilot
        return self._run_legacy_copilot(prompt)

    def fix_file_directly(
        self,
        filepath: Path,
        errors: list[LintError],
        complexity: ErrorComplexity | None = None,
    ) -> tuple[bool, str]:
        """Have Copilot CLI directly edit the file to fix errors.

        This method tells Copilot to use its file editing tools directly,
        so we don't need to parse and apply fixes ourselves.

        Returns:
            tuple[bool, str]: (success, explanation)
        """
        if not is_binary_available('copilot'):
            return False, 'Copilot CLI not available'

        # Build a prompt that tells Copilot to fix the file directly
        error_list = '\n'.join(
            f'  - Line {e.line}: [{e.linter}:{e.code}] {e.message}'
            for e in errors
        )

        prompt = f"""Fix the following linting errors in file {filepath}:

{error_list}

Instructions:
1. Read the file at {filepath}
2. Fix ONLY the specific errors listed above
3. Make minimal changes - do not refactor or change unrelated code
4. Write the fixed content back to the file
5. Do NOT add any new functions, imports, or code that wasn't there before
6. Preserve the original code style and formatting"""

        model = self.get_model(complexity)
        copilot_bin = get_binary('copilot')

        # Run with --allow-all so Copilot can edit files directly
        cmd = [
            copilot_bin,
            '-p', prompt,
            '--allow-all',  # Allow file read/write
            '--no-ask-user',
            '--model', model,
            '--add-dir', str(filepath.parent),  # Allow access to file's directory
        ]

        logger.debug(f'Running Copilot CLI direct fix with model: {model}')

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout or 180,
                cwd=filepath.parent,
            )

            logger.ai_output(result.stdout)

            if result.returncode == 0:
                # Check if the output indicates success
                output = result.stdout.lower()
                if any(word in output for word in ['fixed', 'updated', 'wrote', 'saved', 'modified']):
                    return True, f'Fixed by Copilot CLI ({model})'
                # Even if no explicit success message, check if file was modified
                return True, f'Copilot CLI completed ({model})'
            else:
                error_msg = result.stderr or result.stdout
                return False, f'Copilot CLI failed: {error_msg[:200]}'

        except subprocess.TimeoutExpired:
            return False, 'Copilot CLI timed out'
        except Exception as e:
            return False, f'Copilot CLI error: {e}'

    def _build_fix_prompt(self, error: LintError, context: str) -> str:
        """Build a concise prompt for the fix."""
        return f"""Fix this {error.linter} error ({error.code}): {error.message}

File: {error.file}, Line: {error.line}

Code:
```
{context}
```

Return ONLY the fixed code, no explanation."""

    def _run_new_copilot(
        self,
        prompt: str,
        error: LintError,
        model: str,
    ) -> tuple[str | None, str]:
        """Run the new copilot CLI with -p/--prompt flag (non-interactive)."""
        try:
            # Use -p/--prompt for non-interactive mode (exits after completion)
            # Use --allow-all for all permissions (tools, paths, urls)
            # Use --no-ask-user for autonomous operation
            copilot_bin = get_binary('copilot')
            cmd = [
                copilot_bin,
                '-p', prompt,  # Non-interactive mode, exits after completion
                '--allow-all',  # Enable all permissions (tools, paths, urls)
                '--no-ask-user',  # Don't ask questions, work autonomously
                '--model', model,
            ]

            logger.debug(f'Using Copilot CLI with model: {model}')

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout or 120,
                cwd=Path(error.file).parent if Path(error.file).exists() else None,
            )

            if result.returncode == 0:
                # Show full AI output if requested
                logger.ai_output(result.stdout)
                
                fixed_code = self._extract_code(result.stdout)
                if fixed_code:
                    return fixed_code, f'Fix by Copilot CLI ({model})'
                return None, 'Copilot CLI returned empty response'
            else:
                error_msg = result.stderr or result.stdout
                logger.debug(f'Copilot CLI error: {error_msg}')
                return None, f'Copilot CLI failed: {error_msg[:200]}'

        except subprocess.TimeoutExpired:
            return None, 'Copilot CLI timed out'
        except Exception as e:
            return None, f'Copilot CLI error: {e}'

    def _run_legacy_copilot(self, prompt: str) -> tuple[str | None, str]:
        """Run legacy gh copilot suggest."""
        try:
            gh_bin = get_binary('gh')
            result = subprocess.run(
                [gh_bin, 'copilot', 'suggest', prompt],
                capture_output=True,
                text=True,
                timeout=self.config.timeout or 30,
                input='',
            )

            if result.returncode == 0:
                fixed_code = self._extract_code(result.stdout)
                return fixed_code, 'Fix suggested by GitHub Copilot (legacy)'
            else:
                logger.debug(f'gh copilot error: {result.stderr}')
                return None, f'gh copilot failed: {result.stderr[:200]}'

        except subprocess.TimeoutExpired:
            return None, 'gh copilot timed out'
        except Exception as e:
            return None, f'gh copilot error: {e}'

    def _extract_code(self, output: str) -> str | None:
        """Extract code from Copilot output."""
        # Look for code blocks
        code_match = re.search(r'```(?:\w+)?\n(.*?)```', output, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()

        # Try to find code after common patterns
        for pattern in [r'Fixed code:\s*\n(.*)', r'Here.*:\s*\n(.*)']:
            match = re.search(pattern, output, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()

        # Return non-empty output as-is if it looks like code
        stripped = output.strip()
        if stripped and not stripped.startswith(('I ', 'The ', 'This ', 'Here')):
            return stripped
        return None


class MistralProvider(AIProviderBase):
    """Mistral AI provider (via API).

    Available models: mistral-small-latest, mistral-medium-latest,
    mistral-large-latest, codestral-latest, open-codestral-mamba

    Requires MISTRAL_API_KEY environment variable.
    """

    name = 'mistral'

    def is_available(self) -> bool:
        """Check if Mistral API key is available."""
        api_key_env = self.config.api_key_env or 'MISTRAL_API_KEY'
        return bool(os.environ.get(api_key_env))

    def generate_fix(
        self,
        error: LintError,
        file_content: str,
        context: str,
        complexity: ErrorComplexity | None = None,
    ) -> tuple[str | None, str]:
        """Generate fix using Mistral API."""
        api_key_env = self.config.api_key_env or 'MISTRAL_API_KEY'
        api_key = os.environ.get(api_key_env)

        if not api_key:
            return None, f'Mistral API key not found in {api_key_env}'

        prompt = self.build_prompt(error, file_content, context)
        model = self.get_model(complexity)

        logger.debug(f'Using Mistral API with model: {model}')

        try:
            import urllib.request

            data = json.dumps({
                'model': model,
                'messages': [
                    {'role': 'system', 'content': 'You are a code fixing assistant. Return only the fixed code, no explanations.'},
                    {'role': 'user', 'content': prompt},
                ],
                'temperature': 0.1,
                'max_tokens': 1000,
            }).encode('utf-8')

            req = urllib.request.Request(
                'https://api.mistral.ai/v1/chat/completions',
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {api_key}',
                },
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
                content = result['choices'][0]['message']['content']
                fixed_code = self._extract_code(content)
                return fixed_code, f'Fix by Mistral ({model})'

        except Exception as e:
            return None, f'Mistral API error: {e}'

    def _extract_code(self, output: str) -> str | None:
        """Extract code from Mistral output."""
        code_match = re.search(r'```(?:\w+)?\n(.*?)```', output, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()
        return output.strip() if output.strip() else None


class OllamaProvider(AIProviderBase):
    """Ollama local AI provider.

    Available models: codellama:7b, codellama:13b, codellama:34b,
    deepseek-coder:6.7b, deepseek-coder:33b, qwen2.5-coder:7b,
    qwen2.5-coder:32b, llama3.2:3b, llama3.3:70b

    Requires Ollama running locally (ollama serve).
    """

    name = 'ollama'

    def is_available(self) -> bool:
        """Check if Ollama is available."""
        host = self.config.host or os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
        try:
            import urllib.request
            req = urllib.request.Request(f'{host}/api/tags')
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status == 200
        except Exception:
            return False

    def generate_fix(
        self,
        error: LintError,
        file_content: str,
        context: str,
        complexity: ErrorComplexity | None = None,
    ) -> tuple[str | None, str]:
        """Generate fix using Ollama."""
        host = self.config.host or os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
        model = self.get_model(complexity)
        prompt = self.build_prompt(error, file_content, context)

        logger.debug(f'Using Ollama with model: {model}')

        try:
            import urllib.request

            data = json.dumps({
                'model': model,
                'prompt': prompt,
                'stream': False,
                'options': {
                    'temperature': 0.1,
                    'num_predict': 1000,
                },
            }).encode('utf-8')

            req = urllib.request.Request(
                f'{host}/api/generate',
                data=data,
                headers={'Content-Type': 'application/json'},
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
                content = result.get('response', '')
                fixed_code = self._extract_code(content)
                return fixed_code, f'Fix by Ollama ({model})'

        except Exception as e:
            return None, f'Ollama error: {e}'

    def _extract_code(self, output: str) -> str | None:
        """Extract code from Ollama output."""
        code_match = re.search(r'```(?:\w+)?\n(.*?)```', output, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()
        # If no code block, assume the entire output is code
        lines = output.strip().split('\n')
        # Filter out explanation lines
        code_lines = [line for line in lines if not line.startswith(('#', '//', 'Note:', 'Fix:'))]
        return '\n'.join(code_lines) if code_lines else None


class VibeProvider(AIProviderBase):
    """Mistral Vibe CLI provider.

    Uses the Mistral Vibe CLI (https://github.com/mistralai/vibe)
    which can be installed via:
        brew install mistralai/tap/vibe

    Available models: mistral-small-latest, mistral-medium-latest,
    mistral-large-latest, codestral-latest, open-codestral-mamba

    Requires MISTRAL_API_KEY environment variable.
    """

    name = 'vibe'

    def is_available(self) -> bool:
        """Check if Vibe CLI is available at configured path."""
        return is_binary_available('vibe') and bool(
            os.environ.get('MISTRAL_API_KEY')
        )

    def generate_fix(
        self,
        error: LintError,
        file_content: str,
        context: str,
        complexity: ErrorComplexity | None = None,
    ) -> tuple[str | None, str]:
        """Generate fix using Vibe CLI."""
        prompt = self._build_fix_prompt(error, context)
        model = self.get_model(complexity)

        try:
            # Use -p/--prompt for non-interactive mode
            # Use --yolo to auto-approve all actions
            vibe_bin = get_binary('vibe')
            cmd = [
                vibe_bin,
                '-p', prompt,  # Non-interactive prompt
                '--yolo',  # Auto-approve all actions
                '--model', model,
            ]

            logger.debug(f'Using Vibe CLI with model: {model}')

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout or 120,
                cwd=Path(error.file).parent if Path(error.file).exists() else None,
            )

            if result.returncode == 0:
                # Show full AI output if requested
                logger.ai_output(result.stdout)
                
                fixed_code = self._extract_code(result.stdout)
                if fixed_code:
                    return fixed_code, f'Fix by Vibe CLI ({model})'
                return None, 'Vibe CLI returned empty response'
            else:
                error_msg = result.stderr or result.stdout
                logger.debug(f'Vibe CLI error: {error_msg}')
                return None, f'Vibe CLI failed: {error_msg[:200]}'

        except subprocess.TimeoutExpired:
            return None, 'Vibe CLI timed out'
        except Exception as e:
            return None, f'Vibe CLI error: {e}'

    def _build_fix_prompt(self, error: LintError, context: str) -> str:
        """Build a concise prompt for the fix."""
        return f"""Fix this {error.linter} error ({error.code}): {error.message}

File: {error.file}, Line: {error.line}

Code:
```
{context}
```

Return ONLY the fixed code, no explanation."""

    def supports_direct_edit(self) -> bool:
        """Vibe CLI supports direct file editing via its tools."""
        return is_binary_available('vibe')

    def fix_file_directly(
        self,
        filepath: Path,
        errors: list[LintError],
        complexity: ErrorComplexity | None = None,
    ) -> tuple[bool, str]:
        """Have Vibe CLI directly edit the file to fix errors."""
        if not is_binary_available('vibe'):
            return False, 'Vibe CLI not available'

        # Build a prompt that tells Vibe to fix the file directly
        error_list = '\n'.join(
            f'  - Line {e.line}: [{e.linter}:{e.code}] {e.message}'
            for e in errors
        )

        prompt = f"""Fix the following linting errors in file {filepath}:

{error_list}

Instructions:
1. Read the file at {filepath}
2. Fix ONLY the specific errors listed above
3. Make minimal changes - do not refactor or change unrelated code
4. Write the fixed content back to the file
5. Do NOT add any new functions, imports, or code that wasn't there before
6. Preserve the original code style and formatting"""

        model = self.get_model(complexity)
        vibe_bin = get_binary('vibe')

        # Run with -p for programmatic mode (auto-approves all tools)
        cmd = [
            vibe_bin,
            '-p', prompt,  # Programmatic mode with auto-approve
        ]

        logger.debug(f'Running Vibe CLI direct fix with model: {model}')

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout or 180,
                cwd=filepath.parent,
            )

            logger.ai_output(result.stdout)

            if result.returncode == 0:
                output = result.stdout.lower()
                if any(word in output for word in ['fixed', 'updated', 'wrote', 'saved', 'modified']):
                    return True, f'Fixed by Vibe CLI ({model})'
                return True, f'Vibe CLI completed ({model})'
            else:
                error_msg = result.stderr or result.stdout
                return False, f'Vibe CLI failed: {error_msg[:200]}'

        except subprocess.TimeoutExpired:
            return False, 'Vibe CLI timed out'
        except Exception as e:
            return False, f'Vibe CLI error: {e}'

    def _extract_code(self, output: str) -> str | None:
        """Extract code from Vibe output."""
        # Look for code blocks
        code_match = re.search(r'```(?:\w+)?\n(.*?)```', output, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()

        # Try to find code after common patterns
        for pattern in [r'Fixed code:\s*\n(.*)', r'Here.*:\s*\n(.*)']:
            match = re.search(pattern, output, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()

        # Return non-empty output as-is if it looks like code
        stripped = output.strip()
        if stripped and not stripped.startswith(('I ', 'The ', 'This ', 'Here')):
            return stripped
        return None


class MockProvider(AIProviderBase):
    """Mock provider for testing."""

    name = 'mock'

    def is_available(self) -> bool:
        """Always available."""
        return True

    def generate_fix(
        self,
        error: LintError,
        file_content: str,
        context: str,
        complexity: ErrorComplexity | None = None,
    ) -> tuple[str | None, str]:
        """Return mock fix."""
        return context, 'Mock fix for testing'


# Registry of providers
AI_PROVIDERS: dict[str, type[AIProviderBase]] = {
    'copilot-cli': CopilotCLIProvider,
    'vibe': VibeProvider,
    'mistral': MistralProvider,
    'ollama': OllamaProvider,
    'mock': MockProvider,
}


# =============================================================================
# Core Functions
# =============================================================================


def load_config_file(config_path: Path | None = None) -> AIFixConfigDict:
    """Load configuration from file."""
    if config_path:
        paths = [config_path]
    else:
        paths = [Path(name) for name in CONFIG_FILE_NAMES]

    for path in paths:
        if path.exists():
            logger.debug(f'Loading config from {path}')
            with open(path) as f:
                if path.suffix in ('.yaml', '.yml'):
                    return cast(AIFixConfigDict, yaml.safe_load(f) or {})
                else:
                    return cast(AIFixConfigDict, json.load(f))

    return {}


def load_env_config() -> AIFixConfigDict:
    """Load configuration from environment variables."""
    config: AIFixConfigDict = {}

    if os.environ.get('AI_FIX_PROVIDER'):
        config['ai_provider'] = os.environ['AI_FIX_PROVIDER']

    return config


def detect_linters(root_dir: Path) -> list[str]:
    """Auto-detect which linters to use based on project files."""
    detected: list[str] = []

    # Python linters
    python_markers = ['pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt']
    if any((root_dir / m).exists() for m in python_markers):
        if is_binary_available('ruff'):
            detected.append('ruff')
        if is_binary_available('mypy'):
            detected.append('mypy')

    # JavaScript/TypeScript linters
    js_markers = ['package.json', 'tsconfig.json']
    if any((root_dir / m).exists() for m in js_markers):
        if is_binary_available('eslint') or (root_dir / 'node_modules' / '.bin' / 'eslint').exists():
            detected.append('eslint')
        if (root_dir / 'tsconfig.json').exists():
            detected.append('tsc')

    return detected


def get_staged_files() -> list[str]:
    """Get list of staged files from git."""
    try:
        git_bin = get_binary('git')
        result = subprocess.run(
            [git_bin, 'diff', '--cached', '--name-only', '--diff-filter=ACM'],
            capture_output=True,
            text=True,
            check=True,
        )
        return [f for f in result.stdout.strip().split('\n') if f]
    except subprocess.CalledProcessError:
        return []


def get_file_context(filepath: Path, line: int, context_lines: int = 5) -> tuple[str, int]:
    """Get context around a specific line in a file."""
    try:
        with open(filepath) as f:
            lines = f.readlines()

        start = max(0, line - context_lines - 1)
        end = min(len(lines), line + context_lines)

        context = ''.join(lines[start:end])
        return context, start + 1
    except Exception:
        return '', 0


def run_linter(
    linter_name: str,
    files: list[str],
    config: LinterRuntimeConfig,
) -> list[LintError]:
    """Run a linter and parse its output."""
    if linter_name not in LINTER_PARSERS:
        logger.warning(f'Unknown linter: {linter_name}')
        return []

    parser = LINTER_PARSERS[linter_name]()

    if not parser.is_available():
        logger.debug(f'Linter {linter_name} not available')
        return []

    cmd = parser.get_command(files, config)
    logger.debug(f'Running: {" ".join(cmd)}')

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        # Linters return non-zero on errors, which is expected
        output = result.stdout or result.stderr
        return parser.parse(output, files)
    except subprocess.TimeoutExpired:
        logger.warning(f'Linter {linter_name} timed out')
        return []
    except Exception as e:
        logger.warning(f'Failed to run {linter_name}: {e}')
        return []


def dedupe_errors(errors: list[LintError]) -> list[LintError]:
    """Remove duplicate errors at the same location."""
    seen: dict[str, LintError] = {}

    for error in errors:
        key = error.location_key
        if key not in seen or error.priority < seen[key].priority:
            seen[key] = error

    return list(seen.values())


def sort_errors(errors: list[LintError]) -> list[LintError]:
    """Sort errors by priority and location."""
    return sorted(errors, key=lambda e: (e.priority, e.file, e.line))


def get_fix_strategy(error: LintError, config: AIFixConfig) -> FixStrategy:
    """Determine fix strategy for an error."""
    # Check never_fix first
    for pattern in config.never_fix_patterns:
        if error.matches_pattern(pattern):
            return FixStrategy.NEVER

    # Check auto_fix
    for pattern in config.auto_fix_patterns:
        if error.matches_pattern(pattern):
            return FixStrategy.AUTO

    # Check prompt_fix
    for pattern in config.prompt_fix_patterns:
        if error.matches_pattern(pattern):
            return FixStrategy.PROMPT

    # Default to prompt
    return FixStrategy.PROMPT


def apply_fix(
    filepath: Path,
    error: LintError,
    fixed_content: str,
    original_content: str,
) -> bool:
    """Apply a fix to a file.

    Uses multiple strategies for reliable fixing:
    1. Exact string replacement of context -> fixed
    2. Line-by-line diff to apply minimal changes
    3. Line-based replacement as fallback
    """
    try:
        # Read current file content
        with open(filepath) as f:
            file_content = f.read()

        # Normalize content (strip trailing whitespace, normalize newlines)
        def normalize(s: str) -> str:
            return '\n'.join(line.rstrip() for line in s.strip().split('\n'))

        original_normalized = normalize(original_content)
        fixed_normalized = normalize(fixed_content)

        # If no actual change, skip
        if original_normalized == fixed_normalized:
            logger.debug('No change detected, skipping')
            return True

        # Strategy 1: Try exact string replacement
        if original_normalized in file_content:
            new_content = file_content.replace(original_normalized, fixed_normalized, 1)
            with open(filepath, 'w') as f:
                f.write(new_content)
            return True

        # Strategy 2: Try matching with preserved whitespace
        original_stripped = original_content.strip()
        if original_stripped in file_content:
            new_content = file_content.replace(original_stripped, fixed_normalized, 1)
            with open(filepath, 'w') as f:
                f.write(new_content)
            return True

        # Strategy 3: Find the error line and try to match a subset
        # This handles when AI returns partial context
        file_lines = file_content.split('\n')
        fixed_lines = fixed_normalized.split('\n')
        original_lines = original_normalized.split('\n')

        # Try to find where the original context starts in the file
        error_line_idx = error.line - 1  # 0-indexed
        context_start = error.context_start_line - 1 if error.context_start_line > 0 else max(0, error_line_idx - 5)
        context_end = context_start + len(original_lines)

        # Check if the file has the expected context
        if context_start < len(file_lines):
            # Try to match as many lines as possible
            matched = True
            for i, orig_line in enumerate(original_lines):
                file_idx = context_start + i
                if file_idx >= len(file_lines):
                    matched = False
                    break
                if normalize(file_lines[file_idx]) != normalize(orig_line):
                    matched = False
                    break

            if matched:
                # Replace the matched range with fixed content
                new_lines = (
                    file_lines[:context_start] +
                    fixed_lines +
                    file_lines[context_end:]
                )
                new_content = '\n'.join(new_lines)
                # Preserve trailing newline if original had one
                if file_content.endswith('\n') and not new_content.endswith('\n'):
                    new_content += '\n'
                with open(filepath, 'w') as f:
                    f.write(new_content)
                return True

        # Strategy 4: If AI returned fewer lines (partial fix), try to match
        # just those lines within the context
        if len(fixed_lines) < len(original_lines):
            # Find which part of original matches the fixed content
            for offset in range(len(original_lines) - len(fixed_lines) + 1):
                subset = original_lines[offset:offset + len(fixed_lines)]
                subset_str = '\n'.join(normalize(l) for l in subset)
                if subset_str in file_content:
                    new_content = file_content.replace(subset_str, fixed_normalized, 1)
                    with open(filepath, 'w') as f:
                        f.write(new_content)
                    return True

        # Strategy 5: Last resort - direct line replacement at error location
        if error_line_idx < len(file_lines) and len(fixed_lines) > 0:
            # Replace just around the error line
            lines_to_replace = min(len(fixed_lines), len(file_lines) - error_line_idx)
            new_lines = (
                file_lines[:error_line_idx] +
                fixed_lines[:lines_to_replace] +
                file_lines[error_line_idx + lines_to_replace:]
            )
            new_content = '\n'.join(new_lines)
            if file_content.endswith('\n') and not new_content.endswith('\n'):
                new_content += '\n'
            with open(filepath, 'w') as f:
                f.write(new_content)
            return True

        logger.error(f'Could not find matching content to replace in {filepath}')
        return False

    except Exception as e:
        logger.error(f'Failed to apply fix: {e}')
        return False


def prompt_user(error: LintError, diff: str) -> str:
    """Prompt user for action on a fix."""
    print(f'\n{Colors.BOLD}Fix available for:{Colors.RESET}')
    logger.lint_error(error)
    print(diff)
    print(f'\n{Colors.CYAN}[A]pply  [S]kip  [E]dit  [Q]uit{Colors.RESET}')

    try:
        response = input('> ').strip().lower()
        return response if response in ('a', 's', 'e', 'q') else 's'
    except (EOFError, KeyboardInterrupt):
        return 'q'


def validate_fix(
    linter_name: str,
    filepath: Path,
    config: LinterRuntimeConfig,
) -> bool:
    """Re-run linter to validate fix worked."""
    errors = run_linter(linter_name, [str(filepath)], config)
    return len(errors) == 0


# =============================================================================
# Fix Cache
# =============================================================================


class FixCache:
    """Cache for storing successful fixes."""

    def __init__(self, config: CacheConfig, root_dir: Path) -> None:
        self.enabled = config.enabled
        self.cache_dir = root_dir / config.cache_dir
        self.ttl_hours = config.ttl_hours

    def _get_cache_key(self, error: LintError, context: str) -> str:
        """Generate cache key for an error."""
        key_data = f'{error.linter}:{error.code}:{context}'
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    def get(self, error: LintError, context: str) -> str | None:
        """Get cached fix if available."""
        if not self.enabled:
            return None

        key = self._get_cache_key(error, context)
        cache_file = self.cache_dir / f'{key}.json'

        if not cache_file.exists():
            return None

        try:
            with open(cache_file) as f:
                data = json.load(f)

            # Check TTL
            cached_time = datetime.fromisoformat(data['timestamp'])
            age_hours = (datetime.now(timezone.utc) - cached_time).total_seconds() / 3600

            if age_hours > self.ttl_hours:
                cache_file.unlink()
                return None

            return data.get('fixed_content')
        except Exception:
            return None

    def put(self, error: LintError, context: str, fixed_content: str) -> None:
        """Cache a successful fix."""
        if not self.enabled:
            return

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = self._get_cache_key(error, context)
        cache_file = self.cache_dir / f'{key}.json'

        try:
            with open(cache_file, 'w') as f:
                json.dump({
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'linter': error.linter,
                    'code': error.code,
                    'fixed_content': fixed_content,
                }, f)
        except Exception:
            pass


# =============================================================================
# Main Runner
# =============================================================================


class AIFixRunner:
    """Main runner for AI fix operations."""

    def __init__(
        self,
        config: AIFixConfig,
        dry_run: bool = False,
        auto_mode: bool = False,
        explain: bool = False,
        check_only: bool = False,
        direct_edit: bool = False,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.auto_mode = auto_mode
        self.explain = explain
        self.check_only = check_only
        self.direct_edit = direct_edit
        self.cache = FixCache(config.cache, config.root_dir)
        self.provider = self._get_provider()
        self.results: list[FixAttempt] = []

    def _get_provider(self) -> AIProviderBase | None:
        """Get the best available AI provider.

        Uses PROVIDER_PRIORITY for auto-detection order:
        1. copilot-cli (if logged in)
        2. vibe (if MISTRAL_API_KEY is set)
        3. ollama (if running locally)
        4. mistral (if MISTRAL_API_KEY is set)
        """
        # Try configured provider first
        provider_name = self.config.ai_provider
        if provider_name in AI_PROVIDERS:
            provider_config = self.config.providers.get(
                provider_name, ProviderConfig(),
            )
            provider = AI_PROVIDERS[provider_name](provider_config)
            if provider.is_available():
                logger.info(f'Using AI provider: {provider_name}')
                return provider
            else:
                logger.warning(f'Configured provider {provider_name} is not available')

        # Fall back through providers in priority order
        for name in PROVIDER_PRIORITY:
            if name in AI_PROVIDERS:
                provider_config = self.config.providers.get(name, ProviderConfig())
                provider = AI_PROVIDERS[name](provider_config)
                if provider.is_available():
                    logger.info(f'Using AI provider: {name} (auto-detected)')
                    return provider

        logger.error('No AI provider available. Install one of:')
        logger.error('  - copilot-cli: brew install copilot-cli')
        logger.error('  - vibe: brew install mistralai/tap/vibe (+ MISTRAL_API_KEY)')
        logger.error('  - ollama: ollama serve')
        return None

    def run(
        self,
        files: list[str] | None = None,
        preloaded_errors: list[LintError] | None = None,
    ) -> int:
        """Run the AI fix process.

        Args:
            files: Files to check (default: staged files)
            preloaded_errors: Pre-collected errors to fix (skips linter run)
        """
        # Get files to check
        if not files:
            files = get_staged_files()
            if not files and not preloaded_errors:
                logger.info('No staged files to check')
                return 0

        if files:
            logger.header(f'Checking {len(files)} file(s)...')

        # Use preloaded errors or collect fresh ones
        if preloaded_errors:
            all_errors = preloaded_errors
            linters = list({e.linter for e in all_errors})
            # Get files from errors if not provided
            if not files:
                files = list({e.file for e in all_errors})
            logger.info(f'Using {len(all_errors)} pre-collected error(s)')
        else:
            # Detect and run linters
            linters = detect_linters(self.config.root_dir)
            if not linters:
                logger.warning('No linters detected for this project')
                return 0

            logger.debug(f'Using linters: {", ".join(linters)}')

            # Collect initial errors
            all_errors = self._collect_errors(files, linters)

        if not all_errors:
            logger.success('No linting errors found!')
            return 0

        logger.header(f'Found {len(all_errors)} error(s)')

        # Show complexity breakdown
        by_complexity = self._group_by_complexity(all_errors)
        for complexity, errs in by_complexity.items():
            if errs:
                logger.debug(f'  {complexity.value}: {len(errs)} errors')

        # Check-only mode
        if self.check_only:
            for error in all_errors:
                logger.lint_error(error)
            return 1 if all_errors else 0

        # Process errors
        if not self.provider:
            logger.error('No AI provider available')
            logger.info('Install GitHub CLI (gh), set MISTRAL_API_KEY, or run Ollama')
            return 1

        # Use direct edit mode if requested and provider supports it
        if self.direct_edit and self.provider.supports_direct_edit():
            return self._run_direct_edit(files, linters, all_errors)

        return self._run_iterative_fix(files, linters)

    def _run_direct_edit(
        self,
        files: list[str],
        linters: list[str],
        errors: list[LintError],
    ) -> int:
        """Run AI fix using direct file editing mode.

        In this mode, we tell the AI to edit files directly rather than
        parsing its output and applying changes ourselves.
        """
        if not self.provider:
            return 1

        logger.info('Using direct file editing mode')

        # Group errors by file
        errors_by_file: dict[str, list[LintError]] = {}
        for error in errors:
            if error.file not in errors_by_file:
                errors_by_file[error.file] = []
            errors_by_file[error.file].append(error)

        total_fixed = 0
        total_failed = 0

        for filepath, file_errors in errors_by_file.items():
            logger.header(f'Fixing {filepath} ({len(file_errors)} errors)')

            # Determine complexity (use highest complexity in batch)
            max_complexity = max(
                (e.complexity or ErrorComplexity.MODERATE for e in file_errors),
                key=lambda c: {'simple': 0, 'moderate': 1, 'complex': 2}[c.value],
            )

            # Show errors being fixed
            for error in file_errors:
                logger.lint_error(error)

            if self.dry_run:
                logger.info('  (dry-run, skipping fix)')
                continue

            # Let AI fix the file directly
            full_path = self.config.root_dir / filepath
            success, explanation = self.provider.fix_file_directly(
                full_path,
                file_errors,
                complexity=max_complexity,
            )

            if success:
                logger.success(f'  → {explanation}')
                total_fixed += len(file_errors)
            else:
                logger.error(f'  → {explanation}')
                total_failed += len(file_errors)

        # Re-check for remaining errors
        logger.header('Re-checking for remaining errors...')
        remaining_errors = self._collect_errors(files, linters)

        if remaining_errors:
            logger.warning(f'{len(remaining_errors)} error(s) remaining')
            return 1
        else:
            logger.success(f'All errors fixed! ({total_fixed} fixed)')
            return 0

    def _collect_errors(
        self,
        files: list[str],
        linters: list[str],
    ) -> list[LintError]:
        """Collect errors from all linters."""
        all_errors: list[LintError] = []
        for linter_name in linters:
            linter_config = self.config.linters.get(
                linter_name, LinterRuntimeConfig(),
            )
            if not linter_config.enabled:
                continue

            errors = run_linter(linter_name, files, linter_config)
            all_errors.extend(errors)
            logger.debug(f'{linter_name}: {len(errors)} error(s)')

        # Dedupe and sort
        if self.config.behavior.dedupe_errors:
            all_errors = dedupe_errors(all_errors)

        all_errors = sort_errors(all_errors)

        # Limit errors
        if len(all_errors) > self.config.behavior.max_total_errors:
            logger.warning(
                f'Limiting to {self.config.behavior.max_total_errors} errors '
                f'(found {len(all_errors)})',
            )
            all_errors = all_errors[:self.config.behavior.max_total_errors]

        return all_errors

    def _get_batch_size(self, complexity: ErrorComplexity) -> int:
        """Get batch size for a given complexity level."""
        behavior = self.config.behavior
        return {
            ErrorComplexity.SIMPLE: behavior.batch_size_simple,
            ErrorComplexity.MODERATE: behavior.batch_size_moderate,
            ErrorComplexity.COMPLEX: behavior.batch_size_complex,
        }.get(complexity, 1)

    def _group_by_complexity(
        self,
        errors: list[LintError],
    ) -> dict[ErrorComplexity, list[LintError]]:
        """Group errors by complexity level."""
        groups: dict[ErrorComplexity, list[LintError]] = {
            ErrorComplexity.SIMPLE: [],
            ErrorComplexity.MODERATE: [],
            ErrorComplexity.COMPLEX: [],
        }
        for error in errors:
            groups[error.complexity].append(error)
        return groups

    def _create_batches(
        self,
        errors: list[LintError],
    ) -> list[tuple[ErrorComplexity, list[LintError]]]:
        """Create batches of errors respecting complexity limits."""
        batches: list[tuple[ErrorComplexity, list[LintError]]] = []

        # Group by complexity
        by_complexity = self._group_by_complexity(errors)

        # Process complex errors first (one at a time), then moderate, then simple
        for complexity in [ErrorComplexity.COMPLEX, ErrorComplexity.MODERATE, ErrorComplexity.SIMPLE]:
            complexity_errors = by_complexity[complexity]
            batch_size = self._get_batch_size(complexity)

            # Split into batches of appropriate size
            for i in range(0, len(complexity_errors), batch_size):
                batch = complexity_errors[i:i + batch_size]
                batches.append((complexity, batch))

        return batches

    def _run_iterative_fix(
        self,
        files: list[str],
        linters: list[str],
    ) -> int:
        """Run iterative fix loop until all errors are resolved or max iterations reached."""
        progress = IterationProgress()
        max_iterations = self.config.behavior.max_fix_iterations
        rerun_after_batch = self.config.behavior.rerun_after_batch
        processed_error_keys: set[str] = set()  # Track what we've tried to fix

        while progress.iteration < max_iterations:
            progress.iteration += 1
            logger.header(f'Fix iteration {progress.iteration}/{max_iterations}')

            # Collect current errors
            all_errors = self._collect_errors(files, linters)

            if not all_errors:
                logger.success('All errors resolved!')
                break

            # Filter out errors we've already tried
            new_errors = [e for e in all_errors if e.location_key not in processed_error_keys]

            if not new_errors:
                logger.info('No new errors to process')
                break

            logger.info(f'Found {len(all_errors)} error(s), {len(new_errors)} new')

            # Count by complexity
            by_complexity = self._group_by_complexity(new_errors)
            for complexity, errs in by_complexity.items():
                if errs:
                    batch_size = self._get_batch_size(complexity)
                    logger.debug(
                        f'  {complexity.value}: {len(errs)} errors '
                        f'(batch size: {batch_size})',
                    )

            # Create batches
            batches = self._create_batches(new_errors)
            logger.info(f'Processing in {len(batches)} batch(es)')

            iteration_fixed = 0
            iteration_failed = 0
            iteration_skipped = 0

            for batch_idx, (complexity, batch) in enumerate(batches):
                if not batch:
                    continue

                batch_size = len(batch)
                logger.info(
                    f'\nBatch {batch_idx + 1}/{len(batches)}: '
                    f'{batch_size} {complexity.value} error(s)',
                )

                # Process the batch
                for error in batch:
                    # Mark as processed
                    processed_error_keys.add(error.location_key)

                    result = self._process_single_error(error)
                    if result == FixResult.FIXED:
                        iteration_fixed += 1
                    elif result == FixResult.FAILED:
                        iteration_failed += 1
                    else:
                        iteration_skipped += 1

                # Re-run linters after each batch if configured
                # (but not if this is the last batch of the last iteration)
                if rerun_after_batch and batch_idx < len(batches) - 1:
                    logger.debug('Re-checking for new errors after batch...')
                    new_check_errors = self._collect_errors(files, linters)
                    if not new_check_errors:
                        logger.success('All errors resolved after batch!')
                        progress.add_batch_result(iteration_fixed, iteration_failed, iteration_skipped)
                        break

            progress.add_batch_result(iteration_fixed, iteration_failed, iteration_skipped)

            # If we didn't fix anything this iteration, stop
            if iteration_fixed == 0:
                logger.info('No fixes applied this iteration, stopping')
                break

        # Final summary
        logger.header('Final Summary')
        logger.info(f'Iterations: {progress.iteration}')
        if progress.total_fixed > 0:
            logger.success(f'Total fixed: {progress.total_fixed}')
        if progress.total_skipped > 0:
            logger.info(f'Total skipped: {progress.total_skipped}')
        if progress.total_failed > 0:
            logger.error(f'Total failed: {progress.total_failed}')

        # Final check - any remaining errors?
        final_errors = self._collect_errors(files, linters)
        if final_errors:
            logger.warning(f'Remaining errors: {len(final_errors)}')
            return 1

        return 0

    def _process_errors(self, errors: list[LintError]) -> int:
        """Process and fix errors."""
        fixed_count = 0
        failed_count = 0
        skipped_count = 0

        # Group by file if configured
        if self.config.behavior.batch_by_file:
            errors_by_file: dict[str, list[LintError]] = {}
            for error in errors:
                errors_by_file.setdefault(error.file, []).append(error)

            for filepath, file_errors in errors_by_file.items():
                f, fa, s = self._process_file_errors(Path(filepath), file_errors)
                fixed_count += f
                failed_count += fa
                skipped_count += s
        else:
            for error in errors:
                result = self._process_single_error(error)
                if result == FixResult.FIXED:
                    fixed_count += 1
                elif result == FixResult.FAILED:
                    failed_count += 1
                else:
                    skipped_count += 1

        # Summary
        logger.header('Summary')
        if fixed_count > 0:
            logger.success(f'Fixed: {fixed_count}')
        if skipped_count > 0:
            logger.info(f'Skipped: {skipped_count}')
        if failed_count > 0:
            logger.error(f'Failed: {failed_count}')

        return 1 if failed_count > 0 else 0

    def _process_file_errors(
        self,
        filepath: Path,
        errors: list[LintError],
    ) -> tuple[int, int, int]:
        """Process errors for a single file."""
        fixed = 0
        failed = 0
        skipped = 0

        logger.info(f'Processing {filepath} ({len(errors)} errors)')

        for error in errors:
            result = self._process_single_error(error)
            if result == FixResult.FIXED:
                fixed += 1
            elif result == FixResult.FAILED:
                failed += 1
            else:
                skipped += 1

        return fixed, failed, skipped

    def _process_single_error(self, error: LintError) -> FixResult:
        """Process a single error."""
        logger.lint_error(error)

        # Determine strategy
        strategy = get_fix_strategy(error, self.config)

        if strategy == FixStrategy.NEVER:
            logger.debug('  → Skipped (never auto-fix)')
            return FixResult.SKIPPED

        # Get file context
        filepath = self.config.root_dir / error.file
        if not filepath.exists():
            logger.warning(f'  → File not found: {filepath}')
            return FixResult.FAILED

        context, context_start = get_file_context(
            filepath,
            error.line,
            self.config.behavior.context_lines,
        )
        error.context = context
        error.context_start_line = context_start

        # Check cache
        cached_fix = self.cache.get(error, context)
        if cached_fix:
            logger.debug('  → Using cached fix')
            fixed_content = cached_fix
            explanation = 'Cached fix'
        else:
            # Get fix from AI
            if not self.provider:
                return FixResult.NO_FIX

            start_time = time.time()
            with open(filepath) as f:
                file_content = f.read()

            # Pass complexity for smart model selection
            fix_result, explanation = self.provider.generate_fix(
                error, file_content, context, complexity=error.complexity,
            )
            _duration_ms = int((time.time() - start_time) * 1000)

            if not fix_result:
                logger.warning(f'  → No fix available: {explanation}')
                return FixResult.NO_FIX

            fixed_content = fix_result

            # Cache successful fix
            self.cache.put(error, context, fixed_content)

        # Show explanation if requested
        if self.explain:
            logger.info(f'  → {explanation}')

        # Dry run - just show diff
        if self.dry_run:
            logger.diff(context, fixed_content, error.file)
            return FixResult.SKIPPED

        # Auto mode or prompt
        if strategy == FixStrategy.AUTO or self.auto_mode:
            if apply_fix(filepath, error, fixed_content, context):
                logger.success('  → Fixed automatically')
                return FixResult.FIXED
            else:
                return FixResult.FAILED

        # Prompt mode
        import difflib
        diff = '\n'.join(difflib.unified_diff(
            context.splitlines(),
            fixed_content.splitlines(),
            lineterm='',
        ))

        action = prompt_user(error, diff)

        if action == 'a':
            if apply_fix(filepath, error, fixed_content, context):
                logger.success('  → Applied')
                return FixResult.FIXED
            else:
                return FixResult.FAILED
        elif action == 'q':
            raise KeyboardInterrupt
        else:
            logger.info('  → Skipped')
            return FixResult.SKIPPED


# =============================================================================
# CLI
# =============================================================================


def create_argument_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog='ai-fix',
        description='AI-powered linting error fixer with smart model selection',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Common Usage:
  ai-fix                              Auto-detect errors and show them
  ai-fix --fix                        Fix errors (with prompts)
  ai-fix --fix --auto                 Fix all errors automatically

Quick Start:
  1. Run linters:    pre-commit run ruff --all-files
  2. Check errors:   ai-fix
  3. Fix errors:     ai-fix --fix --auto

Setup (one-time):
  ai-fix --init-log-dir               Show pre-commit config snippets
  ai-fix --show-log-dir               Show where logs are stored

Advanced:
  ai-fix --fix --provider ollama      Use local Ollama for fixes
  ai-fix --fix --model gpt-5          Use specific model
  ai-fix --list-models                Show available models
  ai-fix --clear-logs                 Clear linter log files

Environment Variables:
  AI_FIX_PROVIDER                     Default AI provider
  AI_FIX_AUTO=true                    Enable auto mode
  MISTRAL_API_KEY                     API key for Vibe/Mistral
        ''',
    )

    parser.add_argument(
        '--config', '-c',
        type=Path,
        help='Path to configuration file',
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='Check for errors without fixing',
    )
    parser.add_argument(
        '--fix',
        action='store_true',
        help='Attempt to fix errors using AI',
    )
    parser.add_argument(
        '--auto',
        action='store_true',
        help='Auto-apply fixes without prompting',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview fixes without applying',
    )
    parser.add_argument(
        '--explain',
        action='store_true',
        help='Show explanations for fixes',
    )
    parser.add_argument(
        '--provider',
        choices=list(AI_PROVIDERS.keys()),
        help='AI provider to use (default: auto-detect)',
    )
    parser.add_argument(
        '--model',
        help='Model to use (overrides smart model selection)',
    )
    parser.add_argument(
        '--model-simple',
        help='Model for simple errors (formatting, imports)',
    )
    parser.add_argument(
        '--model-moderate',
        help='Model for moderate errors (type hints)',
    )
    parser.add_argument(
        '--model-complex',
        help='Model for complex errors (security, logic)',
    )
    parser.add_argument(
        '--no-smart-models',
        action='store_true',
        help='Disable smart model selection (use default model for all)',
    )
    parser.add_argument(
        '--list-models',
        action='store_true',
        help='Show available models for each provider and exit',
    )
    parser.add_argument(
        '--linters',
        help='Comma-separated linters to run',
    )
    parser.add_argument(
        '--files',
        nargs='*',
        help='Files to check (default: staged files)',
    )
    parser.add_argument(
        '--from-logs',
        nargs='*',
        type=Path,
        help='Read errors from pre-commit log files (auto-discovers from XDG_STATE_HOME)',
    )
    parser.add_argument(
        '--log-dir',
        type=Path,
        help='Override log directory (default: $XDG_STATE_HOME/pre-commit/logs/{repo_hash})',
    )
    parser.add_argument(
        '--clear-logs',
        action='store_true',
        help='Clear all linter log files (use as first hook in pre-commit)',
    )
    parser.add_argument(
        '--show-log-dir',
        action='store_true',
        help='Show the log directory path for this repository and exit',
    )
    parser.add_argument(
        '--init-log-dir',
        action='store_true',
        help='Create the log directory and print paths for use in pre-commit config',
    )
    parser.add_argument(
        '--max-retries',
        type=int,
        default=2,
        help='Maximum fix attempts per error',
    )
    parser.add_argument(
        '--timeout',
        type=int,
        help='Timeout in seconds for AI calls (default: 120)',
    )
    parser.add_argument(
        '--max-iterations',
        type=int,
        default=3,
        help='Maximum fix iterations (re-lint and fix cycles)',
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        help='Override batch size for all complexity levels',
    )
    parser.add_argument(
        '--single-issue',
        action='store_true',
        help='Process only one issue at a time (sets all batch sizes to 1)',
    )
    parser.add_argument(
        '--direct-edit',
        action='store_true',
        default=True,
        help='Let the AI edit files directly (default: enabled)',
    )
    parser.add_argument(
        '--no-direct-edit',
        action='store_true',
        help='Disable direct file editing, parse AI output instead',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show detailed output',
    )
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress all output except errors',
    )
    parser.add_argument(
        '--show-ai-output',
        action='store_true',
        help='Show full AI response (not just extracted code)',
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output results in JSON format',
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}',
    )

    return parser


def print_available_models() -> None:
    """Print available models for each provider."""
    print('Available models by provider:\n')

    print('  copilot-cli (GitHub Copilot CLI)')
    print('    Install: brew install copilot-cli')
    for model, info in COPILOT_MODELS.items():
        default = ' (default)' if model == DEFAULT_MODELS.get('copilot-cli') else ''
        print(f'      {model}: {info["quality"]} quality, {info["speed"]} speed{default}')

    print('\n  vibe (Mistral Vibe CLI)')
    print('    Install: brew install mistralai/tap/vibe')
    print('    Requires: MISTRAL_API_KEY environment variable')
    for model, info in VIBE_MODELS.items():
        default = ' (default)' if model == DEFAULT_MODELS.get('vibe') else ''
        print(f'      {model}: {info["quality"]} quality, {info["speed"]} speed{default}')

    print('\n  mistral (Mistral API)')
    print('    Requires: MISTRAL_API_KEY environment variable')
    for model, info in MISTRAL_MODELS.items():
        default = ' (default)' if model == DEFAULT_MODELS.get('mistral') else ''
        print(f'      {model}: {info["quality"]} quality, {info["speed"]} speed{default}')

    print('\n  ollama (Local)')
    print('    Install: ollama serve')
    for model, info in OLLAMA_MODELS.items():
        default = ' (default)' if model == DEFAULT_MODELS.get('ollama') else ''
        print(f'      {model}: {info["quality"]} quality, {info["speed"]} speed{default}')

    print('\nSmart model selection by complexity:')
    for provider in ['copilot-cli', 'vibe', 'ollama']:
        if provider in MODEL_DEFAULTS_BY_COMPLEXITY:
            models = MODEL_DEFAULTS_BY_COMPLEXITY[provider]
            print(f'  {provider}:')
            print(f'    Simple:   {models[ErrorComplexity.SIMPLE]}')
            print(f'    Moderate: {models[ErrorComplexity.MODERATE]}')
            print(f'    Complex:  {models[ErrorComplexity.COMPLEX]}')


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    global logger

    parser = create_argument_parser()
    args = parser.parse_args(argv)

    # Handle --list-models early
    if args.list_models:
        print_available_models()
        return 0

    # Setup logger
    logger = Logger(
        verbose=args.verbose,
        quiet=args.quiet,
        json_output=args.json,
        show_ai_output=args.show_ai_output,
    )

    # Disable colors if not TTY
    if not sys.stdout.isatty() or args.json:
        Colors.disable()

    # Load configuration
    file_config = load_config_file(args.config)
    env_config = load_env_config()

    # Merge configs (CLI > env > file)
    merged_config: AIFixConfigDict = {**file_config, **env_config}

    # Apply CLI overrides
    if args.provider:
        merged_config['ai_provider'] = args.provider

    # Create runtime config
    config = AIFixConfig.from_dict(merged_config, Path.cwd())

    # Set global config for binary path lookups
    set_global_aifix_config(config)

    # Apply model overrides from CLI
    provider_name = config.ai_provider
    if provider_name and provider_name in config.providers:
        provider_config = config.providers[provider_name]
    else:
        provider_config = ProviderConfig()
        if provider_name:
            config.providers[provider_name] = provider_config

    # Override model settings from CLI
    if args.model:
        provider_config.model = args.model
        provider_config.smart_model_selection = False  # Explicit model disables smart selection
    if args.model_simple:
        provider_config.model_simple = args.model_simple
    if args.model_moderate:
        provider_config.model_moderate = args.model_moderate
    if args.model_complex:
        provider_config.model_complex = args.model_complex
    if args.no_smart_models:
        provider_config.smart_model_selection = False
    if args.timeout:
        provider_config.timeout = args.timeout

    # Update the provider config
    if provider_name:
        config.providers[provider_name] = provider_config

    # Apply batch size overrides from CLI
    if args.single_issue:
        config.behavior.batch_size_simple = 1
        config.behavior.batch_size_moderate = 1
        config.behavior.batch_size_complex = 1
    elif args.batch_size:
        config.behavior.batch_size_simple = args.batch_size
        config.behavior.batch_size_moderate = args.batch_size
        config.behavior.batch_size_complex = min(args.batch_size, 1)  # Complex always max 1

    if args.max_iterations:
        config.behavior.max_fix_iterations = args.max_iterations

    # Handle --show-log-dir (show log directory and exit)
    if args.show_log_dir:
        log_dir = args.log_dir or get_log_dir()
        print(f'Log directory: {log_dir}')
        print(f'Repository:    {get_repo_root() or Path.cwd()}')
        print(f'Repo hash:     {get_repo_hash()}')
        return 0

    # Handle --init-log-dir (create log directory and show config)
    if args.init_log_dir:
        log_dir = ensure_log_dir()
        print(f'Log directory created: {log_dir}')
        print()
        print('=' * 70)
        print('PRE-COMMIT CONFIG SNIPPETS')
        print('=' * 70)
        print()
        print('Add these hooks to your .pre-commit-config.yaml:')
        print()
        print('''# Clear logs at start (must be first hook)
- repo: local
  hooks:
    - id: ai-fix-clear
      name: Clear lint log files
      entry: python3 -m pre_commit.ai_fix --clear-logs
      language: system
      always_run: true
      pass_filenames: false

# Python linting with log capture
- repo: local
  hooks:
    - id: ruff
      name: ruff
      entry: python3 -c "from pre_commit.ai_fix import lint_log_main; exit(lint_log_main(['ruff'] + __import__('sys').argv[1:]))"
      language: system
      types_or: [python, pyi]

# AI Fix (run manually after linters)
- repo: local
  hooks:
    - id: ai-fix
      name: AI Fix
      entry: python3 -m pre_commit.ai_fix --fix --auto
      language: system
      pass_filenames: false
      stages: [manual]  # Run: pre-commit run ai-fix --hook-stage manual
''')
        print('=' * 70)
        print()
        print('Quick start:')
        print('  1. Run linters:  pre-commit run ruff --all-files')
        print('  2. Fix errors:   ai-fix --fix --auto')
        print()
        print('Or just run: ai-fix --fix')
        print('(It will auto-detect errors from log files)')
        return 0

    # Handle --clear-logs (clear log files at start of pre-commit run)
    if args.clear_logs:
        log_dir = args.log_dir if hasattr(args, 'log_dir') and args.log_dir else None
        cleared_dir = clear_log_files(log_dir)
        logger.info(f'Cleared log files in {cleared_dir}')
        return 0

    # Handle --from-logs (read pre-commit log_file outputs)
    collected_errors: list[LintError] | None = None
    if args.from_logs is not None:
        # If --from-logs given without paths, auto-discover from XDG location
        log_files = args.from_logs if args.from_logs else None
        log_dir = args.log_dir if hasattr(args, 'log_dir') and args.log_dir else None
        collected_errors = load_errors_from_log_files(log_files, log_dir)
        if not collected_errors:
            actual_log_dir = log_dir or get_log_dir()
            logger.info(f'No errors found in log files at {actual_log_dir}')
            return 0
        logger.info(f'Loaded {len(collected_errors)} error(s) from log files')

    # AUTO-DETECT: If no explicit input source, check if log files exist
    if collected_errors is None and args.from_logs is None:
        log_dir = get_log_dir()
        if log_dir.exists() and any(log_dir.glob('*.json')):
            collected_errors = load_errors_from_log_files()
            if collected_errors:
                logger.info(f'Auto-loaded {len(collected_errors)} error(s) from {log_dir}')

    # Default to check mode if neither --check nor --fix specified
    check_only = args.check or not args.fix

    # Direct edit mode - let AI edit files directly (default: enabled)
    direct_edit = args.direct_edit and not args.no_direct_edit

    # Create runner
    runner = AIFixRunner(
        config=config,
        dry_run=args.dry_run,
        auto_mode=args.auto or os.environ.get('AI_FIX_AUTO') == 'true',
        explain=args.explain,
        check_only=check_only,
        direct_edit=direct_edit,
    )

    try:
        result = runner.run(args.files, preloaded_errors=collected_errors)
        return result
    except KeyboardInterrupt:
        logger.info('\nInterrupted')
        return 130
    finally:
        logger.flush_json()


if __name__ == '__main__':
    sys.exit(main())


# =============================================================================
# lint-log: Wrapper to run linters and capture JSON output to XDG log directory
# =============================================================================

LINTER_CONFIGS = {
    'ruff': {
        'json_args': ['--output-format=json'],
        'normal_args': ['--fix', '--exit-non-zero-on-fix'],
        'command': 'ruff',
        'subcommand': 'check',
    },
    'mypy': {
        'json_args': ['--output=json'],
        'normal_args': ['--ignore-missing-imports', '--no-error-summary'],
        'command': 'mypy',
        'subcommand': None,
    },
    'eslint': {
        'json_args': ['--format=json'],
        'normal_args': ['--fix'],
        'command': 'eslint',
        'subcommand': None,
    },
    'biome': {
        'json_args': ['--reporter=json'],
        'normal_args': ['--write'],
        'command': 'biome',
        'subcommand': 'check',
    },
    'pylint': {
        'json_args': ['--output-format=json'],
        'normal_args': [],
        'command': 'pylint',
        'subcommand': None,
    },
    'flake8': {
        'json_args': ['--format=json'],
        'normal_args': [],
        'command': 'flake8',
        'subcommand': None,
    },
}


def lint_log_main(argv: list[str] | None = None) -> int:
    """Run a linter and capture JSON output to XDG-compliant log directory.

    This is a wrapper command for use in pre-commit hooks. It:
    1. Runs the linter with JSON output, saving to the log directory
    2. Runs the linter normally for display and exit code

    Usage:
        lint-log <linter> [files...]
        lint-log ruff file1.py file2.py
        lint-log mypy src/

    The log files are stored in:
        $XDG_STATE_HOME/pre-commit/logs/{repo_hash}/{linter}.json

    This allows ai-fix to read linter output from multiple hooks:
        ai-fix --from-logs --fix
    """
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print('Usage: lint-log <linter> [files...]', file=sys.stderr)
        print('Supported linters: ' + ', '.join(LINTER_CONFIGS.keys()), file=sys.stderr)
        return 2

    linter = argv[0].lower()
    files = argv[1:]

    if linter in ('--help', '-h'):
        print(lint_log_main.__doc__)
        print('\nSupported linters:', ', '.join(LINTER_CONFIGS.keys()))
        return 0

    if linter not in LINTER_CONFIGS:
        print(f'Unknown linter: {linter}', file=sys.stderr)
        print('Supported: ' + ', '.join(LINTER_CONFIGS.keys()), file=sys.stderr)
        return 2

    config = LINTER_CONFIGS[linter]

    # Ensure log directory exists
    log_dir = ensure_log_dir()
    log_file = log_dir / f'{linter}.json'

    # Build the JSON output command
    json_cmd = [config['command']]
    if config['subcommand']:
        json_cmd.append(config['subcommand'])
    json_cmd.extend(config['json_args'])
    json_cmd.extend(files)

    # Build the normal display command
    normal_cmd = [config['command']]
    if config['subcommand']:
        normal_cmd.append(config['subcommand'])
    normal_cmd.extend(config['normal_args'])
    normal_cmd.extend(files)

    # Run JSON output command and save to log file
    try:
        result = subprocess.run(
            json_cmd,
            capture_output=True,
            text=True,
        )
        # Write JSON output to log file (even if command failed - that's where errors are!)
        with open(log_file, 'w') as f:
            f.write(result.stdout)
    except FileNotFoundError:
        print(f'Error: {config["command"]} not found', file=sys.stderr)
        return 127
    except Exception as e:
        print(f'Error running {linter} for JSON capture: {e}', file=sys.stderr)

    # Run normal command for display output and exit code
    try:
        result = subprocess.run(normal_cmd)
        return result.returncode
    except FileNotFoundError:
        print(f'Error: {config["command"]} not found', file=sys.stderr)
        return 127
