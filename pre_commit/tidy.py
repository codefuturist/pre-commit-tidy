"""Tidy - Automated file organization for repositories.

Moves files from source directories to target directories based on
configurable rules. Designed to be reusable across different repositories.

Usage:
    tidy [options]

Options:
    --config PATH           Path to configuration file (default: .tidyrc.yaml)
    --source DIR            Source directory (default: .)
    --target DIR            Target directory (default: 00-inbox)
    --extensions EXT        Comma-separated extensions (default: .md)
    --recursive             Recursively scan source directory
    --max-depth N           Maximum recursion depth (default: unlimited)
    --exclude-dirs          Comma-separated directories to exclude
    --preserve-structure    Keep directory structure when moving files
    --flatten-depth N       Preserve only N levels of directory structure
    --exclude-hidden        Exclude hidden files (starting with .)
    --exclude-symlinks      Exclude symbolic links
    --min-size SIZE         Minimum file size (e.g., 1KB, 5MB)
    --max-size SIZE         Maximum file size (e.g., 100MB)
    --modified-after DATE   Only files modified after date (YYYY-MM-DD)
    --modified-before DATE  Only files modified before date (YYYY-MM-DD)
    --collision-keep MODE   How to handle collisions: both|newest|largest|source|target
    --rename-pattern PAT    Pattern for renamed files (default: {name}-{timestamp})
    --delete-mode MODE      How to delete files: trash (recoverable) or permanent
    --trash FILE [FILE ...] Move specified files to system trash
    --dry-run               Preview changes without moving files
    --verbose               Show detailed output
    --quiet                 Suppress all output except errors
    --undo                  Undo the most recent tidy operation
    --undo-list             List available undo operations
    --undo-id ID            Undo a specific operation by ID
    --dedup-by-content      Detect duplicates by file content hash
    --help                  Show this help message
    --version               Show version number

Smart Architecture Features:
    --preset TYPE           Use preset rules for project type
                            (python|node|go|rust|java|ruby|php|dotnet|generic)
    --analyze               Analyze repo structure and suggest rules (no changes)
    --detect-archives       Flag backup/archive files (*.bak, *backup*, *old*)
    --detect-orphans        Flag files not referenced in the codebase
    --interactive, -i       Interactively confirm each file move

Configuration:
    Create a .tidyrc.yaml file in your project root:

    {
        "source_dir": ".",
        "target_dir": "docs",
        "extensions": [".md", ".txt"],
        "exclude_files": ["readme.md", "changelog.md"],
        "exclude_patterns": ["*.config.*"],
        "exclude_dirs": ["node_modules", ".git", "__pycache__"],
        "duplicate_strategy": "rename",
        "dedup_by_content": false,
        "recursive": false,
        "max_depth": null,
        "preserve_structure": false,
        "flatten_depth": null,
        "filters": {
            "min_size": null,
            "max_size": "100MB",
            "modified_after": "2025-01-01",
            "modified_before": null,
            "exclude_hidden": true,
            "exclude_symlinks": true
        },
        "collision": {
            "keep": "both",
            "rename_pattern": "{name}-{timestamp}"
        },
        "undo_history_limit": 10,
        "project_type": "auto",
        "preset": null,
        "detect_archives": false,
        "detect_orphans": false,
        "rules": [
            {"pattern": "*.test.md", "target": "tests/"},
            {"pattern": "*.draft.*", "target": "drafts/"},
            {"extensions": [".png", ".jpg"], "target": "assets/images/"}
        ]
    }

    Rule-based routing supports four formats:
    1. Pattern matching:  {"pattern": "*.test.md", "target": "tests/"}
    2. Regex matching:    {"regex": "^test_.*\\.py$", "target": "tests/"}
    3. Extension-based:   {"extensions": [".png"], "target": "images/"}
    4. Glob-to-folder:    {"glob": "docs/**/*.md", "target": "documentation/"}

    Regex patterns use Python's re module with case-insensitive matching.
    Both filename and relative path are checked against regex patterns.

    Exclusion patterns:
    - "exclude_patterns": Glob-style patterns (e.g., "*.bak", "temp-*")
    - "exclude_regex": Regex patterns (e.g., "^\\d{8}_backup")

    Collision handling modes:
    - "both": Rename the incoming file (default, same as old "rename")
    - "newest": Keep the file with the most recent modification time
    - "largest": Keep the larger file
    - "source": Always keep the source file (overwrite target)
    - "target": Always keep the target file (skip source)

    Rename pattern tokens:
    - {name}: Original filename without extension
    - {ext}: File extension including dot
    - {timestamp}: Unix timestamp in milliseconds
    - {date}: Date in YYYY-MM-DD format
    - {time}: Time in HH-MM-SS format
    - {hash}: First 8 characters of content hash (if available)

    Supported project presets:
    - python: Python projects (setup.py, pyproject.toml)
    - node: Node.js projects (package.json)
    - go: Go projects (go.mod)
    - rust: Rust projects (Cargo.toml)
    - java: Java projects (pom.xml, build.gradle)
    - ruby: Ruby projects (Gemfile)
    - php: PHP projects (composer.json)
    - dotnet: .NET projects (*.csproj, *.sln)
    - generic: Generic fallback

Environment Variables:
    TIDY_SOURCE_DIR     Source directory
    TIDY_TARGET_DIR     Target directory
    TIDY_EXTENSIONS     Comma-separated extensions
    TIDY_EXCLUDE_FILES  Comma-separated files to exclude
    TIDY_EXCLUDE_DIRS   Comma-separated directories to exclude
    TIDY_DRY_RUN        Set to 'true' for dry run
    TIDY_VERBOSE        Set to 'true' for verbose output
    TIDY_RECURSIVE      Set to 'true' for recursive scanning

Examples:
    tidy --analyze                    # Analyze repo and suggest rules
    tidy --preset python              # Use Python project preset
    tidy --detect-archives            # Flag backup/archive files
    tidy --interactive                # Confirm each move interactively
    tidy --dry-run --verbose          # Preview all changes
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from enum import Enum
from pathlib import Path
from typing import Any
from typing import TypedDict

import yaml

# Cross-platform trash support
try:
    from send2trash import send2trash
    TRASH_AVAILABLE = True
except ImportError:
    TRASH_AVAILABLE = False

    def send2trash(path: str | Path) -> None:  # type: ignore[misc]
        """Fallback when send2trash is not installed."""
        raise RuntimeError(
            "send2trash is not installed. Install with: pip install send2trash"
        )

# Version
__version__ = '1.2.0'

# Default configuration file names to search for
CONFIG_FILE_NAMES = ['.tidyrc.yaml', '.tidyrc.yml', 'tidy.config.yaml']

# Undo history directory (replaces single file for persistent history)
UNDO_HISTORY_DIR = '.tidy-undo'

# Legacy single-file manifest (for backward compatibility)
UNDO_MANIFEST_FILE = '.tidy-undo.json'

# Default undo history limit
DEFAULT_UNDO_HISTORY_LIMIT = 10


# =============================================================================
# Project Type Detection and Presets
# =============================================================================

class ProjectType(Enum):
    """Supported project types for auto-detection."""

    AUTO = 'auto'  # Auto-detect from markers
    PYTHON = 'python'
    NODE = 'node'
    GO = 'go'
    RUST = 'rust'
    JAVA = 'java'
    RUBY = 'ruby'
    PHP = 'php'
    DOTNET = 'dotnet'
    SWIFT = 'swift'
    KOTLIN = 'kotlin'
    SCALA = 'scala'
    ELIXIR = 'elixir'
    HASKELL = 'haskell'
    C_CPP = 'c_cpp'
    TERRAFORM = 'terraform'
    DOCKER = 'docker'
    MONOREPO = 'monorepo'
    GENERIC = 'generic'  # No specific type detected


class DetectionConfidence(Enum):
    """Confidence level of project type detection."""

    HIGH = 'high'  # Primary marker file found (e.g., package.json for Node)
    MEDIUM = 'medium'  # Secondary markers or conventions match
    LOW = 'low'  # Only weak indicators present
    NONE = 'none'  # No indicators, using generic


@dataclass
class DetectionResult:
    """Result of project type detection with confidence."""

    project_type: str
    confidence: DetectionConfidence
    markers_found: list[str]
    all_detected_types: list[tuple[str, DetectionConfidence, list[str]]]  # Other potential types  # noqa: E501

    @property
    def is_confident(self) -> bool:
        """Return True if detection confidence is HIGH or MEDIUM."""
        return self.confidence in (DetectionConfidence.HIGH, DetectionConfidence.MEDIUM)


# Marker files with confidence weights for project type detection
# Format: {project_type: [(marker, confidence), ...]}
# Higher confidence = more definitive marker
PROJECT_MARKERS: dict[str, list[tuple[str, DetectionConfidence]]] = {
    'python': [
        ('pyproject.toml', DetectionConfidence.HIGH),
        ('setup.py', DetectionConfidence.HIGH),
        ('setup.cfg', DetectionConfidence.HIGH),
        ('Pipfile', DetectionConfidence.HIGH),
        ('poetry.lock', DetectionConfidence.HIGH),
        ('requirements.txt', DetectionConfidence.MEDIUM),
        ('tox.ini', DetectionConfidence.MEDIUM),
        ('.python-version', DetectionConfidence.MEDIUM),
        ('__init__.py', DetectionConfidence.LOW),
        ('*.py', DetectionConfidence.LOW),
    ],
    'node': [
        ('package.json', DetectionConfidence.HIGH),
        ('package-lock.json', DetectionConfidence.HIGH),
        ('yarn.lock', DetectionConfidence.HIGH),
        ('pnpm-lock.yaml', DetectionConfidence.HIGH),
        ('bun.lockb', DetectionConfidence.HIGH),
        ('tsconfig.json', DetectionConfidence.MEDIUM),
        ('jsconfig.json', DetectionConfidence.MEDIUM),
        ('.nvmrc', DetectionConfidence.MEDIUM),
        ('.node-version', DetectionConfidence.MEDIUM),
        ('webpack.config.js', DetectionConfidence.MEDIUM),
        ('vite.config.*', DetectionConfidence.MEDIUM),
        ('next.config.*', DetectionConfidence.MEDIUM),
        ('nuxt.config.*', DetectionConfidence.MEDIUM),
    ],
    'go': [
        ('go.mod', DetectionConfidence.HIGH),
        ('go.sum', DetectionConfidence.HIGH),
        ('go.work', DetectionConfidence.HIGH),
        ('*.go', DetectionConfidence.MEDIUM),
        ('cmd/', DetectionConfidence.MEDIUM),
        ('pkg/', DetectionConfidence.LOW),
    ],
    'rust': [
        ('Cargo.toml', DetectionConfidence.HIGH),
        ('Cargo.lock', DetectionConfidence.HIGH),
        ('rust-toolchain.toml', DetectionConfidence.HIGH),
        ('rust-toolchain', DetectionConfidence.MEDIUM),
        ('*.rs', DetectionConfidence.MEDIUM),
    ],
    'java': [
        ('pom.xml', DetectionConfidence.HIGH),
        ('build.gradle', DetectionConfidence.HIGH),
        ('build.gradle.kts', DetectionConfidence.HIGH),
        ('settings.gradle', DetectionConfidence.HIGH),
        ('settings.gradle.kts', DetectionConfidence.HIGH),
        ('gradlew', DetectionConfidence.MEDIUM),
        ('mvnw', DetectionConfidence.MEDIUM),
        ('.mvn/', DetectionConfidence.MEDIUM),
        ('*.java', DetectionConfidence.MEDIUM),
        ('src/main/java/', DetectionConfidence.MEDIUM),
    ],
    'ruby': [
        ('Gemfile', DetectionConfidence.HIGH),
        ('Gemfile.lock', DetectionConfidence.HIGH),
        ('*.gemspec', DetectionConfidence.HIGH),
        ('Rakefile', DetectionConfidence.MEDIUM),
        ('.ruby-version', DetectionConfidence.MEDIUM),
        ('.ruby-gemset', DetectionConfidence.MEDIUM),
        ('config.ru', DetectionConfidence.MEDIUM),
        ('*.rb', DetectionConfidence.LOW),
    ],
    'php': [
        ('composer.json', DetectionConfidence.HIGH),
        ('composer.lock', DetectionConfidence.HIGH),
        ('artisan', DetectionConfidence.HIGH),  # Laravel
        ('phpunit.xml', DetectionConfidence.MEDIUM),
        ('phpunit.xml.dist', DetectionConfidence.MEDIUM),
        ('.php-version', DetectionConfidence.MEDIUM),
        ('*.php', DetectionConfidence.LOW),
    ],
    'dotnet': [
        ('*.sln', DetectionConfidence.HIGH),
        ('*.csproj', DetectionConfidence.HIGH),
        ('*.fsproj', DetectionConfidence.HIGH),
        ('*.vbproj', DetectionConfidence.HIGH),
        ('nuget.config', DetectionConfidence.MEDIUM),
        ('global.json', DetectionConfidence.MEDIUM),
        ('packages.config', DetectionConfidence.MEDIUM),
        ('*.cs', DetectionConfidence.LOW),
    ],
    'swift': [
        ('Package.swift', DetectionConfidence.HIGH),
        ('*.xcodeproj', DetectionConfidence.HIGH),
        ('*.xcworkspace', DetectionConfidence.HIGH),
        ('Podfile', DetectionConfidence.MEDIUM),
        ('Cartfile', DetectionConfidence.MEDIUM),
        ('*.swift', DetectionConfidence.MEDIUM),
    ],
    'kotlin': [
        ('build.gradle.kts', DetectionConfidence.MEDIUM),  # Could also be Java
        ('settings.gradle.kts', DetectionConfidence.MEDIUM),
        ('*.kt', DetectionConfidence.HIGH),
        ('*.kts', DetectionConfidence.MEDIUM),
    ],
    'scala': [
        ('build.sbt', DetectionConfidence.HIGH),
        ('project/build.properties', DetectionConfidence.HIGH),
        ('*.scala', DetectionConfidence.MEDIUM),
        ('.scalafix.conf', DetectionConfidence.MEDIUM),
    ],
    'elixir': [
        ('mix.exs', DetectionConfidence.HIGH),
        ('mix.lock', DetectionConfidence.HIGH),
        ('*.ex', DetectionConfidence.MEDIUM),
        ('*.exs', DetectionConfidence.MEDIUM),
    ],
    'haskell': [
        ('*.cabal', DetectionConfidence.HIGH),
        ('stack.yaml', DetectionConfidence.HIGH),
        ('cabal.project', DetectionConfidence.HIGH),
        ('*.hs', DetectionConfidence.MEDIUM),
        ('Setup.hs', DetectionConfidence.MEDIUM),
    ],
    'c_cpp': [
        ('CMakeLists.txt', DetectionConfidence.HIGH),
        ('Makefile', DetectionConfidence.MEDIUM),  # Could be many languages
        ('configure.ac', DetectionConfidence.HIGH),
        ('meson.build', DetectionConfidence.HIGH),
        ('conanfile.txt', DetectionConfidence.HIGH),
        ('vcpkg.json', DetectionConfidence.HIGH),
        ('*.c', DetectionConfidence.MEDIUM),
        ('*.cpp', DetectionConfidence.MEDIUM),
        ('*.h', DetectionConfidence.LOW),
        ('*.hpp', DetectionConfidence.LOW),
    ],
    'terraform': [
        ('*.tf', DetectionConfidence.HIGH),
        ('*.tfvars', DetectionConfidence.HIGH),
        ('.terraform.lock.hcl', DetectionConfidence.HIGH),
        ('terraform.tfstate', DetectionConfidence.MEDIUM),
    ],
    'docker': [
        ('Dockerfile', DetectionConfidence.HIGH),
        ('docker-compose.yml', DetectionConfidence.HIGH),
        ('docker-compose.yaml', DetectionConfidence.HIGH),
        ('compose.yml', DetectionConfidence.HIGH),
        ('compose.yaml', DetectionConfidence.HIGH),
        ('.dockerignore', DetectionConfidence.MEDIUM),
    ],
    'monorepo': [
        ('lerna.json', DetectionConfidence.HIGH),
        ('pnpm-workspace.yaml', DetectionConfidence.HIGH),
        ('nx.json', DetectionConfidence.HIGH),
        ('turbo.json', DetectionConfidence.HIGH),
        ('rush.json', DetectionConfidence.HIGH),
        ('packages/', DetectionConfidence.MEDIUM),
        ('apps/', DetectionConfidence.LOW),
    ],
}

# Standard directory conventions per project type
LANGUAGE_CONVENTIONS: dict[str, dict[str, list[str]]] = {
    'python': {
        'source': ['src/', 'lib/'],
        'tests': ['tests/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.', 'config/'],
        'assets': ['assets/', 'resources/', 'static/'],
    },
    'node': {
        'source': ['src/', 'lib/', 'app/'],
        'tests': ['test/', '__tests__/', 'tests/', 'spec/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.', 'config/'],
        'assets': ['public/', 'assets/', 'static/'],
    },
    'go': {
        'source': ['cmd/', 'pkg/', 'internal/'],
        'tests': ['.'],  # Go tests live alongside code
        'docs': ['docs/', 'doc/'],
        'config_root': ['.', 'configs/'],
        'assets': ['assets/', 'web/'],
    },
    'rust': {
        'source': ['src/'],
        'tests': ['tests/', 'src/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.'],
        'assets': ['assets/', 'resources/'],
    },
    'java': {
        'source': ['src/main/java/', 'src/'],
        'tests': ['src/test/java/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.', 'src/main/resources/'],
        'assets': ['src/main/resources/', 'assets/'],
    },
    'ruby': {
        'source': ['lib/', 'app/'],
        'tests': ['test/', 'spec/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.', 'config/'],
        'assets': ['assets/', 'public/'],
    },
    'php': {
        'source': ['src/', 'app/', 'lib/'],
        'tests': ['tests/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.', 'config/'],
        'assets': ['public/', 'assets/', 'resources/'],
    },
    'dotnet': {
        'source': ['src/', 'Source/'],
        'tests': ['tests/', 'Tests/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.'],
        'assets': ['assets/', 'wwwroot/'],
    },
    'swift': {
        'source': ['Sources/', 'src/'],
        'tests': ['Tests/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.'],
        'assets': ['Resources/', 'assets/'],
    },
    'kotlin': {
        'source': ['src/main/kotlin/', 'src/'],
        'tests': ['src/test/kotlin/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.'],
        'assets': ['src/main/resources/', 'assets/'],
    },
    'scala': {
        'source': ['src/main/scala/', 'src/'],
        'tests': ['src/test/scala/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.', 'project/'],
        'assets': ['src/main/resources/', 'assets/'],
    },
    'elixir': {
        'source': ['lib/'],
        'tests': ['test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.', 'config/'],
        'assets': ['priv/', 'assets/'],
    },
    'haskell': {
        'source': ['src/', 'lib/', 'app/'],
        'tests': ['test/', 'tests/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.'],
        'assets': ['assets/', 'resources/'],
    },
    'c_cpp': {
        'source': ['src/', 'lib/', 'source/'],
        'tests': ['tests/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.', 'cmake/'],
        'assets': ['assets/', 'resources/'],
    },
    'terraform': {
        'source': ['.', 'modules/'],
        'tests': ['tests/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.'],
        'assets': ['templates/'],
    },
    'docker': {
        'source': ['.'],
        'tests': ['tests/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.'],
        'assets': ['scripts/', 'configs/'],
    },
    'monorepo': {
        'source': ['packages/', 'apps/', 'libs/'],
        'tests': ['tests/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.'],
        'assets': ['assets/', 'shared/'],
    },
    'generic': {
        'source': ['src/', 'lib/'],
        'tests': ['tests/', 'test/'],
        'docs': ['docs/', 'doc/'],
        'config_root': ['.'],
        'assets': ['assets/', 'resources/'],
    },
}

# Files that should stay in root for each project type
ROOT_FILES: dict[str, list[str]] = {
    'python': [
        'setup.py', 'setup.cfg', 'pyproject.toml', 'requirements*.txt',
        'tox.ini', 'Makefile', 'MANIFEST.in', '.pre-commit-config.yaml',
        'README*', 'LICENSE*', 'CHANGELOG*', 'CONTRIBUTING*', 'AUTHORS*',
        'noxfile.py', 'conftest.py', '.coveragerc', 'pytest.ini',
    ],
    'node': [
        'package.json', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
        'tsconfig.json', 'jsconfig.json', 'webpack.config.js', 'vite.config.*',
        'rollup.config.*', 'babel.config.*', '.eslintrc*', '.prettierrc*',
        'jest.config.*', 'vitest.config.*', '.npmrc', '.nvmrc',
        'README*', 'LICENSE*', 'CHANGELOG*', 'CONTRIBUTING*',
    ],
    'go': [
        'go.mod', 'go.sum', 'go.work', 'Makefile', 'README*', 'LICENSE*',
        'CHANGELOG*', 'CONTRIBUTING*', '.goreleaser.yml', 'main.go',
    ],
    'rust': [
        'Cargo.toml', 'Cargo.lock', 'README*', 'LICENSE*', 'CHANGELOG*',
        'CONTRIBUTING*', 'rust-toolchain.toml', 'rust-toolchain', '.cargo/',
        'build.rs', 'clippy.toml', 'rustfmt.toml',
    ],
    'java': [
        'pom.xml', 'build.gradle', 'build.gradle.kts', 'settings.gradle',
        'settings.gradle.kts', 'gradlew', 'gradlew.bat', 'mvnw', 'mvnw.cmd',
        'README*', 'LICENSE*', 'CHANGELOG*', '.mvn/',
    ],
    'ruby': [
        'Gemfile', 'Gemfile.lock', 'Rakefile', '*.gemspec', 'README*',
        'LICENSE*', 'CHANGELOG*', '.rubocop.yml', '.rspec', 'config.ru',
    ],
    'php': [
        'composer.json', 'composer.lock', 'phpunit.xml', 'phpunit.xml.dist',
        'README*', 'LICENSE*', 'CHANGELOG*', 'artisan', '.php-cs-fixer.php',
        'phpstan.neon', 'psalm.xml',
    ],
    'dotnet': [
        '*.sln', '*.csproj', '*.fsproj', 'nuget.config', 'global.json',
        'README*', 'LICENSE*', 'CHANGELOG*', 'Directory.Build.props',
        'Directory.Packages.props', '.editorconfig',
    ],
    'swift': [
        'Package.swift', 'Package.resolved', '*.xcodeproj', '*.xcworkspace',
        'Podfile', 'Podfile.lock', 'Cartfile', 'Cartfile.resolved',
        'README*', 'LICENSE*', 'CHANGELOG*', '.swiftlint.yml',
    ],
    'kotlin': [
        'build.gradle.kts', 'settings.gradle.kts', 'gradlew', 'gradlew.bat',
        'README*', 'LICENSE*', 'CHANGELOG*', 'detekt.yml',
    ],
    'scala': [
        'build.sbt', 'project/', 'README*', 'LICENSE*', 'CHANGELOG*',
        '.scalafmt.conf', '.scalafix.conf',
    ],
    'elixir': [
        'mix.exs', 'mix.lock', 'README*', 'LICENSE*', 'CHANGELOG*',
        '.formatter.exs', '.credo.exs',
    ],
    'haskell': [
        '*.cabal', 'stack.yaml', 'stack.yaml.lock', 'cabal.project',
        'Setup.hs', 'README*', 'LICENSE*', 'CHANGELOG*', 'hie.yaml',
    ],
    'c_cpp': [
        'CMakeLists.txt', 'Makefile', 'configure.ac', 'configure',
        'meson.build', 'meson_options.txt', 'conanfile.txt', 'conanfile.py',
        'vcpkg.json', 'README*', 'LICENSE*', 'CHANGELOG*', '.clang-format',
        '.clang-tidy',
    ],
    'terraform': [
        '*.tf', '*.tfvars', 'terraform.tfstate', '.terraform.lock.hcl',
        'README*', 'LICENSE*', 'CHANGELOG*', '.terraformrc', 'backend.tf',
        'versions.tf', 'providers.tf', 'main.tf', 'variables.tf', 'outputs.tf',
    ],
    'docker': [
        'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml',
        'compose.yml', 'compose.yaml', '.dockerignore', 'README*', 'LICENSE*',
        'CHANGELOG*',
    ],
    'monorepo': [
        'lerna.json', 'pnpm-workspace.yaml', 'nx.json', 'turbo.json',
        'rush.json', 'package.json', 'README*', 'LICENSE*', 'CHANGELOG*',
        '.npmrc', 'pnpm-lock.yaml', 'yarn.lock',
    ],
    'generic': [
        'Makefile', 'README*', 'LICENSE*', 'CHANGELOG*', 'CONTRIBUTING*',
        '.pre-commit-config.yaml', '.editorconfig', '.gitignore', '.gitattributes',
        'Dockerfile', 'docker-compose.yml', 'Vagrantfile', 'Jenkinsfile',
        '.travis.yml', '.github/', '.gitlab-ci.yml', 'azure-pipelines.yml',
    ],
}

# Archive/backup file patterns
ARCHIVE_PATTERNS: list[str] = [
    '*backup*', '*Backup*', '*BACKUP*',
    '*old*', '*Old*', '*OLD*',
    '*archive*', '*Archive*', '*ARCHIVE*',
    '*tmp*', '*temp*', '*Temp*', '*TEMP*',
    '*.bak', '*.backup', '*.old', '*.orig',
    '*~', '*.swp', '*.swo',
    '*copy*', '*Copy*', '*COPY*',
    '* (1)*', '* (2)*', '* (copy)*',
    '*_deprecated*', '*_obsolete*',
    '*.bkp', '*.save', '*.saved',
]

# Built-in presets for each project type
PRESETS: dict[str, dict[str, Any]] = {
    'python': {
        'target_dir': 'docs',
        'extensions': ['.md', '.rst', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license', 'authors.md'],  # noqa: E501
        'exclude_patterns': ['requirements*.txt'],
        'exclude_dirs': ['docs', '.git', '.github', 'tests', 'test', 'src', 'lib', '.tox', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache', 'dist', 'build', '.eggs'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'node': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'node_modules', 'src', 'lib', 'test', 'tests', '__tests__', 'dist', 'build', 'coverage', '.next', '.nuxt'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'go': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'cmd', 'pkg', 'internal', 'vendor', 'bin'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'rust': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'src', 'target', 'tests', 'benches', 'examples'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'java': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'src', 'target', 'build', '.gradle', '.idea', '.mvn'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'ruby': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'lib', 'app', 'spec', 'test', 'vendor', 'coverage'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'php': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'src', 'app', 'vendor', 'tests', 'test', 'storage', 'bootstrap'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'dotnet': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'src', 'tests', 'bin', 'obj', 'packages', '.vs'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'swift': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'Sources', 'Tests', '.build', 'Pods', 'Carthage'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'kotlin': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'src', 'build', '.gradle', '.idea'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'scala': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'src', 'target', 'project', '.bsp'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'elixir': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'lib', 'test', '_build', 'deps', 'priv'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'haskell': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'src', 'lib', 'app', 'test', '.stack-work', 'dist-newstyle'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'c_cpp': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'src', 'lib', 'include', 'build', 'cmake-build-*', 'out'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'terraform': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', '.terraform', 'modules'],
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'docker': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github'],
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'monorepo': {
        'target_dir': 'docs',
        'extensions': ['.md', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'packages', 'apps', 'libs', 'node_modules', '.turbo', '.nx'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
    'generic': {
        'target_dir': 'docs',
        'extensions': ['.md', '.rst', '.txt'],
        'exclude_files': ['readme.md', 'changelog.md', 'contributing.md', 'license.md', 'license'],  # noqa: E501
        'exclude_dirs': ['docs', '.git', '.github', 'src', 'lib', 'tests', 'test', 'vendor', 'node_modules'],  # noqa: E501
        'recursive': False,
        'filters': {'exclude_hidden': True},
    },
}


def detect_project_type(root_dir: Path, warn_on_low_confidence: bool = True) -> str:
    """Detect project type based on marker files.

    Returns the project type string (e.g., 'python', 'node') or 'generic'.
    """
    result = detect_project_type_with_confidence(root_dir)

    if warn_on_low_confidence and result.confidence == DetectionConfidence.LOW:
        import sys
        print(
            f"{Colors.YELLOW}Warning:{Colors.RESET} Low confidence detection of "
            f"'{result.project_type}' project type. Found markers: "
            f"{', '.join(result.markers_found)}",
            file=sys.stderr,
        )
        if result.all_detected_types:
            other_types = [
                f"{t} ({c.value})" for t, c, _ in result.all_detected_types
                if t != result.project_type
            ][:3]
            if other_types:
                print(
                    f"         Other possible types: {', '.join(other_types)}",
                    file=sys.stderr,
                )
        print(
            "         Use --preset to specify explicitly, or add more marker files.",
            file=sys.stderr,
        )

    return result.project_type


def detect_project_type_with_confidence(root_dir: Path) -> DetectionResult:
    """Detect project type with confidence level.

    Returns a DetectionResult with the project type, confidence level,
    and markers found.
    """
    all_detected: list[tuple[str, DetectionConfidence, list[str]]] = []

    for project_type, markers in PROJECT_MARKERS.items():
        found_markers: list[str] = []
        best_confidence = DetectionConfidence.NONE

        for marker, confidence in markers:
            marker_found = False

            if marker.endswith('/'):
                # Directory check
                if (root_dir / marker.rstrip('/')).is_dir():
                    marker_found = True
            elif '*' in marker:
                # Glob pattern
                if list(root_dir.glob(marker)):
                    marker_found = True
            elif (root_dir / marker).exists():
                marker_found = True

            if marker_found:
                found_markers.append(marker)
                # Keep highest confidence found
                if confidence.value < best_confidence.value or best_confidence == DetectionConfidence.NONE:  # noqa: E501
                    # Enum values: HIGH < MEDIUM < LOW < NONE (alphabetically)
                    # We want HIGH > MEDIUM > LOW > NONE
                    conf_order = {
                        DetectionConfidence.HIGH: 0,
                        DetectionConfidence.MEDIUM: 1,
                        DetectionConfidence.LOW: 2,
                        DetectionConfidence.NONE: 3,
                    }
                    if conf_order[confidence] < conf_order[best_confidence]:
                        best_confidence = confidence

        if found_markers:
            all_detected.append((project_type, best_confidence, found_markers))

    # Sort by confidence (HIGH first), then by number of markers found
    conf_order = {
        DetectionConfidence.HIGH: 0,
        DetectionConfidence.MEDIUM: 1,
        DetectionConfidence.LOW: 2,
        DetectionConfidence.NONE: 3,
    }
    all_detected.sort(key=lambda x: (conf_order[x[1]], -len(x[2])))

    if all_detected:
        best_type, best_conf, best_markers = all_detected[0]
        return DetectionResult(
            project_type=best_type,
            confidence=best_conf,
            markers_found=best_markers,
            all_detected_types=all_detected,
        )

    return DetectionResult(
        project_type='generic',
        confidence=DetectionConfidence.NONE,
        markers_found=[],
        all_detected_types=[],
    )


def get_preset(project_type: str) -> dict[str, Any]:
    """Get the preset configuration for a project type."""
    return PRESETS.get(project_type, PRESETS['generic']).copy()


def is_root_file(filename: str, project_type: str) -> bool:
    """Check if a file should stay in the root directory."""
    patterns = ROOT_FILES.get(project_type, ROOT_FILES['generic'])
    for pattern in patterns:
        if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(filename.lower(), pattern.lower()):  # noqa: E501
            return True
    return False


def is_archive_file(filename: str) -> bool:
    """Check if a file looks like a backup/archive file."""
    for pattern in ARCHIVE_PATTERNS:
        if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(filename.lower(), pattern.lower()):  # noqa: E501
            return True
    return False


def is_test_file(filepath: Path, project_type: str) -> bool:
    """Check if a file is a test file based on naming conventions."""
    name = filepath.name.lower()
    stem = filepath.stem.lower()

    # Common test patterns
    if name.startswith('test_') or name.endswith('_test.py'):
        return True
    if stem.endswith('_test') or stem.endswith('.test') or stem.endswith('_spec'):
        return True
    if stem.startswith('test') and filepath.suffix in ['.py', '.js', '.ts', '.go', '.rs']:  # noqa: E501
        return True

    # Check if in a test directory
    parts = [p.lower() for p in filepath.parts]
    if any(p in ['tests', 'test', '__tests__', 'spec'] for p in parts):
        return True

    return False


def parse_size(size_str: str | int) -> int:
    """Parse human-readable size string to bytes.

    Examples: "1KB", "5MB", "100GB", "1024" (bytes)
    """
    if isinstance(size_str, int):
        return size_str

    size_str = size_str.strip().upper()
    units = {
        'B': 1,
        'KB': 1024,
        'MB': 1024 ** 2,
        'GB': 1024 ** 3,
        'TB': 1024 ** 4,
        'K': 1024,
        'M': 1024 ** 2,
        'G': 1024 ** 3,
    }

    # Try to match number with optional unit
    match = re.match(r'^(\d+(?:\.\d+)?)\s*([A-Z]*)$', size_str)
    if not match:
        raise ValueError(f"Invalid size format: {size_str}")

    number = float(match.group(1))
    unit = match.group(2) or 'B'

    if unit not in units:
        raise ValueError(f"Unknown size unit: {unit}")

    return int(number * units[unit])


def parse_date(date_str: str | datetime) -> datetime:
    """Parse date string to datetime.

    Supports: "YYYY-MM-DD", "YYYY-MM-DDTHH:MM:SS", ISO format
    """
    if isinstance(date_str, datetime):
        return date_str

    date_str = date_str.strip()

    # Try common formats
    formats = [
        '%Y-%m-%d',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%d %H:%M:%S',
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # Try ISO format
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except ValueError:
        pass

    raise ValueError(f"Invalid date format: {date_str}")


# Pre-commit config file
PRE_COMMIT_CONFIG = '.pre-commit-config.yaml'

# Default directories to exclude from recursive scanning
DEFAULT_EXCLUDE_DIRS = [
    '.git',
    '.hg',
    '.svn',
    'node_modules',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.tox',
    '.venv',
    'venv',
    '.env',
    'dist',
    'build',
    '.eggs',
]


class DuplicateStrategy(Enum):
    """How to handle duplicate files (legacy, for backward compatibility)."""

    RENAME = 'rename'
    SKIP = 'skip'
    OVERWRITE = 'overwrite'


class CollisionKeep(Enum):
    """What to keep when files collide."""

    BOTH = 'both'  # Rename incoming file (same as old RENAME)
    NEWEST = 'newest'  # Keep file with most recent mtime
    LARGEST = 'largest'  # Keep larger file
    SOURCE = 'source'  # Always overwrite with source
    TARGET = 'target'  # Always keep target (skip source)


class DeleteMode(Enum):
    """How to handle file deletion."""

    TRASH = 'trash'  # Move to system trash (default, recoverable)
    PERMANENT = 'permanent'  # Permanently delete (unrecoverable)


class OperationStatus(Enum):
    """Status of a file operation."""

    MOVED = 'moved'
    SKIPPED = 'skipped'
    FAILED = 'failed'
    DUPLICATE = 'duplicate'
    TRASHED = 'trashed'
    DELETED = 'deleted'


@dataclass
class FileOperation:
    """Result of a single file operation."""

    source: Path
    destination: Path | None = None
    status: OperationStatus = OperationStatus.SKIPPED
    reason: str | None = None
    content_hash: str | None = None


@dataclass
class UndoOperation:
    """Record for undoing a file operation."""

    original_path: str
    moved_to_path: str
    timestamp: str


@dataclass
class UndoManifest:
    """Manifest of operations for undo capability."""

    operations: list[UndoOperation] = field(default_factory=list)
    created_at: str = ''
    dry_run: bool = False
    manifest_id: str = ''  # Unique ID for this manifest (timestamp-based)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'created_at': self.created_at,
            'dry_run': self.dry_run,
            'manifest_id': self.manifest_id,
            'operations': [
                {
                    'original_path': op.original_path,
                    'moved_to_path': op.moved_to_path,
                    'timestamp': op.timestamp,
                }
                for op in self.operations
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UndoManifest:
        """Create from dictionary."""
        manifest = cls()
        manifest.created_at = data.get('created_at', '')
        manifest.dry_run = data.get('dry_run', False)
        manifest.manifest_id = data.get('manifest_id', '')
        manifest.operations = [
            UndoOperation(
                original_path=op['original_path'],
                moved_to_path=op['moved_to_path'],
                timestamp=op['timestamp'],
            )
            for op in data.get('operations', [])
        ]
        return manifest


@dataclass
class FilterConfig:
    """Configuration for file metadata filters."""

    min_size: int | None = None  # Minimum file size in bytes
    max_size: int | None = None  # Maximum file size in bytes
    modified_after: datetime | None = None  # Only files modified after this time  # noqa: E501
    modified_before: datetime | None = None  # Only files modified before this time  # noqa: E501
    exclude_hidden: bool = False  # Exclude files starting with .
    exclude_symlinks: bool = False  # Exclude symbolic links

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FilterConfig:
        """Create from dictionary."""
        config = cls()
        if 'min_size' in data and data['min_size']:
            config.min_size = parse_size(data['min_size'])
        if 'max_size' in data and data['max_size']:
            config.max_size = parse_size(data['max_size'])
        if 'modified_after' in data and data['modified_after']:
            config.modified_after = parse_date(data['modified_after'])
        if 'modified_before' in data and data['modified_before']:
            config.modified_before = parse_date(data['modified_before'])
        if 'exclude_hidden' in data:
            config.exclude_hidden = data['exclude_hidden']
        if 'exclude_symlinks' in data:
            config.exclude_symlinks = data['exclude_symlinks']
        return config


@dataclass
class CollisionConfig:
    """Configuration for collision handling."""

    keep: CollisionKeep = CollisionKeep.BOTH
    rename_pattern: str = '{name}-{timestamp}'

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollisionConfig:
        """Create from dictionary."""
        config = cls()
        if 'keep' in data:
            config.keep = CollisionKeep(data['keep'])
        if 'rename_pattern' in data:
            config.rename_pattern = data['rename_pattern']
        return config


@dataclass
class TidyResult:
    """Result of the tidy operation."""

    moved: list[FileOperation] = field(default_factory=list)
    skipped: list[FileOperation] = field(default_factory=list)
    failed: list[FileOperation] = field(default_factory=list)
    total_processed: int = 0
    dry_run: bool = False
    directories_scanned: int = 0


class RuleDict(TypedDict, total=False):
    """Rule dictionary type for routing files."""

    pattern: str  # Glob pattern like "*.test.md"
    extensions: list[str]  # List of extensions like [".png", ".jpg"]
    glob: str  # Full glob pattern like "docs/**/*.md"
    target: str  # Target directory


class FilterDict(TypedDict, total=False):
    """Filter configuration dictionary type."""

    min_size: str | int | None
    max_size: str | int | None
    modified_after: str | None
    modified_before: str | None
    exclude_hidden: bool
    exclude_symlinks: bool


class CollisionDict(TypedDict, total=False):
    """Collision configuration dictionary type."""

    keep: str
    rename_pattern: str


class ConfigDict(TypedDict, total=False):
    """Configuration dictionary type."""

    source_dir: str
    target_dir: str
    extensions: list[str]
    exclude_files: list[str]
    exclude_patterns: list[str]
    exclude_dirs: list[str]
    duplicate_strategy: str
    dedup_by_content: bool
    recursive: bool
    max_depth: int | None
    preserve_structure: bool
    flatten_depth: int | None
    filters: FilterDict
    collision: CollisionDict
    undo_history_limit: int
    rules: list[RuleDict]


@dataclass
class RoutingRule:
    """A rule for routing files to specific targets.

    Supports multiple matching modes:
    - pattern: Glob-style pattern (e.g., "*.test.md", "draft-*")
    - regex: Regular expression pattern (e.g., "^test_.*\\.py$")
    - extensions: List of file extensions (e.g., [".png", ".jpg"])
    - glob: Full path glob pattern (e.g., "docs/**/*.md")
    """

    target: str
    pattern: str | None = None  # Glob-style pattern for filename
    regex: str | None = None  # Regex pattern for filename
    extensions: list[str] | None = None
    glob: str | None = None  # Full path glob pattern
    _compiled_regex: re.Pattern[str] | None = field(default=None, repr=False, compare=False)  # noqa: E501

    def __post_init__(self) -> None:
        """Compile regex pattern if provided."""
        if self.regex:
            try:
                self._compiled_regex = re.compile(self.regex, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{self.regex}': {e}")

    def matches(self, file_path: Path, relative_path: str) -> bool:
        """Check if this rule matches the given file."""
        filename = file_path.name

        # Regex matching (e.g., "^test_.*\\.py$")
        if self._compiled_regex:
            if self._compiled_regex.search(filename):
                return True
            # Also try matching against relative path for path-based regex
            if self._compiled_regex.search(relative_path):
                return True

        # Pattern matching (e.g., "*.test.md") - glob-style
        if self.pattern:
            if fnmatch.fnmatch(filename.lower(), self.pattern.lower()):
                return True

        # Extension matching (e.g., [".png", ".jpg"])
        if self.extensions:
            ext = file_path.suffix.lower()
            if ext in [e.lower() for e in self.extensions]:
                return True

        # Full glob matching (e.g., "docs/**/*.md")
        # Convert ** to work with fnmatch by expanding recursively
        if self.glob:
            glob_pattern = self.glob.lower()
            path_lower = relative_path.lower()

            # Handle ** for recursive matching
            if '**' in glob_pattern:
                # Replace ** with a pattern that matches any path segments
                # Split on ** and check if parts match
                parts = glob_pattern.split('**')
                if len(parts) == 2:
                    prefix, suffix = parts
                    # Remove leading/trailing slashes
                    prefix = prefix.rstrip('/')
                    suffix = suffix.lstrip('/')

                    # Check if path starts with prefix and ends matching suffix
                    if prefix and not path_lower.startswith(prefix + '/') and path_lower != prefix:  # noqa: E501
                        if not path_lower.startswith(prefix):
                            return False

                    if suffix:
                        return fnmatch.fnmatch(path_lower, f"*{suffix}")
                    return True
            else:
                if fnmatch.fnmatch(path_lower, glob_pattern):
                    return True

        return False


@dataclass
class TidyConfig:
    """Configuration for the tidy operation."""

    root_dir: Path = field(default_factory=Path.cwd)
    source_dir: str = '.'
    target_dir: str = '00-inbox'
    extensions: list[str] = field(default_factory=lambda: ['.md'])
    exclude_files: list[str] = field(
        default_factory=lambda: [
            'readme.md',
            'changelog.md',
            'license.md',
            'contributing.md',
        ],
    )
    exclude_patterns: list[str] = field(default_factory=list)  # Glob patterns
    exclude_regex: list[str] = field(default_factory=list)  # Regex patterns
    _compiled_exclude_regex: list[re.Pattern[str]] = field(default_factory=list, repr=False)  # noqa: E501
    exclude_dirs: list[str] = field(default_factory=lambda: DEFAULT_EXCLUDE_DIRS.copy())  # noqa: E501
    duplicate_strategy: DuplicateStrategy = DuplicateStrategy.RENAME
    dedup_by_content: bool = False
    recursive: bool = False
    max_depth: int | None = None
    preserve_structure: bool = False
    flatten_depth: int | None = None
    filters: FilterConfig = field(default_factory=FilterConfig)
    collision: CollisionConfig = field(default_factory=CollisionConfig)
    delete_mode: DeleteMode = DeleteMode.TRASH  # How to handle file deletion
    undo_history_limit: int = DEFAULT_UNDO_HISTORY_LIMIT
    create_legacy_undo_file: bool = False
    # Smart architecture features
    project_type: str = 'auto'  # auto, python, node, go, rust, java, generic
    preset: str | None = None  # Use preset config for project type
    detect_archives: bool = False  # Flag archive/backup files
    detect_orphans: bool = False  # Flag files not referenced in codebase
    interactive: bool = False  # Ask before moving ambiguous files
    analyze_only: bool = False  # Just analyze, don't move (implies dry_run)
    rules: list[RoutingRule] = field(default_factory=list)
    dry_run: bool = False
    verbosity: int = 1  # 0=quiet, 1=normal, 2=verbose

    @classmethod
    def from_dict(cls, data: ConfigDict, root_dir: Path | None = None) -> TidyConfig:  # noqa: E501
        """Create config from dictionary."""
        config = cls()
        if root_dir:
            config.root_dir = root_dir

        if 'source_dir' in data:
            config.source_dir = data['source_dir']
        if 'target_dir' in data:
            config.target_dir = data['target_dir']
        if 'extensions' in data:
            config.extensions = data['extensions']
        if 'exclude_files' in data:
            config.exclude_files = data['exclude_files']
        if 'exclude_patterns' in data:
            config.exclude_patterns = data['exclude_patterns']
        if 'exclude_regex' in data:
            config.exclude_regex = data['exclude_regex']
            # Compile regex patterns
            for pattern in config.exclude_regex:
                try:
                    config._compiled_exclude_regex.append(
                        re.compile(pattern, re.IGNORECASE),
                    )
                except re.error as e:
                    raise ValueError(f"Invalid exclude_regex pattern '{pattern}': {e}")  # noqa: E501
        if 'exclude_dirs' in data:
            config.exclude_dirs = data['exclude_dirs']
        if 'duplicate_strategy' in data:
            config.duplicate_strategy = DuplicateStrategy(data['duplicate_strategy'])  # noqa: E501
        if 'dedup_by_content' in data:
            config.dedup_by_content = data['dedup_by_content']
        if 'recursive' in data:
            config.recursive = data['recursive']
        if 'max_depth' in data:
            config.max_depth = data['max_depth']
        if 'preserve_structure' in data:
            config.preserve_structure = data['preserve_structure']
        if 'flatten_depth' in data:
            config.flatten_depth = data['flatten_depth']
        if 'filters' in data:
            config.filters = FilterConfig.from_dict(data['filters'])  # type: ignore[arg-type]  # noqa: E501
        if 'collision' in data:
            config.collision = CollisionConfig.from_dict(data['collision'])  # type: ignore[arg-type]  # noqa: E501
        if 'delete_mode' in data:
            config.delete_mode = DeleteMode(data['delete_mode'])
        if 'undo_history_limit' in data:
            config.undo_history_limit = data['undo_history_limit']
        if 'create_legacy_undo_file' in data:
            config.create_legacy_undo_file = data['create_legacy_undo_file']
        # Smart architecture features
        if 'project_type' in data:
            config.project_type = data['project_type']
        if 'preset' in data:
            config.preset = data['preset']
        if 'detect_archives' in data:
            config.detect_archives = data['detect_archives']
        if 'detect_orphans' in data:
            config.detect_orphans = data['detect_orphans']
        if 'interactive' in data:
            config.interactive = data['interactive']
        if 'analyze_only' in data:
            config.analyze_only = data['analyze_only']
        if 'rules' in data:
            config.rules = [
                RoutingRule(
                    target=rule['target'],
                    pattern=rule.get('pattern'),
                    regex=rule.get('regex'),
                    extensions=rule.get('extensions'),
                    glob=rule.get('glob'),
                )
                for rule in data['rules']
            ]

        return config

    @classmethod
    def from_preset(cls, project_type: str, root_dir: Path | None = None) -> TidyConfig:  # noqa: E501
        """Create config from a preset for the given project type."""
        preset_data = get_preset(project_type)
        config = cls.from_dict(preset_data, root_dir)  # type: ignore[arg-type]
        config.project_type = project_type
        config.preset = project_type
        return config


class Colors:
    """ANSI color codes for terminal output."""

    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    RED = '\033[31m'
    CYAN = '\033[36m'
    GRAY = '\033[90m'

    @classmethod
    def disable(cls) -> None:
        """Disable colors (for non-TTY output)."""
        cls.RESET = ''
        cls.BOLD = ''
        cls.DIM = ''
        cls.GREEN = ''
        cls.YELLOW = ''
        cls.RED = ''
        cls.CYAN = ''
        cls.GRAY = ''


class Logger:
    """Simple logger with verbosity control."""

    def __init__(self, verbosity: int = 1, dry_run: bool = False) -> None:
        self.verbosity = verbosity
        self.dry_run = dry_run

        # Disable colors if not a TTY
        if not sys.stdout.isatty():
            Colors.disable()

    def info(self, message: str) -> None:
        """Log info message."""
        if self.verbosity >= 1:
            prefix = '[DRY-RUN] ' if self.dry_run else ''
            print(f"{prefix}{message}")

    def success(self, message: str) -> None:
        """Log success message."""
        if self.verbosity >= 1:
            print(f"{Colors.GREEN}✓{Colors.RESET} {message}")

    def warn(self, message: str) -> None:
        """Log warning message."""
        if self.verbosity >= 1:
            print(f"{Colors.YELLOW}⚠{Colors.RESET} {message}")

    def error(self, message: str) -> None:
        """Log error message."""
        print(f"{Colors.RED}✗{Colors.RESET} {message}", file=sys.stderr)

    def skip(self, message: str) -> None:
        """Log skip message."""
        if self.verbosity >= 2:
            print(f"{Colors.GRAY}⊘ {message}{Colors.RESET}")

    def verbose(self, message: str) -> None:
        """Log verbose message."""
        if self.verbosity >= 2:
            print(f"{Colors.DIM}{message}{Colors.RESET}")

    def header(self, message: str) -> None:
        """Log header message."""
        if self.verbosity >= 1:
            print(f"\n{Colors.BOLD}=== {message} ==={Colors.RESET}")


def load_config_file(config_path: Path | None = None) -> ConfigDict:
    """Load configuration from YAML file."""
    root_dir = Path.cwd()

    # If explicit config path provided, try to load it
    if config_path:
        full_path = root_dir / config_path
        if full_path.exists():
            with open(full_path, encoding='utf-8') as f:
                data: ConfigDict = yaml.safe_load(f) or {}
                return data
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # Try default config file names
    for filename in CONFIG_FILE_NAMES:
        full_path = root_dir / filename
        if full_path.exists():
            with open(full_path, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
                return data

    return {}


def load_env_config() -> ConfigDict:
    """Load configuration from environment variables."""
    config: ConfigDict = {}

    if os.environ.get('TIDY_SOURCE_DIR'):
        config['source_dir'] = os.environ['TIDY_SOURCE_DIR']
    if os.environ.get('TIDY_TARGET_DIR'):
        config['target_dir'] = os.environ['TIDY_TARGET_DIR']
    if os.environ.get('TIDY_EXTENSIONS'):
        config['extensions'] = os.environ['TIDY_EXTENSIONS'].split(',')
    if os.environ.get('TIDY_EXCLUDE_FILES'):
        config['exclude_files'] = os.environ['TIDY_EXCLUDE_FILES'].split(',')
    if os.environ.get('TIDY_EXCLUDE_DIRS'):
        config['exclude_dirs'] = os.environ['TIDY_EXCLUDE_DIRS'].split(',')
    if os.environ.get('TIDY_RECURSIVE') == 'true':
        config['recursive'] = True
    if os.environ.get('TIDY_DEDUP_BY_CONTENT') == 'true':
        config['dedup_by_content'] = True

    return config


def load_pre_commit_config(root_dir: Path) -> ConfigDict:
    """Load tidy configuration from .pre-commit-config.yaml if present."""
    try:
        import yaml
    except ImportError:
        return {}

    config_path = root_dir / PRE_COMMIT_CONFIG
    if not config_path.exists():
        return {}

    try:
        with open(config_path, encoding='utf-8') as f:
            pre_commit_config = yaml.safe_load(f)

        # Look for tidy hook configuration
        for repo in pre_commit_config.get('repos', []):
            for hook in repo.get('hooks', []):
                if hook.get('id') == 'tidy':
                    # Parse args into config
                    args = hook.get('args', [])
                    return _parse_args_to_config(args)

        return {}
    except Exception:
        return {}


def _parse_args_to_config(args: list[str]) -> ConfigDict:
    """Parse CLI-style args into a config dict."""
    config: ConfigDict = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--source' and i + 1 < len(args):
            config['source_dir'] = args[i + 1]
            i += 2
        elif arg == '--target' and i + 1 < len(args):
            config['target_dir'] = args[i + 1]
            i += 2
        elif arg == '--extensions' and i + 1 < len(args):
            config['extensions'] = [e.strip() for e in args[i + 1].split(',')]
            i += 2
        elif arg == '--recursive':
            config['recursive'] = True
            i += 1
        elif arg == '--dedup-by-content':
            config['dedup_by_content'] = True
            i += 1
        else:
            i += 1
    return config


def should_exclude(filename: str, config: TidyConfig) -> tuple[bool, str | None]:  # noqa: E501
    """Check if a file should be excluded."""
    lower_filename = filename.lower()

    # Check exact filename matches
    if any(f.lower() == lower_filename for f in config.exclude_files):
        return True, 'excluded by filename'

    # Check extension
    ext = Path(filename).suffix.lower()
    if ext not in [e.lower() for e in config.extensions]:
        return True, f"extension {ext} not in allowed list"

    # Check patterns (glob-like matching)
    for pattern in config.exclude_patterns:
        if fnmatch.fnmatch(filename.lower(), pattern.lower()):
            return True, f"matches pattern: {pattern}"

    # Check regex patterns
    for regex in config._compiled_exclude_regex:
        if regex.search(filename):
            return True, f"matches regex: {regex.pattern}"

    return False, None


def should_exclude_by_metadata(
    file_path: Path, config: TidyConfig,
) -> tuple[bool, str | None]:
    """Check if a file should be excluded by metadata filters."""
    filters = config.filters

    # Check hidden files
    if filters.exclude_hidden and file_path.name.startswith('.'):
        return True, 'hidden file'

    # Check symlinks
    if filters.exclude_symlinks and file_path.is_symlink():
        return True, 'symbolic link'

    try:
        stat = file_path.stat()
    except OSError as e:
        return True, f"cannot stat file: {e}"

    # Check size filters
    if filters.min_size is not None and stat.st_size < filters.min_size:
        return True, f"file too small ({stat.st_size} < {filters.min_size} bytes)"  # noqa: E501

    if filters.max_size is not None and stat.st_size > filters.max_size:
        return True, f"file too large ({stat.st_size} > {filters.max_size} bytes)"  # noqa: E501

    # Check modification time filters
    mtime = datetime.fromtimestamp(stat.st_mtime)

    if filters.modified_after is not None and mtime < filters.modified_after:
        return True, f"modified before {filters.modified_after.date()}"

    if filters.modified_before is not None and mtime > filters.modified_before:
        return True, f"modified after {filters.modified_before.date()}"

    return False, None


def should_exclude_dir(dirname: str, config: TidyConfig) -> bool:
    """Check if a directory should be excluded from recursive scanning."""
    lower_dirname = dirname.lower()
    return any(d.lower() == lower_dirname for d in config.exclude_dirs)


def compute_file_hash(file_path: Path, algorithm: str = 'sha256') -> str:
    """Compute hash of file contents for duplicate detection."""
    hash_func = hashlib.new(algorithm)
    with open(file_path, 'rb') as f:
        # Read in chunks to handle large files
        for chunk in iter(lambda: f.read(8192), b''):
            hash_func.update(chunk)
    return hash_func.hexdigest()


def find_content_duplicate(
    file_path: Path, target_dir: Path, file_hash: str,
) -> Path | None:
    """Find if a file with the same content exists in target directory."""
    if not target_dir.exists():
        return None

    for existing_file in target_dir.iterdir():
        if existing_file.is_file():
            try:
                existing_hash = compute_file_hash(existing_file)
                if existing_hash == file_hash:
                    return existing_file
            except OSError:
                continue
    return None


def generate_unique_name(
    filename: str, pattern: str = '{name}-{timestamp}', file_hash: str | None = None,  # noqa: E501
) -> str:
    """Generate a unique filename for duplicates using configurable pattern.

    Available tokens:
    - {name}: Original filename without extension
    - {ext}: File extension including dot
    - {timestamp}: Unix timestamp in milliseconds
    - {date}: Date in YYYY-MM-DD format
    - {time}: Time in HH-MM-SS format
    - {hash}: First 8 characters of content hash (if available)
    """
    path = Path(filename)
    now = datetime.now()

    # Build replacement dictionary
    replacements = {
        'name': path.stem,
        'ext': path.suffix,
        'timestamp': str(int(time.time() * 1000)),
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H-%M-%S'),
        'hash': (file_hash or '')[:8] if file_hash else '',
    }

    # Handle {hash:N} pattern for custom hash length
    result = pattern
    hash_match = re.search(r'\{hash:(\d+)\}', result)
    if hash_match:
        hash_len = int(hash_match.group(1))
        hash_val = (file_hash or '')[:hash_len] if file_hash else ''
        result = result.replace(hash_match.group(0), hash_val)

    # Replace standard tokens
    for token, value in replacements.items():
        result = result.replace(f"{{{token}}}", value)

    # Ensure extension is present
    if not result.endswith(path.suffix) and path.suffix:
        result += path.suffix

    return result


def trash_file(
    file_path: Path,
    delete_mode: DeleteMode = DeleteMode.TRASH,
    logger: Logger | None = None,
) -> tuple[bool, str]:
    """Move a file to system trash or permanently delete it.

    Args:
        file_path: Path to the file to delete
        delete_mode: TRASH (recoverable) or PERMANENT (unrecoverable)
        logger: Optional logger for output

    Returns:
        Tuple of (success, error_message)
    """
    if not file_path.exists():
        return False, f"File does not exist: {file_path}"

    try:
        if delete_mode == DeleteMode.TRASH:
            if not TRASH_AVAILABLE:
                return False, (
                    "send2trash not installed. "
                    "Install with: pip install send2trash"
                )
            send2trash(str(file_path))
            if logger:
                logger.verbose(f"Moved to trash: {file_path}")
            return True, ""
        else:
            # Permanent delete
            if file_path.is_dir():
                shutil.rmtree(file_path)
            else:
                file_path.unlink()
            if logger:
                logger.verbose(f"Permanently deleted: {file_path}")
            return True, ""
    except Exception as e:
        return False, str(e)


def get_target_for_file(
    file_path: Path, relative_path: str, config: TidyConfig,
) -> str:
    """Determine the target directory for a file based on routing rules."""
    # Check each rule in order (first match wins)
    for rule in config.rules:
        if rule.matches(file_path, relative_path):
            return rule.target

    # Default target
    return config.target_dir


def collect_files(
    source_dir: Path,
    config: TidyConfig,
    current_depth: int = 0,
) -> list[tuple[Path, str]]:
    """Collect files to process, optionally recursively.

    Returns list of (file_path, relative_path) tuples.
    """
    files: list[tuple[Path, str]] = []

    try:
        entries = list(source_dir.iterdir())
    except PermissionError:
        return files

    for entry in entries:
        if entry.is_file():
            # Calculate relative path from config.root_dir
            try:
                relative = str(entry.relative_to(config.root_dir))
            except ValueError:
                relative = entry.name
            files.append((entry, relative))

        elif entry.is_dir() and config.recursive:
            # Check depth limit
            if config.max_depth is not None and current_depth >= config.max_depth:  # noqa: E501
                continue

            # Check if directory should be excluded
            if should_exclude_dir(entry.name, config):
                continue

            # Recursively collect files
            files.extend(
                collect_files(entry, config, current_depth + 1),
            )

    return files


def save_undo_manifest(
    manifest: UndoManifest, root_dir: Path, history_limit: int = DEFAULT_UNDO_HISTORY_LIMIT,  # noqa: E501
    create_legacy_file: bool = False,
) -> None:
    """Save the undo manifest to persistent history directory."""
    undo_dir = root_dir / UNDO_HISTORY_DIR
    undo_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique manifest ID from timestamp
    if not manifest.manifest_id:
        manifest.manifest_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')  # noqa: E501

    # Save to timestamped file
    manifest_path = undo_dir / f"{manifest.manifest_id}.yaml"
    with open(manifest_path, 'w', encoding='utf-8') as f:
        yaml.dump(manifest.to_dict(), f, default_flow_style=False, sort_keys=False)

    # Only create legacy single file if explicitly enabled
    if create_legacy_file:
        legacy_path = root_dir / UNDO_MANIFEST_FILE
        with open(legacy_path, 'w', encoding='utf-8') as f:
            yaml.dump(manifest.to_dict(), f, default_flow_style=False, sort_keys=False)

    # Cleanup old manifests beyond history limit
    _cleanup_undo_history(undo_dir, history_limit)


def _cleanup_undo_history(undo_dir: Path, limit: int) -> None:
    """Remove old undo manifests beyond the history limit."""
    if not undo_dir.exists():
        return

    manifests = sorted(undo_dir.glob('*.yaml'), key=lambda p: p.name, reverse=True)  # noqa: E501
    for old_manifest in manifests[limit:]:
        try:
            old_manifest.unlink()
        except OSError:
            pass


def load_undo_manifest(
    root_dir: Path, manifest_id: str | None = None,
) -> UndoManifest | None:
    """Load an undo manifest from disk.

    If manifest_id is provided, loads that specific manifest.
    Otherwise, loads the most recent manifest.
    """
    undo_dir = root_dir / UNDO_HISTORY_DIR

    if manifest_id:
        # Load specific manifest
        manifest_path = undo_dir / f"{manifest_id}.yaml"
        if not manifest_path.exists():
            # Try with .yaml extension if not provided
            manifest_path = undo_dir / f"{manifest_id}"
            if not manifest_path.exists():
                return None
    elif undo_dir.exists():
        # Load most recent manifest from history directory
        manifests = sorted(undo_dir.glob('*.yaml'), reverse=True)
        if manifests:
            manifest_path = manifests[0]
        else:
            # Fall back to legacy single file
            manifest_path = root_dir / UNDO_MANIFEST_FILE
    else:
        # Fall back to legacy single file
        manifest_path = root_dir / UNDO_MANIFEST_FILE

    if not manifest_path.exists():
        return None

    try:
        with open(manifest_path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        manifest = UndoManifest.from_dict(data)
        # Set manifest_id from filename if not present
        if not manifest.manifest_id and manifest_path.parent == undo_dir:
            manifest.manifest_id = manifest_path.stem
        return manifest
    except (yaml.YAMLError, KeyError):
        return None


def list_undo_manifests(root_dir: Path) -> list[tuple[str, str, int, bool]]:
    """List all available undo manifests.

    Returns list of (manifest_id, created_at, operation_count, is_dry_run) tuples.  # noqa: E501
    """
    undo_dir = root_dir / UNDO_HISTORY_DIR
    manifests = []

    if not undo_dir.exists():
        # Check legacy file
        legacy_path = root_dir / UNDO_MANIFEST_FILE
        if legacy_path.exists():
            try:
                with open(legacy_path, encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                manifests.append((
                    'legacy',
                    data.get('created_at', 'unknown'),
                    len(data.get('operations', [])),
                    data.get('dry_run', False),
                ))
            except (yaml.YAMLError, KeyError):
                pass
        return manifests

    for manifest_path in sorted(undo_dir.glob('*.yaml'), reverse=True):
        try:
            with open(manifest_path, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            manifests.append((
                manifest_path.stem,
                data.get('created_at', 'unknown'),
                len(data.get('operations', [])),
                data.get('dry_run', False),
            ))
        except (yaml.YAMLError, KeyError):
            continue

    return manifests


def delete_undo_manifest(root_dir: Path, manifest_id: str | None = None) -> None:  # noqa: E501
    """Delete an undo manifest file.

    If manifest_id is provided, deletes that specific manifest.
    Otherwise, deletes the most recent manifest and legacy file.
    """
    undo_dir = root_dir / UNDO_HISTORY_DIR

    if manifest_id:
        # Delete specific manifest
        manifest_path = undo_dir / f"{manifest_id}.json"
        if manifest_path.exists():
            manifest_path.unlink()
    else:
        # Delete most recent manifest
        if undo_dir.exists():
            manifests = sorted(undo_dir.glob('*.json'), reverse=True)
            if manifests:
                manifests[0].unlink()

        # Also delete legacy file
        legacy_path = root_dir / UNDO_MANIFEST_FILE
        if legacy_path.exists():
            legacy_path.unlink()


def move_file(
    source: Path,
    target_dir: Path,
    config: TidyConfig,
    logger: Logger,
    content_hashes: dict[str, Path] | None = None,
    relative_path: str | None = None,
) -> FileOperation:
    """Move a single file with optional structure preservation."""
    filename = source.name
    file_hash: str | None = None

    # Calculate destination path considering structure preservation
    if config.preserve_structure and relative_path:
        # Get the directory part of the relative path (relative to root_dir)
        rel_path = Path(relative_path)
        rel_dir = rel_path.parent

        # Remove source_dir prefix if present to get path relative to source
        source_prefix = Path(config.source_dir)
        if source_prefix != Path('.') and rel_dir.parts:
            try:
                rel_dir = rel_dir.relative_to(source_prefix)
            except ValueError:
                # If source_prefix is not a prefix, keep as is
                pass

        if rel_dir != Path('.') and str(rel_dir) != '.':
            # Apply flatten_depth if specified
            if config.flatten_depth is not None and len(rel_dir.parts) > config.flatten_depth:  # noqa: E501
                rel_dir = Path(*rel_dir.parts[:config.flatten_depth])
            destination_dir = target_dir / rel_dir
        else:
            destination_dir = target_dir
    else:
        destination_dir = target_dir

    destination = destination_dir / filename

    # Content-based duplicate detection
    if config.dedup_by_content:
        try:
            file_hash = compute_file_hash(source)

            # Check in-memory cache first (files being moved in this run)
            if content_hashes and file_hash in content_hashes:
                return FileOperation(
                    source=source,
                    status=OperationStatus.DUPLICATE,
                    reason=f"content duplicate of {content_hashes[file_hash].name}",  # noqa: E501
                    content_hash=file_hash,
                )

            # Check target directory for existing duplicates
            existing = find_content_duplicate(source, destination_dir, file_hash)  # noqa: E501
            if existing:
                return FileOperation(
                    source=source,
                    status=OperationStatus.DUPLICATE,
                    reason=f"content duplicate of {existing.name} in target",
                    content_hash=file_hash,
                )
        except OSError as e:
            logger.verbose(f"Could not compute hash for {filename}: {e}")

    # Check if destination exists (by filename) - apply collision handling
    if destination.exists():
        collision_result = _handle_collision(
            source, destination, config, logger, file_hash,
        )
        if collision_result is not None:
            if isinstance(collision_result, FileOperation):
                return collision_result
            else:
                destination = collision_result

    # Perform the move (or simulate in dry-run mode)
    if not config.dry_run:
        try:
            # Ensure target directory exists
            destination_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except OSError as e:
            return FileOperation(
                source=source,
                status=OperationStatus.FAILED,
                reason=str(e),
                content_hash=file_hash,
            )

    # Track hash for in-run duplicate detection
    if content_hashes is not None and file_hash:
        content_hashes[file_hash] = destination

    return FileOperation(
        source=source,
        destination=destination,
        status=OperationStatus.MOVED,
        content_hash=file_hash,
    )


def _handle_collision(
    source: Path,
    destination: Path,
    config: TidyConfig,
    logger: Logger,
    file_hash: str | None = None,
) -> FileOperation | Path | None:
    """Handle file collision based on configuration.

    Returns:
        - FileOperation if the file should be skipped
        - Path if the destination should be changed (renamed)
        - None if the destination should be overwritten
    """
    collision = config.collision
    target_dir = destination.parent

    # Backward compatibility: if duplicate_strategy is set and collision.keep is default,  # noqa: E501
    # use the legacy strategy
    use_legacy = (
        collision.keep == CollisionKeep.BOTH and
        config.duplicate_strategy != DuplicateStrategy.RENAME
    )

    if use_legacy:
        if config.duplicate_strategy == DuplicateStrategy.SKIP:
            return FileOperation(
                source=source,
                status=OperationStatus.DUPLICATE,
                reason='file already exists in target',
                content_hash=file_hash,
            )
        elif config.duplicate_strategy == DuplicateStrategy.OVERWRITE:
            logger.verbose(f"Overwriting existing file: {destination.name}")
            return None  # Proceed with overwrite

    # Handle based on collision.keep mode
    if collision.keep == CollisionKeep.TARGET:
        # Keep target, skip source (same as old SKIP)
        return FileOperation(
            source=source,
            status=OperationStatus.DUPLICATE,
            reason='file already exists in target (keeping target)',
            content_hash=file_hash,
        )

    elif collision.keep == CollisionKeep.SOURCE:
        # Overwrite target with source
        logger.verbose(f"Overwriting existing file: {destination.name}")
        return None  # Proceed with overwrite

    elif collision.keep == CollisionKeep.NEWEST:
        # Compare modification times
        try:
            source_mtime = source.stat().st_mtime
            dest_mtime = destination.stat().st_mtime
            if dest_mtime >= source_mtime:
                return FileOperation(
                    source=source,
                    status=OperationStatus.DUPLICATE,
                    reason='target is newer or same age',
                    content_hash=file_hash,
                )
            else:
                logger.verbose(f"Overwriting older file: {destination.name}")
                return None  # Overwrite with newer source
        except OSError:
            # On error, fall back to rename
            pass

    elif collision.keep == CollisionKeep.LARGEST:
        # Compare file sizes
        try:
            source_size = source.stat().st_size
            dest_size = destination.stat().st_size
            if dest_size >= source_size:
                return FileOperation(
                    source=source,
                    status=OperationStatus.DUPLICATE,
                    reason='target is larger or same size',
                    content_hash=file_hash,
                )
            else:
                logger.verbose(f"Overwriting smaller file: {destination.name}")
                return None  # Overwrite with larger source
        except OSError:
            # On error, fall back to rename
            pass

    # Default: CollisionKeep.BOTH - rename the incoming file
    # Also fallback for errors in NEWEST/LARGEST
    new_name = generate_unique_name(
        destination.name,
        pattern=collision.rename_pattern,
        file_hash=file_hash,
    )
    new_destination = target_dir / new_name
    logger.verbose(f"Renamed to avoid conflict: {new_name}")
    return new_destination


def undo_tidy(config: TidyConfig, manifest_id: str | None = None) -> TidyResult:  # noqa: E501
    """Undo a tidy operation.

    Args:
        config: Tidy configuration
        manifest_id: Optional specific manifest ID to undo. If None, undoes most recent.  # noqa: E501
    """
    logger = Logger(config.verbosity, dry_run=config.dry_run)
    result = TidyResult(dry_run=config.dry_run)

    manifest = load_undo_manifest(config.root_dir, manifest_id)
    if not manifest:
        if manifest_id:
            logger.error(f"Undo manifest '{manifest_id}' not found.")
        else:
            logger.error('No undo manifest found. Nothing to undo.')
        return result

    if manifest.dry_run:
        logger.warn('Last operation was a dry run. Nothing to undo.')
        delete_undo_manifest(config.root_dir, manifest.manifest_id)
        return result

    logger.header(f"Undoing tidy operation from {manifest.created_at}")
    if manifest.manifest_id:
        logger.info(f"Manifest ID: {manifest.manifest_id}")
    logger.info(f"Restoring {len(manifest.operations)} file(s)")

    for op in reversed(manifest.operations):
        moved_path = Path(op.moved_to_path)
        original_path = Path(op.original_path)
        result.total_processed += 1

        if not moved_path.exists():
            logger.warn(f"File no longer exists: {moved_path}")
            result.skipped.append(
                FileOperation(
                    source=moved_path,
                    status=OperationStatus.SKIPPED,
                    reason='file no longer exists',
                ),
            )
            continue

        if original_path.exists():
            logger.warn(f"Original location occupied: {original_path}")
            result.skipped.append(
                FileOperation(
                    source=moved_path,
                    destination=original_path,
                    status=OperationStatus.SKIPPED,
                    reason='original location occupied',
                ),
            )
            continue

        if not config.dry_run:
            try:
                # Ensure parent directory exists
                original_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved_path), str(original_path))
            except OSError as e:
                logger.error(f"Failed to restore {moved_path.name}: {e}")
                result.failed.append(
                    FileOperation(
                        source=moved_path,
                        destination=original_path,
                        status=OperationStatus.FAILED,
                        reason=str(e),
                    ),
                )
                continue

        logger.success(f"Restored: {moved_path.name} → {original_path}")
        result.moved.append(
            FileOperation(
                source=moved_path,
                destination=original_path,
                status=OperationStatus.MOVED,
            ),
        )

    # Delete manifest after successful undo
    if not config.dry_run and not result.failed:
        delete_undo_manifest(config.root_dir, manifest.manifest_id)
        logger.info('Undo manifest cleared')

    # Summary
    summary_parts = []
    if result.moved:
        summary_parts.append(f"{len(result.moved)} restored")
    if result.skipped:
        summary_parts.append(f"{len(result.skipped)} skipped")
    if result.failed:
        summary_parts.append(f"{len(result.failed)} failed")

    if summary_parts:
        logger.info(f"\n{Colors.BOLD}Summary:{Colors.RESET} {', '.join(summary_parts)}")  # noqa: E501

    return result


def list_undo_history(config: TidyConfig) -> None:
    """List available undo operations."""
    logger = Logger(config.verbosity)
    manifests = list_undo_manifests(config.root_dir)

    if not manifests:
        logger.info('No undo history available.')
        return

    logger.header('Undo History')
    for manifest_id, created_at, op_count, is_dry_run in manifests:
        status = ' (dry-run)' if is_dry_run else ''
        logger.info(
            f'  {manifest_id}  {created_at}  {op_count} files{status}',
        )


def tidy(config: TidyConfig) -> TidyResult:
    """Run the tidy operation."""
    logger = Logger(config.verbosity, config.dry_run)
    result = TidyResult(dry_run=config.dry_run)

    source_dir = config.root_dir / config.source_dir

    mode_info = []
    if config.recursive:
        depth_info = f"depth={config.max_depth}" if config.max_depth else 'unlimited'  # noqa: E501
        mode_info.append(f"recursive ({depth_info})")
    if config.dedup_by_content:
        mode_info.append('content-based dedup')
    if config.rules:
        mode_info.append(f"{len(config.rules)} routing rules")

    header_text = f"Tidying files from {config.source_dir}"
    if mode_info:
        header_text += f" [{', '.join(mode_info)}]"
    logger.header(header_text)

    # Ensure source directory exists
    if not source_dir.exists():
        logger.error(f"Source directory does not exist: {source_dir}")
        return result

    # Collect all files (with optional recursion)
    files = collect_files(source_dir, config)
    result.directories_scanned = 1  # At least the source dir

    if not files:
        logger.info('No files found in source directory')
        return result

    logger.verbose(f"Found {len(files)} files to process")

    # Track content hashes for in-run duplicate detection
    content_hashes: dict[str, Path] = {} if config.dedup_by_content else {}

    # Initialize undo manifest
    undo_manifest = UndoManifest(
        created_at=datetime.now(timezone.utc).isoformat(),
        dry_run=config.dry_run,
    )

    # Group files by target directory based on rules
    files_by_target: dict[str, list[tuple[Path, str]]] = {}
    for file_path, relative_path in files:
        target = get_target_for_file(file_path, relative_path, config)
        if target not in files_by_target:
            files_by_target[target] = []
        files_by_target[target].append((file_path, relative_path))

    # Process each file
    for target_dir_name, target_files in files_by_target.items():
        target_dir = config.root_dir / target_dir_name

        for file_path, relative_path in target_files:
            result.total_processed += 1
            filename = file_path.name

            # Check exclusions by filename/extension/pattern
            exclude, reason = should_exclude(filename, config)
            if exclude:
                logger.skip(f"Skipping: {relative_path} ({reason})")
                result.skipped.append(
                    FileOperation(
                        source=file_path,
                        status=OperationStatus.SKIPPED,
                        reason=reason,
                    ),
                )
                continue

            # Check exclusions by metadata (size, date, hidden, symlink)
            exclude, reason = should_exclude_by_metadata(file_path, config)
            if exclude:
                logger.skip(f"Skipping: {relative_path} ({reason})")
                result.skipped.append(
                    FileOperation(
                        source=file_path,
                        status=OperationStatus.SKIPPED,
                        reason=reason,
                    ),
                )
                continue

            # Move the file
            operation = move_file(
                file_path, target_dir, config, logger, content_hashes, relative_path,  # noqa: E501
            )

            if operation.status == OperationStatus.MOVED:
                dest_name = operation.destination.name if operation.destination else filename  # noqa: E501
                # Show relative destination path if structure is preserved
                if config.preserve_structure and operation.destination:
                    try:
                        dest_rel = operation.destination.relative_to(config.root_dir)  # noqa: E501
                        dest_display = str(dest_rel)
                    except ValueError:
                        dest_display = f"{target_dir_name.rstrip('/')}/{dest_name}"
                else:
                    dest_display = f"{target_dir_name.rstrip('/')}/{dest_name}"
                display_path = relative_path if relative_path != filename else filename  # noqa: E501
                logger.success(f"Moved: {display_path} → {dest_display}")
                result.moved.append(operation)

                # Record for undo
                if operation.destination:
                    undo_manifest.operations.append(
                        UndoOperation(
                            original_path=str(file_path),
                            moved_to_path=str(operation.destination),
                            timestamp=datetime.now(timezone.utc).isoformat(),
                        ),
                    )

            elif operation.status == OperationStatus.DUPLICATE:
                logger.warn(f"Duplicate: {filename} - {operation.reason}")
                result.skipped.append(operation)
            elif operation.status == OperationStatus.FAILED:
                logger.error(f"Failed: {filename} - {operation.reason}")
                result.failed.append(operation)

    # Save undo manifest (only if files were actually moved)
    if undo_manifest.operations:
        save_undo_manifest(undo_manifest, config.root_dir, config.undo_history_limit, config.create_legacy_undo_file)  # noqa: E501
        if not config.dry_run:
            logger.verbose(f"Undo manifest saved to {UNDO_HISTORY_DIR}/")

    # Summary
    if not result.moved and not result.failed:
        logger.info('\nNo files to move')
    else:
        summary_parts = []
        if result.moved:
            summary_parts.append(f"{len(result.moved)} moved")
        if result.skipped:
            summary_parts.append(f"{len(result.skipped)} skipped")
        if result.failed:
            summary_parts.append(f"{len(result.failed)} failed")

        logger.info(f"\n{Colors.BOLD}Summary:{Colors.RESET} {', '.join(summary_parts)}")  # noqa: E501

    return result


# =============================================================================
# Analysis and Interactive Features
# =============================================================================

@dataclass
class AnalysisResult:
    """Result of repository structure analysis."""

    project_type: str
    misplaced_files: list[tuple[Path, str, str]]  # (file, issue, suggestion)
    archive_files: list[Path]
    orphan_files: list[Path]
    suggested_rules: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            'project_type': self.project_type,
            'misplaced_files': [
                {'file': str(f), 'issue': i, 'suggestion': s}
                for f, i, s in self.misplaced_files
            ],
            'archive_files': [str(f) for f in self.archive_files],
            'orphan_files': [str(f) for f in self.orphan_files],
            'suggested_rules': self.suggested_rules,
        }


def find_orphan_files(
    files: list[Path],
    root_dir: Path,
    extensions_to_check: list[str] | None = None,
) -> list[Path]:
    """Find files that are not referenced anywhere in the codebase.

    This checks for files that might be unused/forgotten.
    """
    orphans: list[Path] = []

    # Extensions that are typically referenced in code
    if extensions_to_check is None:
        extensions_to_check = ['.md', '.txt', '.json', '.yaml', '.yml', '.png', '.jpg', '.svg']  # noqa: E501

    # Code file extensions to search in
    code_extensions = ['.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.rb', '.php', '.cs', '.md']  # noqa: E501

    for file in files:
        if file.suffix.lower() not in extensions_to_check:
            continue

        filename = file.name
        stem = file.stem

        # Search for references to this file
        found = False
        try:
            for code_file in root_dir.rglob('*'):
                if not code_file.is_file():
                    continue
                if code_file.suffix.lower() not in code_extensions:
                    continue
                if code_file == file:
                    continue

                # Skip large files
                try:
                    if code_file.stat().st_size > 1024 * 1024:  # 1MB limit
                        continue
                except OSError:
                    continue

                try:
                    content = code_file.read_text(encoding='utf-8', errors='ignore')
                    if filename in content or stem in content:
                        found = True
                        break
                except (OSError, UnicodeDecodeError):
                    continue

            if not found:
                orphans.append(file)
        except Exception:
            pass  # Skip files that can't be checked

    return orphans


def analyze_repository(
    root_dir: Path,
    config: TidyConfig,
    logger: Logger,
) -> AnalysisResult:
    """Analyze repository structure and identify issues.

    Returns analysis of:
    - Detected project type
    - Files that might be misplaced
    - Archive/backup files
    - Orphaned files (if enabled)
    - Suggested rules
    """
    # Detect project type with confidence
    detection = detect_project_type_with_confidence(root_dir)
    project_type = detection.project_type

    confidence_color = {
        DetectionConfidence.HIGH: Colors.GREEN,
        DetectionConfidence.MEDIUM: Colors.CYAN,
        DetectionConfidence.LOW: Colors.YELLOW,
        DetectionConfidence.NONE: Colors.GRAY,
    }.get(detection.confidence, Colors.RESET)

    logger.info(
        f"Detected project type: {Colors.CYAN}{project_type}{Colors.RESET} "
        f"(confidence: {confidence_color}{detection.confidence.value}{Colors.RESET})"
    )

    if detection.markers_found:
        logger.verbose(f"Markers found: {', '.join(detection.markers_found)}")

    if detection.confidence == DetectionConfidence.LOW:
        logger.warn(
            "Low confidence detection. Consider using --preset to specify project type."
        )

    # Collect files to analyze
    source_path = root_dir / config.source_dir
    file_tuples = collect_files(source_path, config)

    misplaced: list[tuple[Path, str, str]] = []
    archives: list[Path] = []
    orphans: list[Path] = []

    logger.info(f"\nAnalyzing {len(file_tuples)} files...")

    for file, relative_path in file_tuples:
        filename = file.name

        # Check for archive/backup files
        if is_archive_file(filename):
            archives.append(file)
            misplaced.append((
                file,
                'archive_file',
                "Move to archive/ directory or delete",
            ))
            continue

        # Check if file should be in root
        if is_root_file(filename, project_type):
            # File is expected in root - check if it's NOT in root
            try:
                if len(file.relative_to(root_dir).parts) > 1:
                    misplaced.append((
                        file,
                        'root_file_not_in_root',
                        "This file typically belongs in the project root",
                    ))
            except ValueError:
                pass  # File is not relative to root_dir
            continue

        # Check for test files not in test directory
        if is_test_file(file, project_type):
            conventions = LANGUAGE_CONVENTIONS.get(project_type, LANGUAGE_CONVENTIONS['generic'])  # noqa: E501
            test_dirs = conventions.get('tests', ['tests/', 'test/'])
            in_test_dir = any(
                d.rstrip('/') in str(relative_path).lower()
                for d in test_dirs
            )
            if not in_test_dir:
                misplaced.append((
                    file,
                    'test_not_in_test_dir',
                    f"Move to {test_dirs[0]} directory",
                ))
            continue

        # Check documentation files
        if file.suffix.lower() in ['.md', '.rst', '.txt', '.adoc']:
            # Check if in source directory (usually not ideal)
            conventions = LANGUAGE_CONVENTIONS.get(project_type, LANGUAGE_CONVENTIONS['generic'])  # noqa: E501
            source_dirs = conventions.get('source', ['src/', 'lib/'])
            in_source = any(
                d.rstrip('/') in str(relative_path).lower()
                for d in source_dirs
            )
            if in_source and file.parent != root_dir:
                misplaced.append((
                    file,
                    'docs_in_source',
                    "Consider moving to docs/ directory",
                ))

    # Find orphan files if enabled
    if config.detect_orphans:
        logger.info("Scanning for orphaned files...")
        # Extract just the paths from the file_tuples
        file_paths = [f for f, _ in file_tuples]
        orphans = find_orphan_files(file_paths, root_dir)
        for orphan in orphans:
            if orphan not in [m[0] for m in misplaced]:
                misplaced.append((
                    orphan,
                    'orphan_file',
                    "File not referenced in codebase - consider archiving or deleting",  # noqa: E501
                ))

    # Generate suggested rules based on analysis
    suggested_rules = generate_suggested_rules(root_dir, project_type, misplaced)

    return AnalysisResult(
        project_type=project_type,
        misplaced_files=misplaced,
        archive_files=archives,
        orphan_files=orphans,
        suggested_rules=suggested_rules,
    )


def generate_suggested_rules(
    root_dir: Path,
    project_type: str,
    misplaced: list[tuple[Path, str, str]],
) -> list[dict[str, Any]]:
    """Generate suggested rules based on repository analysis."""
    rules: list[dict[str, Any]] = []

    # Count issues by type
    issue_counts: dict[str, int] = {}
    for _, issue, _ in misplaced:
        issue_counts[issue] = issue_counts.get(issue, 0) + 1

    # Suggest rules based on common issues
    if issue_counts.get('archive_file', 0) > 0:
        rules.append({
            'comment': 'Move backup/archive files to archive directory',
            'pattern': '*backup*',
            'target': 'archive/',
        })
        rules.append({
            'comment': 'Move old files to archive directory',
            'pattern': '*.bak',
            'target': 'archive/',
        })

    if issue_counts.get('docs_in_source', 0) > 0:
        rules.append({
            'comment': 'Move documentation to docs directory',
            'extensions': ['.md', '.rst', '.txt'],
            'target': 'docs/',
        })

    if issue_counts.get('test_not_in_test_dir', 0) > 0:
        rules.append({
            'comment': 'Move test files to tests directory',
            'pattern': '*_test.py',
            'target': 'tests/',
        })
        rules.append({
            'comment': 'Move test files to tests directory',
            'pattern': 'test_*.py',
            'target': 'tests/',
        })

    # Add preset-based rules
    preset = get_preset(project_type)
    if preset:
        rules.append({
            'comment': f'Preset rules for {project_type} project',
            'preset': project_type,
        })

    return rules


def print_analysis_report(result: AnalysisResult, logger: Logger) -> None:
    """Print a human-readable analysis report."""
    logger.info(f"\n{Colors.BOLD}=== Repository Analysis Report ==={Colors.RESET}")
    logger.info(f"Project type: {Colors.CYAN}{result.project_type}{Colors.RESET}")

    if result.misplaced_files:
        logger.info(f"\n{Colors.YELLOW}Potential Issues Found:{Colors.RESET}")
        for file, issue, suggestion in result.misplaced_files:
            issue_display = issue.replace('_', ' ').title()
            logger.info(f"  • {file}")
            logger.info(f"    Issue: {issue_display}")
            logger.info(f"    Suggestion: {suggestion}")
    else:
        logger.success("\nNo issues found! Repository structure looks good.")

    if result.archive_files:
        logger.info(f"\n{Colors.YELLOW}Archive/Backup Files ({len(result.archive_files)}):{Colors.RESET}")  # noqa: E501
        for f in result.archive_files[:10]:  # Show first 10
            logger.info(f"  • {f}")
        if len(result.archive_files) > 10:
            logger.info(f"  ... and {len(result.archive_files) - 10} more")

    if result.orphan_files:
        logger.info(f"\n{Colors.YELLOW}Potentially Orphaned Files ({len(result.orphan_files)}):{Colors.RESET}")  # noqa: E501
        for f in result.orphan_files[:10]:
            logger.info(f"  • {f}")
        if len(result.orphan_files) > 10:
            logger.info(f"  ... and {len(result.orphan_files) - 10} more")

    if result.suggested_rules:
        logger.info(f"\n{Colors.CYAN}Suggested Configuration:{Colors.RESET}")
        suggested_config = {
            'project_type': result.project_type,
            'rules': [r for r in result.suggested_rules if 'preset' not in r],
        }
        logger.info(yaml.dump(suggested_config, default_flow_style=False, sort_keys=False))


def interactive_move(
    file: Path,
    suggested_target: Path,
    config: TidyConfig,
    logger: Logger,
) -> str:
    """Interactively prompt user for file move decision.

    Returns: 'move', 'skip', 'custom', or 'quit'
    """
    print(f"\n{Colors.BOLD}File:{Colors.RESET} {file}")
    print(f"{Colors.CYAN}Suggested target:{Colors.RESET} {suggested_target}")
    print()
    print("Options:")
    print("  [m] Move to suggested target")
    print("  [s] Skip this file")
    print("  [c] Enter custom target")
    print("  [a] Move all remaining (non-interactive)")
    print("  [q] Quit")
    print()

    while True:
        try:
            choice = input(f"{Colors.BOLD}Choice [m/s/c/a/q]:{Colors.RESET} ").strip().lower()  # noqa: E501
        except (EOFError, KeyboardInterrupt):
            return 'quit'

        if choice in ['m', 'move', '']:
            return 'move'
        elif choice in ['s', 'skip']:
            return 'skip'
        elif choice in ['c', 'custom']:
            return 'custom'
        elif choice in ['a', 'all']:
            return 'all'
        elif choice in ['q', 'quit']:
            return 'quit'
        else:
            print("Invalid choice. Please enter m, s, c, a, or q.")


def get_custom_target(config: TidyConfig) -> Path | None:
    """Prompt user for custom target directory."""
    try:
        target = input(f"{Colors.BOLD}Enter target directory:{Colors.RESET} ").strip()
        if not target:
            return None
        return config.root_dir / target
    except (EOFError, KeyboardInterrupt):
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog='tidy',
        description='Automated file organization for repositories',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tidy                                  # Run with defaults
  tidy --dry-run                        # Preview changes
  tidy --source drafts                  # Custom source directory
  tidy --extensions .md,.txt            # Multiple extensions
  tidy --recursive                      # Scan subdirectories
  tidy --recursive --max-depth 3        # Limit recursion depth
  tidy --preserve-structure             # Keep directory structure
  tidy --exclude-hidden                 # Skip hidden files
  tidy --min-size 1KB --max-size 10MB   # Filter by size
  tidy --modified-after 2025-01-01      # Only recent files
  tidy --collision-keep newest          # Keep newer file on collision
  tidy --dedup-by-content               # Detect duplicates by content
  tidy --undo                           # Undo last operation
  tidy --undo-list                      # List undo history
  tidy --undo-id 20260121-103045-123456 # Undo specific operation

Configuration Files:
  .tidyrc.yaml, .tidyrc.yml, tidy.config.yaml

Rule-based Routing (in config file):
  "rules": [
    {"pattern": "*.test.md", "target": "tests/"},
    {"extensions": [".png", ".jpg"], "target": "assets/"},
    {"glob": "docs/**/*.md", "target": "documentation/"}
  ]
""",
    )

    parser.add_argument(
        '--version',
        action='version',
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        '--config',
        type=Path,
        help='Path to configuration file (default: .tidyrc.yaml)',
    )
    parser.add_argument(
        '--source',
        dest='source_dir',
        help='Source directory (default: .)',
    )
    parser.add_argument(
        '--target',
        dest='target_dir',
        help='Target directory (default: 00-inbox)',
    )
    parser.add_argument(
        '--extensions',
        help='Comma-separated file extensions (default: .md)',
    )
    parser.add_argument(
        '--exclude-dirs',
        dest='exclude_dirs',
        help='Comma-separated directories to exclude from recursive scan',
    )
    parser.add_argument(
        '--recursive',
        '-r',
        action='store_true',
        help='Recursively scan source directory',
    )
    parser.add_argument(
        '--max-depth',
        dest='max_depth',
        type=int,
        help='Maximum recursion depth (default: unlimited)',
    )
    parser.add_argument(
        '--preserve-structure',
        dest='preserve_structure',
        action='store_true',
        help='Preserve directory structure when moving files',
    )
    parser.add_argument(
        '--flatten-depth',
        dest='flatten_depth',
        type=int,
        help='Preserve only N levels of directory structure',
    )
    parser.add_argument(
        '--exclude-hidden',
        dest='exclude_hidden',
        action='store_true',
        help='Exclude hidden files (starting with .)',
    )
    parser.add_argument(
        '--exclude-symlinks',
        dest='exclude_symlinks',
        action='store_true',
        help='Exclude symbolic links',
    )
    parser.add_argument(
        '--min-size',
        dest='min_size',
        help='Minimum file size (e.g., 1KB, 5MB)',
    )
    parser.add_argument(
        '--max-size',
        dest='max_size',
        help='Maximum file size (e.g., 100MB)',
    )
    parser.add_argument(
        '--modified-after',
        dest='modified_after',
        help='Only files modified after date (YYYY-MM-DD)',
    )
    parser.add_argument(
        '--modified-before',
        dest='modified_before',
        help='Only files modified before date (YYYY-MM-DD)',
    )
    parser.add_argument(
        '--collision-keep',
        dest='collision_keep',
        choices=['both', 'newest', 'largest', 'source', 'target'],
        help='How to handle collisions (default: both)',
    )
    parser.add_argument(
        '--rename-pattern',
        dest='rename_pattern',
        help='Pattern for renamed files (default: {name}-{timestamp})',
    )
    parser.add_argument(
        '--delete-mode',
        dest='delete_mode',
        choices=['trash', 'permanent'],
        help='How to handle deletions: trash (recoverable) or permanent',
    )
    parser.add_argument(
        '--trash',
        dest='trash_files',
        nargs='*',
        metavar='FILE',
        help='Move specified files to system trash',
    )
    parser.add_argument(
        '--dedup-by-content',
        dest='dedup_by_content',
        action='store_true',
        help='Detect duplicates by file content hash',
    )
    parser.add_argument(
        '--undo',
        action='store_true',
        help='Undo the most recent tidy operation',
    )
    parser.add_argument(
        '--undo-list',
        dest='undo_list',
        action='store_true',
        help='List available undo operations',
    )
    parser.add_argument(
        '--undo-id',
        dest='undo_id',
        help='Undo a specific operation by ID',
    )
    # Smart architecture features
    parser.add_argument(
        '--preset',
        choices=['python', 'node', 'go', 'rust', 'java', 'ruby', 'php', 'dotnet',
                 'swift', 'kotlin', 'scala', 'elixir', 'haskell', 'c_cpp',
                 'terraform', 'docker', 'monorepo', 'generic'],
        help='Use preset rules for project type (auto-detects if not specified)',
    )
    parser.add_argument(
        '--show-detection',
        dest='show_detection',
        action='store_true',
        help='Show project type detection details and exit',
    )
    parser.add_argument(
        '--analyze',
        action='store_true',
        help='Analyze repo structure and suggest rules (no changes made)',
    )
    parser.add_argument(
        '--detect-archives',
        dest='detect_archives',
        action='store_true',
        help='Flag backup/archive files (e.g., *.bak, *backup*, *old*)',
    )
    parser.add_argument(
        '--detect-orphans',
        dest='detect_orphans',
        action='store_true',
        help='Flag files not referenced elsewhere in the codebase',
    )
    parser.add_argument(
        '--interactive', '-i',
        action='store_true',
        help='Interactively confirm each file move',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without moving files',
    )
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Show detailed output',
    )
    parser.add_argument(
        '--quiet',
        '-q',
        action='store_true',
        help='Suppress all output except errors',
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)

    # Load configurations with precedence: CLI > ENV > pre-commit > File > Defaults  # noqa: E501
    try:
        file_config = load_config_file(args.config)
    except FileNotFoundError as e:
        print(f"{Colors.RED}Error:{Colors.RESET} {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"{Colors.RED}Error:{Colors.RESET} Invalid JSON in config file: {e}", file=sys.stderr)  # noqa: E501
        return 1

    env_config = load_env_config()
    pre_commit_config = load_pre_commit_config(Path.cwd())

    # Merge configs (file < pre-commit < env)
    merged_config: ConfigDict = {**file_config, **pre_commit_config, **env_config}  # noqa: E501

    # Create config object
    config = TidyConfig.from_dict(merged_config, Path.cwd())

    # Apply CLI overrides
    if args.source_dir:
        config.source_dir = args.source_dir
    if args.target_dir:
        config.target_dir = args.target_dir
    if args.extensions:
        config.extensions = [e.strip() for e in args.extensions.split(',')]
    if args.exclude_dirs:
        config.exclude_dirs = [d.strip() for d in args.exclude_dirs.split(',')]
    if args.recursive or os.environ.get('TIDY_RECURSIVE') == 'true':
        config.recursive = True
    if args.max_depth is not None:
        config.max_depth = args.max_depth
    if args.preserve_structure:
        config.preserve_structure = True
    if args.flatten_depth is not None:
        config.flatten_depth = args.flatten_depth
    if args.dedup_by_content or os.environ.get('TIDY_DEDUP_BY_CONTENT') == 'true':  # noqa: E501
        config.dedup_by_content = True
    if args.dry_run or os.environ.get('TIDY_DRY_RUN') == 'true':
        config.dry_run = True
    if args.verbose or os.environ.get('TIDY_VERBOSE') == 'true':
        config.verbosity = 2
    if args.quiet:
        config.verbosity = 0

    # Apply filter CLI overrides
    if args.exclude_hidden:
        config.filters.exclude_hidden = True
    if args.exclude_symlinks:
        config.filters.exclude_symlinks = True
    if args.min_size:
        try:
            config.filters.min_size = parse_size(args.min_size)
        except ValueError as e:
            print(f"{Colors.RED}Error:{Colors.RESET} Invalid min-size: {e}", file=sys.stderr)  # noqa: E501
            return 2
    if args.max_size:
        try:
            config.filters.max_size = parse_size(args.max_size)
        except ValueError as e:
            print(f"{Colors.RED}Error:{Colors.RESET} Invalid max-size: {e}", file=sys.stderr)  # noqa: E501
            return 2
    if args.modified_after:
        try:
            config.filters.modified_after = parse_date(args.modified_after)
        except ValueError as e:
            print(f"{Colors.RED}Error:{Colors.RESET} Invalid modified-after: {e}", file=sys.stderr)  # noqa: E501
            return 2
    if args.modified_before:
        try:
            config.filters.modified_before = parse_date(args.modified_before)
        except ValueError as e:
            print(f"{Colors.RED}Error:{Colors.RESET} Invalid modified-before: {e}", file=sys.stderr)  # noqa: E501
            return 2

    # Apply collision CLI overrides
    if args.collision_keep:
        config.collision.keep = CollisionKeep(args.collision_keep)
    if args.rename_pattern:
        config.collision.rename_pattern = args.rename_pattern
    if args.delete_mode:
        config.delete_mode = DeleteMode(args.delete_mode)

    # Handle --trash command (move files to trash)
    if args.trash_files is not None:
        logger = Logger(verbosity=config.verbosity, dry_run=config.dry_run)
        files_to_trash = args.trash_files
        if not files_to_trash:
            # If no files specified, show help
            print(f"{Colors.YELLOW}Usage:{Colors.RESET} tidy --trash FILE [FILE ...]")
            print("Move files to system trash (recoverable)")
            return 0

        trashed = 0
        failed = 0
        for file_path in files_to_trash:
            path = Path(file_path)
            if not path.exists():
                logger.error(f"File not found: {file_path}")
                failed += 1
                continue
            if config.dry_run:
                logger.success(f"Would trash: {file_path}")
                trashed += 1
            else:
                success, error = trash_file(path, config.delete_mode, logger)
                if success:
                    status = "trashed" if config.delete_mode == DeleteMode.TRASH else "deleted"  # noqa: E501
                    logger.success(f"{status.capitalize()}: {file_path}")
                    trashed += 1
                else:
                    logger.error(f"Failed to trash {file_path}: {error}")
                    failed += 1

        print(f"\nSummary: {trashed} trashed, {failed} failed")
        return 1 if failed > 0 else 0

    # Handle --show-detection before other options
    if args.show_detection:
        detection = detect_project_type_with_confidence(config.root_dir)
        confidence_symbol = {
            DetectionConfidence.HIGH: f"{Colors.GREEN}✓ HIGH{Colors.RESET}",
            DetectionConfidence.MEDIUM: f"{Colors.CYAN}◐ MEDIUM{Colors.RESET}",
            DetectionConfidence.LOW: f"{Colors.YELLOW}⚠ LOW{Colors.RESET}",
            DetectionConfidence.NONE: f"{Colors.GRAY}✗ NONE{Colors.RESET}",
        }.get(detection.confidence, "UNKNOWN")

        print(f"\n{Colors.BOLD}Project Type Detection{Colors.RESET}")
        print(f"{'=' * 40}")
        print(f"Detected type:  {Colors.CYAN}{detection.project_type}{Colors.RESET}")
        print(f"Confidence:     {confidence_symbol}")
        print(f"Markers found:  {', '.join(detection.markers_found) or 'none'}")

        if detection.all_detected_types:
            print(f"\n{Colors.BOLD}All Detected Types:{Colors.RESET}")
            for ptype, conf, markers in detection.all_detected_types[:8]:
                conf_label = {
                    DetectionConfidence.HIGH: f"{Colors.GREEN}HIGH{Colors.RESET}",
                    DetectionConfidence.MEDIUM: f"{Colors.CYAN}MEDIUM{Colors.RESET}",
                    DetectionConfidence.LOW: f"{Colors.YELLOW}LOW{Colors.RESET}",
                }.get(conf, "?")
                print(f"  • {ptype:12} ({conf_label:20}) - {', '.join(markers[:3])}")

        print(f"\n{Colors.BOLD}Available Presets:{Colors.RESET}")
        presets_list = sorted(PRESETS.keys())
        for i in range(0, len(presets_list), 6):
            print(f"  {', '.join(presets_list[i:i+6])}")

        print("\nUse --preset <type> to override auto-detection.\n")
        return 0

    # Apply smart architecture CLI overrides
    if args.preset:
        # Load preset and merge with existing config
        preset_config = get_preset(args.preset)
        for key, value in preset_config.items():
            if key == 'filters' and isinstance(value, dict):
                for fk, fv in value.items():
                    setattr(config.filters, fk, fv)
            elif hasattr(config, key):
                setattr(config, key, value)
        config.preset = args.preset
        config.project_type = args.preset
    if args.detect_archives:
        config.detect_archives = True
    if args.detect_orphans:
        config.detect_orphans = True
    if args.interactive:
        config.interactive = True
    if args.analyze:
        config.analyze_only = True
        config.dry_run = True  # Analyze implies dry run

    # Create logger
    logger = Logger(
        verbosity=config.verbosity,
        dry_run=config.dry_run or config.analyze_only,
    )

    # Handle analyze mode
    if config.analyze_only:
        try:
            result = analyze_repository(config.root_dir, config, logger)
            print_analysis_report(result, logger)

            # Output JSON if verbose
            if config.verbosity >= 2:
                logger.info(f"\n{Colors.CYAN}JSON Output:{Colors.RESET}")
                print(json.dumps(result.to_dict(), indent=2))

            return 0 if not result.misplaced_files else 1
        except Exception as e:
            print(f"{Colors.RED}Error during analysis:{Colors.RESET} {e}", file=sys.stderr)  # noqa: E501
            return 1

    # Handle undo-list command
    if args.undo_list:
        list_undo_history(config)
        return 0

    # Handle undo commands
    if args.undo or args.undo_id:
        try:
            result = undo_tidy(config, args.undo_id)
        except Exception as e:
            print(f"{Colors.RED}Fatal error:{Colors.RESET} {e}", file=sys.stderr)  # noqa: E501
            return 1
        return 1 if result.failed else 0

    # Run tidy
    try:
        result = tidy(config)
    except Exception as e:
        print(f"{Colors.RED}Fatal error:{Colors.RESET} {e}", file=sys.stderr)
        return 1

    # Exit with appropriate code
    return 1 if result.failed else 0


if __name__ == '__main__':
    sys.exit(main())
