"""Tidy - Automated file organization for repositories.

Moves files from source directories to target directories based on
configurable rules. Designed to be reusable across different repositories.

Usage:
    tidy [options]

Options:
    --config PATH       Path to configuration file (default: .tidyrc.json)
    --source DIR        Source directory (default: .)
    --target DIR        Target directory (default: 00-inbox)
    --extensions EXT    Comma-separated extensions (default: .md)
    --dry-run           Preview changes without moving files
    --verbose           Show detailed output
    --quiet             Suppress all output except errors
    --help              Show this help message
    --version           Show version number

Configuration:
    Create a .tidyrc.json file in your project root:

    {
        "source_dir": ".",
        "target_dir": "00-inbox",
        "extensions": [".md", ".txt"],
        "exclude_files": ["readme.md", "changelog.md"],
        "exclude_patterns": ["*.config.*"],
        "duplicate_strategy": "rename"
    }

Environment Variables:
    TIDY_SOURCE_DIR     Source directory
    TIDY_TARGET_DIR     Target directory
    TIDY_EXTENSIONS     Comma-separated extensions
    TIDY_EXCLUDE_FILES  Comma-separated files to exclude
    TIDY_DRY_RUN        Set to 'true' for dry run
    TIDY_VERBOSE        Set to 'true' for verbose output
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TypedDict

# Default configuration file names to search for
CONFIG_FILE_NAMES = [".tidyrc.json", ".tidyrc", "tidy.config.json"]


class DuplicateStrategy(Enum):
    """How to handle duplicate files."""

    RENAME = "rename"
    SKIP = "skip"
    OVERWRITE = "overwrite"


class OperationStatus(Enum):
    """Status of a file operation."""

    MOVED = "moved"
    SKIPPED = "skipped"
    FAILED = "failed"
    DUPLICATE = "duplicate"


@dataclass
class FileOperation:
    """Result of a single file operation."""

    source: Path
    destination: Path | None = None
    status: OperationStatus = OperationStatus.SKIPPED
    reason: str | None = None


@dataclass
class TidyResult:
    """Result of the tidy operation."""

    moved: list[FileOperation] = field(default_factory=list)
    skipped: list[FileOperation] = field(default_factory=list)
    failed: list[FileOperation] = field(default_factory=list)
    total_processed: int = 0
    dry_run: bool = False


class ConfigDict(TypedDict, total=False):
    """Configuration dictionary type."""

    source_dir: str
    target_dir: str
    extensions: list[str]
    exclude_files: list[str]
    exclude_patterns: list[str]
    duplicate_strategy: str


@dataclass
class TidyConfig:
    """Configuration for the tidy operation."""

    root_dir: Path = field(default_factory=Path.cwd)
    source_dir: str = "."
    target_dir: str = "00-inbox"
    extensions: list[str] = field(default_factory=lambda: [".md"])
    exclude_files: list[str] = field(
        default_factory=lambda: [
            "readme.md",
            "changelog.md",
            "license.md",
            "contributing.md",
        ]
    )
    exclude_patterns: list[str] = field(default_factory=list)
    duplicate_strategy: DuplicateStrategy = DuplicateStrategy.RENAME
    dry_run: bool = False
    verbosity: int = 1  # 0=quiet, 1=normal, 2=verbose

    @classmethod
    def from_dict(cls, data: ConfigDict, root_dir: Path | None = None) -> TidyConfig:
        """Create config from dictionary."""
        config = cls()
        if root_dir:
            config.root_dir = root_dir

        if "source_dir" in data:
            config.source_dir = data["source_dir"]
        if "target_dir" in data:
            config.target_dir = data["target_dir"]
        if "extensions" in data:
            config.extensions = data["extensions"]
        if "exclude_files" in data:
            config.exclude_files = data["exclude_files"]
        if "exclude_patterns" in data:
            config.exclude_patterns = data["exclude_patterns"]
        if "duplicate_strategy" in data:
            config.duplicate_strategy = DuplicateStrategy(data["duplicate_strategy"])

        return config


class Colors:
    """ANSI color codes for terminal output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    @classmethod
    def disable(cls) -> None:
        """Disable colors (for non-TTY output)."""
        cls.RESET = ""
        cls.BOLD = ""
        cls.DIM = ""
        cls.GREEN = ""
        cls.YELLOW = ""
        cls.RED = ""
        cls.CYAN = ""
        cls.GRAY = ""


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
            prefix = "[DRY-RUN] " if self.dry_run else ""
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
            with open(full_path, encoding="utf-8") as f:
                data: ConfigDict = json.load(f)
                return data
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # Try default config file names
    for filename in CONFIG_FILE_NAMES:
        full_path = root_dir / filename
        if full_path.exists():
            with open(full_path, encoding="utf-8") as f:
                data = json.load(f)
                return data

    return {}


def load_env_config() -> ConfigDict:
    """Load configuration from environment variables."""
    config: ConfigDict = {}

    if os.environ.get("TIDY_SOURCE_DIR"):
        config["source_dir"] = os.environ["TIDY_SOURCE_DIR"]
    if os.environ.get("TIDY_TARGET_DIR"):
        config["target_dir"] = os.environ["TIDY_TARGET_DIR"]
    if os.environ.get("TIDY_EXTENSIONS"):
        config["extensions"] = os.environ["TIDY_EXTENSIONS"].split(",")
    if os.environ.get("TIDY_EXCLUDE_FILES"):
        config["exclude_files"] = os.environ["TIDY_EXCLUDE_FILES"].split(",")

    return config


def should_exclude(filename: str, config: TidyConfig) -> tuple[bool, str | None]:
    """Check if a file should be excluded."""
    lower_filename = filename.lower()

    # Check exact filename matches
    if any(f.lower() == lower_filename for f in config.exclude_files):
        return True, "excluded by filename"

    # Check extension
    ext = Path(filename).suffix.lower()
    if ext not in [e.lower() for e in config.extensions]:
        return True, f"extension {ext} not in allowed list"

    # Check patterns (glob-like matching)
    for pattern in config.exclude_patterns:
        if fnmatch.fnmatch(filename.lower(), pattern.lower()):
            return True, f"matches pattern: {pattern}"

    return False, None


def generate_unique_name(filename: str) -> str:
    """Generate a unique filename for duplicates."""
    path = Path(filename)
    timestamp = int(time.time() * 1000)
    return f"{path.stem}-{timestamp}{path.suffix}"


def move_file(source: Path, target_dir: Path, config: TidyConfig, logger: Logger) -> FileOperation:
    """Move a single file."""
    filename = source.name
    destination = target_dir / filename

    # Check if destination exists
    if destination.exists():
        if config.duplicate_strategy == DuplicateStrategy.SKIP:
            return FileOperation(
                source=source,
                status=OperationStatus.DUPLICATE,
                reason="file already exists in target",
            )
        elif config.duplicate_strategy == DuplicateStrategy.RENAME:
            new_name = generate_unique_name(filename)
            destination = target_dir / new_name
            logger.verbose(f"Renamed to avoid conflict: {new_name}")
        elif config.duplicate_strategy == DuplicateStrategy.OVERWRITE:
            logger.verbose(f"Overwriting existing file: {filename}")

    # Perform the move (or simulate in dry-run mode)
    if not config.dry_run:
        try:
            shutil.move(str(source), str(destination))
        except OSError as e:
            return FileOperation(
                source=source,
                status=OperationStatus.FAILED,
                reason=str(e),
            )

    return FileOperation(
        source=source,
        destination=destination,
        status=OperationStatus.MOVED,
    )


def tidy(config: TidyConfig) -> TidyResult:
    """Run the tidy operation."""
    logger = Logger(config.verbosity, config.dry_run)
    result = TidyResult(dry_run=config.dry_run)

    source_dir = config.root_dir / config.source_dir
    target_dir = config.root_dir / config.target_dir

    logger.header(f"Moving files from {config.source_dir} to {config.target_dir}")

    # Ensure source directory exists
    if not source_dir.exists():
        logger.error(f"Source directory does not exist: {source_dir}")
        return result

    # Ensure target directory exists
    if not config.dry_run and not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.verbose(f"Created target directory: {target_dir}")

    # Get all files in source directory
    try:
        files = [f for f in source_dir.iterdir() if f.is_file()]
    except PermissionError as e:
        logger.error(f"Cannot read source directory: {e}")
        return result

    if not files:
        logger.info("No files found in source directory")
        return result

    logger.verbose(f"Found {len(files)} files to process")

    # Process each file
    for file_path in files:
        result.total_processed += 1
        filename = file_path.name

        # Check exclusions
        exclude, reason = should_exclude(filename, config)
        if exclude:
            logger.skip(f"Skipping: {filename} ({reason})")
            result.skipped.append(
                FileOperation(
                    source=file_path,
                    status=OperationStatus.SKIPPED,
                    reason=reason,
                )
            )
            continue

        # Move the file
        operation = move_file(file_path, target_dir, config, logger)

        if operation.status == OperationStatus.MOVED:
            dest_name = operation.destination.name if operation.destination else filename
            logger.success(f"Moved: {filename} → {config.target_dir}/{dest_name}")
            result.moved.append(operation)
        elif operation.status == OperationStatus.DUPLICATE:
            logger.warn(f"Duplicate: {filename} - {operation.reason}")
            result.skipped.append(operation)
        elif operation.status == OperationStatus.FAILED:
            logger.error(f"Failed: {filename} - {operation.reason}")
            result.failed.append(operation)

    # Summary
    if not result.moved and not result.failed:
        logger.info("\nNo files to move")
    else:
        summary_parts = []
        if result.moved:
            summary_parts.append(f"{len(result.moved)} moved")
        if result.skipped:
            summary_parts.append(f"{len(result.skipped)} skipped")
        if result.failed:
            summary_parts.append(f"{len(result.failed)} failed")

        logger.info(f"\n{Colors.BOLD}Summary:{Colors.RESET} {', '.join(summary_parts)}")

    return result


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="tidy",
        description="Automated file organization for repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tidy                           # Run with defaults
  tidy --dry-run                 # Preview changes
  tidy --source drafts           # Custom source directory
  tidy --extensions .md,.txt     # Multiple extensions
""",
    )

    parser.add_argument(
        "--config",
        type=Path,
        help="Path to configuration file (default: .tidyrc.json)",
    )
    parser.add_argument(
        "--source",
        dest="source_dir",
        help="Source directory (default: .)",
    )
    parser.add_argument(
        "--target",
        dest="target_dir",
        help="Target directory (default: 00-inbox)",
    )
    parser.add_argument(
        "--extensions",
        help="Comma-separated file extensions (default: .md)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without moving files",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress all output except errors",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Load configurations with precedence: CLI > ENV > File > Defaults
    try:
        file_config = load_config_file(args.config)
    except FileNotFoundError as e:
        print(f"{Colors.RED}Error:{Colors.RESET} {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"{Colors.RED}Error:{Colors.RESET} Invalid JSON in config file: {e}", file=sys.stderr)
        return 1

    env_config = load_env_config()

    # Merge file and env configs
    merged_config: ConfigDict = {**file_config, **env_config}

    # Create config object
    config = TidyConfig.from_dict(merged_config, Path.cwd())

    # Apply CLI overrides
    if args.source_dir:
        config.source_dir = args.source_dir
    if args.target_dir:
        config.target_dir = args.target_dir
    if args.extensions:
        config.extensions = [e.strip() for e in args.extensions.split(",")]
    if args.dry_run or os.environ.get("TIDY_DRY_RUN") == "true":
        config.dry_run = True
    if args.verbose or os.environ.get("TIDY_VERBOSE") == "true":
        config.verbosity = 2
    if args.quiet:
        config.verbosity = 0

    # Run tidy
    try:
        result = tidy(config)
    except Exception as e:
        print(f"{Colors.RED}Fatal error:{Colors.RESET} {e}", file=sys.stderr)
        return 1

    # Exit with appropriate code
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
