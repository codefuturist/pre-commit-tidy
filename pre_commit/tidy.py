"""Tidy - Automated file organization for repositories.

Moves files from source directories to target directories based on
configurable rules. Designed to be reusable across different repositories.

Usage:
    tidy [options]

Options:
    --config PATH           Path to configuration file (default: .tidyrc.json)
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
    --modified-after DATE
        Only files modified after date (YYYY-MM-DD)
    --modified-before DATE
        Only files modified before date (YYYY-MM-DD)
    --collision-keep MODE
        How to handle collisions: both|newest|largest|source|target
    --rename-pattern PAT
        Pattern for renamed files (default: {name}-{timestamp})
    --dry-run               Preview changes without moving files
    --verbose               Show detailed output
    --quiet                 Suppress all output except errors
    --undo                  Undo the most recent tidy operation
    --undo-list             List available undo operations
    --undo-id ID            Undo a specific operation by ID
    --dedup-by-content      Detect duplicates by file content hash
    --help                  Show this help message
    --version               Show version number

Configuration:
    Create a .tidyrc.json file in your project root:

    {
        "source_dir": ".",
        "target_dir": "00-inbox",
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
        "rules": [
            {"pattern": "*.test.md", "target": "tests/"},
            {"pattern": "*.draft.*", "target": "drafts/"},
            {"extensions": [".png", ".jpg"], "target": "assets/images/"}
        ]
    }

    Rule-based routing supports three formats:
    1. Pattern matching:  {"pattern": "*.test.md", "target": "tests/"}
    2. Extension-based:   {"extensions": [".png"], "target": "images/"}
    3. Glob-to-folder:    {"glob": "docs/**/*.md", "target": "documentation/"}

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

Environment Variables:
    TIDY_SOURCE_DIR     Source directory
    TIDY_TARGET_DIR     Target directory
    TIDY_EXTENSIONS     Comma-separated extensions
    TIDY_EXCLUDE_FILES  Comma-separated files to exclude
    TIDY_EXCLUDE_DIRS   Comma-separated directories to exclude
    TIDY_DRY_RUN        Set to 'true' for dry run
    TIDY_VERBOSE        Set to 'true' for verbose output
    TIDY_RECURSIVE      Set to 'true' for recursive scanning
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

# Version
__version__ = '1.1.0'

# Default configuration file names to search for
CONFIG_FILE_NAMES = ['.tidyrc.json', '.tidyrc', 'tidy.config.json']

# Undo history directory (replaces single file for persistent history)
UNDO_HISTORY_DIR = '.tidy-undo'

# Legacy single-file manifest (for backward compatibility)
UNDO_MANIFEST_FILE = '.tidy-undo.json'

# Default undo history limit
DEFAULT_UNDO_HISTORY_LIMIT = 10


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


class OperationStatus(Enum):
    """Status of a file operation."""

    MOVED = 'moved'
    SKIPPED = 'skipped'
    FAILED = 'failed'
    DUPLICATE = 'duplicate'


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
    """A rule for routing files to specific targets."""

    target: str
    pattern: str | None = None
    extensions: list[str] | None = None
    glob: str | None = None

    def matches(self, file_path: Path, relative_path: str) -> bool:
        """Check if this rule matches the given file."""
        filename = file_path.name

        # Pattern matching (e.g., "*.test.md")
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
    exclude_patterns: list[str] = field(default_factory=list)
    exclude_dirs: list[str] = field(default_factory=lambda: DEFAULT_EXCLUDE_DIRS.copy())  # noqa: E501
    duplicate_strategy: DuplicateStrategy = DuplicateStrategy.RENAME
    dedup_by_content: bool = False
    recursive: bool = False
    max_depth: int | None = None
    preserve_structure: bool = False
    flatten_depth: int | None = None
    filters: FilterConfig = field(default_factory=FilterConfig)
    collision: CollisionConfig = field(default_factory=CollisionConfig)
    undo_history_limit: int = DEFAULT_UNDO_HISTORY_LIMIT
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
        if 'undo_history_limit' in data:
            config.undo_history_limit = data['undo_history_limit']
        if 'rules' in data:
            config.rules = [
                RoutingRule(
                    target=rule['target'],
                    pattern=rule.get('pattern'),
                    extensions=rule.get('extensions'),
                    glob=rule.get('glob'),
                )
                for rule in data['rules']
            ]

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
    """Load configuration from file."""
    root_dir = Path.cwd()

    # If explicit config path provided, try to load it
    if config_path:
        full_path = root_dir / config_path
        if full_path.exists():
            with open(full_path, encoding='utf-8') as f:
                data: ConfigDict = json.load(f)
                return data
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # Try default config file names
    for filename in CONFIG_FILE_NAMES:
        full_path = root_dir / filename
        if full_path.exists():
            with open(full_path, encoding='utf-8') as f:
                data = json.load(f)
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
) -> None:
    """Save the undo manifest to persistent history directory."""
    undo_dir = root_dir / UNDO_HISTORY_DIR
    undo_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique manifest ID from timestamp
    if not manifest.manifest_id:
        manifest.manifest_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')  # noqa: E501

    # Save to timestamped file
    manifest_path = undo_dir / f"{manifest.manifest_id}.json"
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest.to_dict(), f, indent=2)

    # Also maintain legacy single file for backward compatibility
    legacy_path = root_dir / UNDO_MANIFEST_FILE
    with open(legacy_path, 'w', encoding='utf-8') as f:
        json.dump(manifest.to_dict(), f, indent=2)

    # Cleanup old manifests beyond history limit
    _cleanup_undo_history(undo_dir, history_limit)


def _cleanup_undo_history(undo_dir: Path, limit: int) -> None:
    """Remove old undo manifests beyond the history limit."""
    if not undo_dir.exists():
        return

    manifests = sorted(undo_dir.glob('*.json'), key=lambda p: p.name, reverse=True)  # noqa: E501
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
        manifest_path = undo_dir / f"{manifest_id}.json"
        if not manifest_path.exists():
            # Try with .json extension if not provided
            manifest_path = undo_dir / f"{manifest_id}"
            if not manifest_path.exists():
                return None
    elif undo_dir.exists():
        # Load most recent manifest from history directory
        manifests = sorted(undo_dir.glob('*.json'), reverse=True)
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
            data = json.load(f)
        manifest = UndoManifest.from_dict(data)
        # Set manifest_id from filename if not present
        if not manifest.manifest_id and manifest_path.parent == undo_dir:
            manifest.manifest_id = manifest_path.stem
        return manifest
    except (json.JSONDecodeError, KeyError):
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
                    data = json.load(f)
                manifests.append((
                    'legacy',
                    data.get('created_at', 'unknown'),
                    len(data.get('operations', [])),
                    data.get('dry_run', False),
                ))
            except (json.JSONDecodeError, KeyError):
                pass
        return manifests

    for manifest_path in sorted(undo_dir.glob('*.json'), reverse=True):
        try:
            with open(manifest_path, encoding='utf-8') as f:
                data = json.load(f)
            manifests.append((
                manifest_path.stem,
                data.get('created_at', 'unknown'),
                len(data.get('operations', [])),
                data.get('dry_run', False),
            ))
        except (json.JSONDecodeError, KeyError):
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
                        dest_display = f"{target_dir_name}/{dest_name}"
                else:
                    dest_display = f"{target_dir_name}/{dest_name}"
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
        save_undo_manifest(undo_manifest, config.root_dir, config.undo_history_limit)  # noqa: E501
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
  .tidyrc.json, .tidyrc, tidy.config.json

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
        help='Path to configuration file (default: .tidyrc.json)',
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
