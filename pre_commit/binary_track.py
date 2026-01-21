"""Binary Track - Keep locally-installed binaries up to date with source code.

Tracks binaries developers build from their own source code and detects when
they become stale (out of sync with source changes). Provides rebuild triggers,
watch mode, and pre-commit integration.

Usage:
    binary-track [options]

Options:
    --config PATH       Path to configuration file (default: .binariesrc.json)
    --status            Show status of all tracked binaries
    --check             Check for stale binaries (exit 1 if any stale)
    --rebuild           Rebuild all stale binaries
    --rebuild-all       Rebuild all tracked binaries
    --watch             Watch source files and rebuild on change
    --add               Interactive add a new binary to track
    --remove NAME       Remove a binary from tracking
    --verify            Verify binaries exist and are executable
    --health            Check binary health (exists, executable, in PATH)
    --dry-run           Preview changes without executing
    --verbose           Show detailed output
    --quiet             Suppress all output except errors
    --json              Output in JSON format
    --help              Show this help message
    --version           Show version number

Configuration:
    Create a .binariesrc.json file in your project root:

    {
        "binaries": {
            "mytool": {
                "source_patterns": ["cmd/mytool/**/*.go", "internal/**/*.go"],
                "build_cmd": "go build -o ~/.local/bin/mytool ./cmd/mytool",
                "install_path": "~/.local/bin/mytool",
                "language": "go",
                "rebuild_on_commit": true,
                "check_in_path": true
            },
            "myutil": {
                "source_patterns": ["src/**/*.rs", "Cargo.toml"],
                "build_cmd": "cargo build --release && cp target/release/myutil ~/.local/bin/",
                "install_path": "~/.local/bin/myutil",
                "language": "rust"
            }
        },
        "auto_rebuild": false,
        "stale_threshold_hours": 24,
        "watch_debounce_ms": 500,
        "pre_commit_policy": "warn",
        "track_by": "git_commit"
    }

    Track-by Options:
    - "git_commit": Compare source git commit SHA vs last build commit (recommended)
    - "mtime": Compare source file modification times vs binary mtime
    - "hash": Compare source file hashes vs stored hashes (most precise, slower)

    Pre-commit Policies:
    - "warn": Print warning but allow commit
    - "block": Prevent commit if binaries are stale
    - "ignore": Don't check during pre-commit

Environment Variables:
    BINARY_TRACK_DRY_RUN       Set to 'true' for dry run
    BINARY_TRACK_VERBOSE       Set to 'true' for verbose output
    BINARY_TRACK_AUTO_REBUILD  Set to 'true' to auto-rebuild stale binaries
    BINARY_TRACK_POLICY        Pre-commit policy (warn/block/ignore)
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TypedDict

# Version
__version__ = "1.0.0"

# Default configuration file names
CONFIG_FILE_NAMES = [".binariesrc.json", ".binariesrc", "binaries.config.json"]

# Manifest file for tracking build state
BUILD_MANIFEST_FILE = ".binary-track-manifest.json"

# Pre-commit config file
PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"


class TrackingMethod(Enum):
    """How to track source changes."""

    GIT_COMMIT = "git_commit"
    MTIME = "mtime"
    HASH = "hash"


class PreCommitPolicy(Enum):
    """What to do when stale binaries detected during pre-commit."""

    WARN = "warn"
    BLOCK = "block"
    IGNORE = "ignore"


class BinaryStatus(Enum):
    """Status of a tracked binary."""

    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"
    NOT_EXECUTABLE = "not_executable"
    NOT_IN_PATH = "not_in_path"
    BUILD_FAILED = "build_failed"
    UNKNOWN = "unknown"


class RebuildStatus(Enum):
    """Status of a rebuild operation."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


class BinaryConfigDict(TypedDict, total=False):
    """Type definition for binary configuration."""

    source_patterns: list[str]
    build_cmd: str
    install_path: str
    language: str
    rebuild_on_commit: bool
    check_in_path: bool
    working_dir: str
    env: dict[str, str]
    timeout: int


class ConfigDict(TypedDict, total=False):
    """Type definition for configuration dictionary."""

    binaries: dict[str, BinaryConfigDict]
    auto_rebuild: bool
    stale_threshold_hours: int
    watch_debounce_ms: int
    pre_commit_policy: str
    track_by: str
    parallel_builds: bool
    max_workers: int


@dataclass
class BinaryConfig:
    """Configuration for a single tracked binary."""

    name: str
    source_patterns: list[str] = field(default_factory=list)
    build_cmd: str = ""
    install_path: str = ""
    language: str = ""
    rebuild_on_commit: bool = True
    check_in_path: bool = True
    working_dir: str = "."
    env: dict[str, str] = field(default_factory=dict)
    timeout: int = 300  # 5 minutes default

    @classmethod
    def from_dict(cls, name: str, data: BinaryConfigDict) -> BinaryConfig:
        """Create from dictionary."""
        return cls(
            name=name,
            source_patterns=data.get("source_patterns", []),
            build_cmd=data.get("build_cmd", ""),
            install_path=data.get("install_path", ""),
            language=data.get("language", ""),
            rebuild_on_commit=data.get("rebuild_on_commit", True),
            check_in_path=data.get("check_in_path", True),
            working_dir=data.get("working_dir", "."),
            env=data.get("env", {}),
            timeout=data.get("timeout", 300),
        )

    def get_expanded_install_path(self) -> Path:
        """Get install path with ~ expanded."""
        return Path(os.path.expanduser(self.install_path))


@dataclass
class TrackConfig:
    """Configuration for binary tracking operations."""

    root_dir: Path = field(default_factory=Path.cwd)
    binaries: dict[str, BinaryConfig] = field(default_factory=dict)
    auto_rebuild: bool = False
    stale_threshold_hours: int = 24
    watch_debounce_ms: int = 500
    pre_commit_policy: PreCommitPolicy = PreCommitPolicy.WARN
    track_by: TrackingMethod = TrackingMethod.GIT_COMMIT
    parallel_builds: bool = True
    max_workers: int = 4
    dry_run: bool = False
    verbose: bool = False
    quiet: bool = False
    json_output: bool = False

    @classmethod
    def from_dict(cls, data: ConfigDict, root_dir: Path | None = None) -> TrackConfig:
        """Create from dictionary."""
        binaries = {}
        for name, binary_data in data.get("binaries", {}).items():
            binaries[name] = BinaryConfig.from_dict(name, binary_data)

        policy_str = data.get("pre_commit_policy", "warn")
        try:
            policy = PreCommitPolicy(policy_str)
        except ValueError:
            policy = PreCommitPolicy.WARN

        track_by_str = data.get("track_by", "git_commit")
        try:
            track_by = TrackingMethod(track_by_str)
        except ValueError:
            track_by = TrackingMethod.GIT_COMMIT

        config = cls(
            binaries=binaries,
            auto_rebuild=data.get("auto_rebuild", False),
            stale_threshold_hours=data.get("stale_threshold_hours", 24),
            watch_debounce_ms=data.get("watch_debounce_ms", 500),
            pre_commit_policy=policy,
            track_by=track_by,
            parallel_builds=data.get("parallel_builds", True),
            max_workers=data.get("max_workers", 4),
        )

        if root_dir:
            config.root_dir = root_dir

        return config


@dataclass
class BuildRecord:
    """Record of when a binary was last built."""

    binary_name: str
    built_at: str
    source_commit: str = ""
    source_hashes: dict[str, str] = field(default_factory=dict)
    source_mtimes: dict[str, float] = field(default_factory=dict)
    build_duration: float = 0.0
    success: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "binary_name": self.binary_name,
            "built_at": self.built_at,
            "source_commit": self.source_commit,
            "source_hashes": self.source_hashes,
            "source_mtimes": self.source_mtimes,
            "build_duration": self.build_duration,
            "success": self.success,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BuildRecord:
        """Create from dictionary."""
        return cls(
            binary_name=data["binary_name"],
            built_at=data["built_at"],
            source_commit=data.get("source_commit", ""),
            source_hashes=data.get("source_hashes", {}),
            source_mtimes=data.get("source_mtimes", {}),
            build_duration=data.get("build_duration", 0.0),
            success=data.get("success", True),
            error=data.get("error", ""),
        )


@dataclass
class BuildManifest:
    """Manifest tracking all binary builds."""

    records: dict[str, BuildRecord] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "records": {name: record.to_dict() for name, record in self.records.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> BuildManifest:
        """Create from dictionary."""
        records = {}
        for name, record_data in data.get("records", {}).items():
            records[name] = BuildRecord.from_dict(record_data)
        return cls(
            records=records,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class BinaryStatusResult:
    """Status result for a single binary."""

    name: str
    status: BinaryStatus
    install_path: str = ""
    exists: bool = False
    executable: bool = False
    in_path: bool = False
    last_built: str = ""
    last_commit: str = ""
    current_commit: str = ""
    commits_behind: int = 0
    stale_files: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class RebuildResult:
    """Result of a rebuild operation."""

    name: str
    status: RebuildStatus
    duration: float = 0.0
    message: str = ""
    output: str = ""


@dataclass
class TrackResult:
    """Result of a tracking operation."""

    statuses: list[BinaryStatusResult] = field(default_factory=list)
    rebuilds: list[RebuildResult] = field(default_factory=list)
    all_current: bool = False
    stale_count: int = 0
    missing_count: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON output."""
        return {
            "all_current": self.all_current,
            "stale_count": self.stale_count,
            "missing_count": self.missing_count,
            "dry_run": self.dry_run,
            "statuses": [
                {
                    "name": s.name,
                    "status": s.status.value,
                    "install_path": s.install_path,
                    "exists": s.exists,
                    "executable": s.executable,
                    "in_path": s.in_path,
                    "last_built": s.last_built,
                    "commits_behind": s.commits_behind,
                    "stale_files": s.stale_files,
                    "message": s.message,
                }
                for s in self.statuses
            ],
            "rebuilds": [
                {
                    "name": r.name,
                    "status": r.status.value,
                    "duration": r.duration,
                    "message": r.message,
                }
                for r in self.rebuilds
            ],
        }


class Colors:
    """ANSI color codes for terminal output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    GRAY = "\033[90m"

    @classmethod
    def disable(cls) -> None:
        """Disable colors for non-TTY output."""
        cls.RESET = ""
        cls.BOLD = ""
        cls.DIM = ""
        cls.RED = ""
        cls.GREEN = ""
        cls.YELLOW = ""
        cls.BLUE = ""
        cls.MAGENTA = ""
        cls.CYAN = ""
        cls.WHITE = ""
        cls.GRAY = ""


class Logger:
    """Logger with colored output and verbosity levels."""

    def __init__(self, verbose: bool = False, quiet: bool = False, json_output: bool = False) -> None:
        self.verbose = verbose
        self.quiet = quiet
        self.json_output = json_output
        if not sys.stdout.isatty() or json_output:
            Colors.disable()

    def info(self, message: str) -> None:
        """Print info message."""
        if not self.quiet and not self.json_output:
            print(f"{Colors.BLUE}ℹ{Colors.RESET} {message}")

    def success(self, message: str) -> None:
        """Print success message."""
        if not self.quiet and not self.json_output:
            print(f"{Colors.GREEN}✓{Colors.RESET} {message}")

    def warn(self, message: str) -> None:
        """Print warning message."""
        if not self.quiet and not self.json_output:
            print(f"{Colors.YELLOW}⚠{Colors.RESET} {message}")

    def error(self, message: str) -> None:
        """Print error message."""
        if not self.json_output:
            print(f"{Colors.RED}✗{Colors.RESET} {message}", file=sys.stderr)

    def debug(self, message: str) -> None:
        """Print debug message (verbose only)."""
        if self.verbose and not self.quiet and not self.json_output:
            print(f"{Colors.GRAY}  {message}{Colors.RESET}")

    def header(self, message: str) -> None:
        """Print header message."""
        if not self.quiet and not self.json_output:
            print(f"\n{Colors.BOLD}=== {message} ==={Colors.RESET}")

    def status_line(self, icon: str, color: str, name: str, detail: str) -> None:
        """Print a status line."""
        if not self.quiet and not self.json_output:
            print(f"  {color}{icon}{Colors.RESET} {Colors.BOLD}{name}{Colors.RESET} {detail}")


def load_config_file(config_path: Path | None = None, root_dir: Path | None = None) -> ConfigDict:
    """Load configuration from file."""
    if root_dir is None:
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

    if os.environ.get("BINARY_TRACK_AUTO_REBUILD") == "true":
        config["auto_rebuild"] = True
    if os.environ.get("BINARY_TRACK_POLICY"):
        config["pre_commit_policy"] = os.environ["BINARY_TRACK_POLICY"]

    return config


def save_manifest(manifest: BuildManifest, root_dir: Path) -> None:
    """Save the build manifest to disk."""
    manifest.updated_at = datetime.now(timezone.utc).isoformat()
    if not manifest.created_at:
        manifest.created_at = manifest.updated_at

    manifest_path = root_dir / BUILD_MANIFEST_FILE
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, indent=2)


def load_manifest(root_dir: Path) -> BuildManifest:
    """Load the build manifest from disk."""
    manifest_path = root_dir / BUILD_MANIFEST_FILE
    if not manifest_path.exists():
        return BuildManifest()

    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        return BuildManifest.from_dict(data)
    except (json.JSONDecodeError, KeyError):
        return BuildManifest()


def get_git_root(path: Path) -> Path | None:
    """Get the git repository root."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_current_commit(root_dir: Path) -> str:
    """Get the current git commit SHA."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def get_commit_for_files(root_dir: Path, patterns: list[str]) -> str:
    """Get the latest commit that touched any of the given file patterns."""
    try:
        # Expand patterns to actual files
        files = expand_patterns(root_dir, patterns)
        if not files:
            return ""

        # Get most recent commit that touched any of these files
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--"] + [str(f) for f in files],
            cwd=root_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def get_commits_between(root_dir: Path, old_commit: str, new_commit: str) -> int:
    """Count commits between two refs."""
    if not old_commit or not new_commit:
        return -1
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{old_commit}..{new_commit}"],
            cwd=root_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return -1


def expand_patterns(root_dir: Path, patterns: list[str]) -> list[Path]:
    """Expand glob patterns to actual file paths."""
    files: list[Path] = []
    for pattern in patterns:
        # Handle ** for recursive matching
        if "**" in pattern:
            matched = list(root_dir.glob(pattern))
        else:
            matched = list(root_dir.glob(pattern))

        for path in matched:
            if path.is_file() and path not in files:
                files.append(path)

    return files


def compute_file_hash(file_path: Path, algorithm: str = "sha256") -> str:
    """Compute hash of file contents."""
    hash_func = hashlib.new(algorithm)
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_func.update(chunk)
        return hash_func.hexdigest()
    except OSError:
        return ""


def get_source_fingerprint(
    root_dir: Path, patterns: list[str], method: TrackingMethod
) -> tuple[str, dict[str, str], dict[str, float]]:
    """Get a fingerprint of source files using the specified method.

    Returns: (commit_sha, hashes_dict, mtimes_dict)
    """
    files = expand_patterns(root_dir, patterns)

    if method == TrackingMethod.GIT_COMMIT:
        commit = get_commit_for_files(root_dir, patterns)
        return commit, {}, {}

    elif method == TrackingMethod.HASH:
        hashes = {}
        for f in files:
            rel_path = str(f.relative_to(root_dir))
            hashes[rel_path] = compute_file_hash(f)
        return "", hashes, {}

    elif method == TrackingMethod.MTIME:
        mtimes = {}
        for f in files:
            try:
                rel_path = str(f.relative_to(root_dir))
                mtimes[rel_path] = f.stat().st_mtime
            except OSError:
                pass
        return "", {}, mtimes

    return "", {}, {}


def is_binary_stale(
    binary_config: BinaryConfig,
    build_record: BuildRecord | None,
    root_dir: Path,
    method: TrackingMethod,
) -> tuple[bool, str, list[str]]:
    """Check if a binary is stale compared to its source files.

    Returns: (is_stale, reason, list_of_changed_files)
    """
    if not build_record:
        return True, "never built", []

    current_commit, current_hashes, current_mtimes = get_source_fingerprint(
        root_dir, binary_config.source_patterns, method
    )

    if method == TrackingMethod.GIT_COMMIT:
        if not current_commit:
            return False, "could not determine source commit", []
        if not build_record.source_commit:
            return True, "no build commit recorded", []
        if current_commit != build_record.source_commit:
            commits_behind = get_commits_between(root_dir, build_record.source_commit, current_commit)
            return True, f"source is {commits_behind} commit(s) ahead", []
        return False, "up to date", []

    elif method == TrackingMethod.HASH:
        changed = []
        for path, new_hash in current_hashes.items():
            old_hash = build_record.source_hashes.get(path, "")
            if old_hash != new_hash:
                changed.append(path)
        # Check for new files
        for path in current_hashes:
            if path not in build_record.source_hashes:
                if path not in changed:
                    changed.append(path)
        if changed:
            return True, f"{len(changed)} file(s) changed", changed
        return False, "up to date", []

    elif method == TrackingMethod.MTIME:
        binary_path = binary_config.get_expanded_install_path()
        if not binary_path.exists():
            return True, "binary missing", []
        try:
            binary_mtime = binary_path.stat().st_mtime
        except OSError:
            return True, "could not stat binary", []

        changed = []
        for path, mtime in current_mtimes.items():
            if mtime > binary_mtime:
                changed.append(path)
        if changed:
            return True, f"{len(changed)} file(s) modified after build", changed
        return False, "up to date", []

    return False, "unknown tracking method", []


def check_binary_health(binary_config: BinaryConfig) -> BinaryStatusResult:
    """Check the health status of a binary."""
    path = binary_config.get_expanded_install_path()
    result = BinaryStatusResult(
        name=binary_config.name,
        status=BinaryStatus.UNKNOWN,
        install_path=str(path),
    )

    # Check if file exists
    if not path.exists():
        result.status = BinaryStatus.MISSING
        result.message = f"Binary not found at {path}"
        return result

    result.exists = True

    # Check if executable
    if not os.access(path, os.X_OK):
        result.status = BinaryStatus.NOT_EXECUTABLE
        result.message = "Binary exists but is not executable"
        return result

    result.executable = True

    # Check if in PATH
    if binary_config.check_in_path:
        binary_name = path.name
        which_result = shutil.which(binary_name)
        if which_result:
            result.in_path = True
            # Verify it's the same binary
            if Path(which_result).resolve() != path.resolve():
                result.message = f"Warning: {binary_name} in PATH points to different location: {which_result}"
        else:
            result.in_path = False
            result.status = BinaryStatus.NOT_IN_PATH
            result.message = f"Binary not found in PATH. Add {path.parent} to PATH"
            return result

    result.status = BinaryStatus.CURRENT
    return result


def get_binary_status(
    binary_config: BinaryConfig,
    config: TrackConfig,
    manifest: BuildManifest,
    logger: Logger,
) -> BinaryStatusResult:
    """Get full status of a binary including staleness check."""
    # First check health
    result = check_binary_health(binary_config)
    build_record = manifest.records.get(binary_config.name)

    if build_record:
        result.last_built = build_record.built_at
        result.last_commit = build_record.source_commit

    # If binary doesn't exist, no need to check staleness
    if result.status == BinaryStatus.MISSING:
        return result

    # Check staleness
    is_stale, reason, changed_files = is_binary_stale(
        binary_config, build_record, config.root_dir, config.track_by
    )

    if is_stale:
        result.status = BinaryStatus.STALE
        result.stale_files = changed_files
        result.message = reason

        # Calculate commits behind
        if config.track_by == TrackingMethod.GIT_COMMIT and build_record:
            current_commit = get_commit_for_files(config.root_dir, binary_config.source_patterns)
            result.current_commit = current_commit
            if build_record.source_commit:
                result.commits_behind = get_commits_between(
                    config.root_dir, build_record.source_commit, current_commit
                )
    else:
        if result.status == BinaryStatus.CURRENT:
            result.message = "Up to date"

    return result


def rebuild_binary(
    binary_config: BinaryConfig,
    config: TrackConfig,
    manifest: BuildManifest,
    logger: Logger,
) -> RebuildResult:
    """Rebuild a single binary."""
    result = RebuildResult(name=binary_config.name, status=RebuildStatus.SKIPPED)

    if not binary_config.build_cmd:
        result.message = "No build command configured"
        return result

    if config.dry_run:
        result.status = RebuildStatus.DRY_RUN
        result.message = f"Would run: {binary_config.build_cmd}"
        logger.info(f"[DRY-RUN] Would rebuild {binary_config.name}: {binary_config.build_cmd}")
        return result

    logger.info(f"Rebuilding {binary_config.name}...")
    logger.debug(f"Command: {binary_config.build_cmd}")

    # Prepare environment
    env = os.environ.copy()
    env.update(binary_config.env)

    # Determine working directory
    working_dir = config.root_dir / binary_config.working_dir

    start_time = time.time()
    try:
        proc = subprocess.run(
            binary_config.build_cmd,
            shell=True,
            cwd=working_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=binary_config.timeout,
        )
        duration = time.time() - start_time
        result.duration = duration

        if proc.returncode == 0:
            result.status = RebuildStatus.SUCCESS
            result.message = f"Built in {duration:.1f}s"
            result.output = proc.stdout

            # Update manifest
            current_commit, current_hashes, current_mtimes = get_source_fingerprint(
                config.root_dir, binary_config.source_patterns, config.track_by
            )
            manifest.records[binary_config.name] = BuildRecord(
                binary_name=binary_config.name,
                built_at=datetime.now(timezone.utc).isoformat(),
                source_commit=current_commit,
                source_hashes=current_hashes,
                source_mtimes=current_mtimes,
                build_duration=duration,
                success=True,
            )
            logger.success(f"Rebuilt {binary_config.name} in {duration:.1f}s")
        else:
            result.status = RebuildStatus.FAILED
            result.message = f"Build failed with exit code {proc.returncode}"
            result.output = proc.stderr or proc.stdout
            logger.error(f"Failed to rebuild {binary_config.name}: {result.message}")
            if result.output and config.verbose:
                for line in result.output.strip().split("\n"):
                    logger.debug(f"  {line}")

    except subprocess.TimeoutExpired:
        result.status = RebuildStatus.FAILED
        result.message = f"Build timed out after {binary_config.timeout}s"
        result.duration = time.time() - start_time
        logger.error(f"Build timed out for {binary_config.name}")

    except Exception as e:
        result.status = RebuildStatus.FAILED
        result.message = str(e)
        result.duration = time.time() - start_time
        logger.error(f"Build error for {binary_config.name}: {e}")

    return result


def check_all_binaries(config: TrackConfig, logger: Logger) -> TrackResult:
    """Check status of all tracked binaries."""
    result = TrackResult(dry_run=config.dry_run)
    manifest = load_manifest(config.root_dir)

    logger.header("Binary Status")

    for name, binary_config in config.binaries.items():
        status = get_binary_status(binary_config, config, manifest, logger)
        result.statuses.append(status)

        # Display status
        if status.status == BinaryStatus.CURRENT:
            logger.status_line("✓", Colors.GREEN, name, f"- {status.message}")
        elif status.status == BinaryStatus.STALE:
            result.stale_count += 1
            detail = status.message
            if status.commits_behind > 0:
                detail = f"- {status.commits_behind} commit(s) behind"
            logger.status_line("⚠", Colors.YELLOW, name, f"- STALE {detail}")
            if status.stale_files and config.verbose:
                for f in status.stale_files[:5]:
                    logger.debug(f"    └ {f}")
                if len(status.stale_files) > 5:
                    logger.debug(f"    └ ... and {len(status.stale_files) - 5} more")
        elif status.status == BinaryStatus.MISSING:
            result.missing_count += 1
            logger.status_line("✗", Colors.RED, name, f"- MISSING at {status.install_path}")
        elif status.status == BinaryStatus.NOT_EXECUTABLE:
            logger.status_line("✗", Colors.RED, name, "- NOT EXECUTABLE")
        elif status.status == BinaryStatus.NOT_IN_PATH:
            logger.status_line("⚠", Colors.YELLOW, name, f"- {status.message}")
        else:
            logger.status_line("?", Colors.GRAY, name, f"- {status.message}")

    result.all_current = result.stale_count == 0 and result.missing_count == 0

    # Summary
    if not config.quiet and not config.json_output:
        print()
        if result.all_current:
            logger.success(f"All {len(config.binaries)} binary(ies) up to date")
        else:
            parts = []
            if result.stale_count > 0:
                parts.append(f"{result.stale_count} stale")
            if result.missing_count > 0:
                parts.append(f"{result.missing_count} missing")
            current = len(config.binaries) - result.stale_count - result.missing_count
            if current > 0:
                parts.append(f"{current} current")
            logger.info(f"Summary: {', '.join(parts)}")

    return result


def rebuild_stale_binaries(config: TrackConfig, logger: Logger, rebuild_all: bool = False) -> TrackResult:
    """Rebuild stale (or all) binaries."""
    result = TrackResult(dry_run=config.dry_run)
    manifest = load_manifest(config.root_dir)

    # First, get status of all binaries
    binaries_to_rebuild: list[BinaryConfig] = []

    for name, binary_config in config.binaries.items():
        status = get_binary_status(binary_config, config, manifest, logger)
        result.statuses.append(status)

        if rebuild_all:
            binaries_to_rebuild.append(binary_config)
        elif status.status in (BinaryStatus.STALE, BinaryStatus.MISSING):
            binaries_to_rebuild.append(binary_config)

    if not binaries_to_rebuild:
        logger.success("All binaries are up to date")
        result.all_current = True
        return result

    logger.header(f"Rebuilding {len(binaries_to_rebuild)} binary(ies)")

    # Rebuild binaries
    if config.parallel_builds and len(binaries_to_rebuild) > 1:
        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            futures = {
                executor.submit(rebuild_binary, bc, config, manifest, logger): bc
                for bc in binaries_to_rebuild
            }
            for future in as_completed(futures):
                rebuild_result = future.result()
                result.rebuilds.append(rebuild_result)
    else:
        for binary_config in binaries_to_rebuild:
            rebuild_result = rebuild_binary(binary_config, config, manifest, logger)
            result.rebuilds.append(rebuild_result)

    # Save manifest
    if not config.dry_run:
        save_manifest(manifest, config.root_dir)

    # Summary
    success_count = sum(1 for r in result.rebuilds if r.status == RebuildStatus.SUCCESS)
    failed_count = sum(1 for r in result.rebuilds if r.status == RebuildStatus.FAILED)

    if not config.quiet and not config.json_output:
        print()
        if failed_count == 0:
            logger.success(f"Successfully rebuilt {success_count} binary(ies)")
        else:
            logger.warn(f"Rebuilt {success_count}, failed {failed_count}")

    result.stale_count = failed_count
    result.all_current = failed_count == 0

    return result


def verify_binaries(config: TrackConfig, logger: Logger) -> TrackResult:
    """Verify all binaries exist and are healthy."""
    result = TrackResult(dry_run=config.dry_run)

    logger.header("Binary Health Check")

    for name, binary_config in config.binaries.items():
        status = check_binary_health(binary_config)
        result.statuses.append(status)

        if status.status == BinaryStatus.CURRENT:
            parts = ["exists", "executable"]
            if status.in_path:
                parts.append("in PATH")
            logger.status_line("✓", Colors.GREEN, name, f"- {', '.join(parts)}")
        elif status.status == BinaryStatus.MISSING:
            result.missing_count += 1
            logger.status_line("✗", Colors.RED, name, f"- MISSING at {status.install_path}")
        elif status.status == BinaryStatus.NOT_EXECUTABLE:
            logger.status_line("✗", Colors.RED, name, f"- exists but not executable")
        elif status.status == BinaryStatus.NOT_IN_PATH:
            result.stale_count += 1  # Treat as issue
            logger.status_line("⚠", Colors.YELLOW, name, f"- {status.message}")

    result.all_current = result.missing_count == 0 and result.stale_count == 0
    return result


def watch_sources(config: TrackConfig, logger: Logger) -> None:
    """Watch source files and rebuild on changes."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        logger.error("Watch mode requires 'watchdog' package. Install with: pip install watchdog")
        return

    logger.header("Watch Mode")
    logger.info("Watching for source changes... (Ctrl+C to stop)")

    # Debounce tracking
    pending_rebuilds: dict[str, float] = {}
    lock = threading.Lock()

    class ChangeHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.is_directory:
                return
            self._handle_change(event.src_path)

        def on_created(self, event):
            if event.is_directory:
                return
            self._handle_change(event.src_path)

        def _handle_change(self, file_path: str):
            path = Path(file_path)

            # Check which binaries are affected
            for name, binary_config in config.binaries.items():
                for pattern in binary_config.source_patterns:
                    if path.match(pattern) or fnmatch.fnmatch(str(path), pattern):
                        with lock:
                            pending_rebuilds[name] = time.time()
                        break

    # Process pending rebuilds in a background thread
    stop_event = threading.Event()

    def rebuild_worker():
        debounce_sec = config.watch_debounce_ms / 1000.0
        manifest = load_manifest(config.root_dir)

        while not stop_event.is_set():
            time.sleep(0.1)

            with lock:
                now = time.time()
                ready = [
                    name for name, ts in pending_rebuilds.items()
                    if now - ts > debounce_sec
                ]
                for name in ready:
                    del pending_rebuilds[name]

            for name in ready:
                if name in config.binaries:
                    logger.info(f"Source changed, rebuilding {name}...")
                    rebuild_binary(config.binaries[name], config, manifest, logger)
                    save_manifest(manifest, config.root_dir)

    worker = threading.Thread(target=rebuild_worker, daemon=True)
    worker.start()

    # Set up observer
    observer = Observer()
    handler = ChangeHandler()

    # Watch all source patterns
    watched_dirs: set[Path] = set()
    for binary_config in config.binaries.values():
        for pattern in binary_config.source_patterns:
            # Extract base directory from pattern
            parts = pattern.split("/")
            base = config.root_dir
            for part in parts:
                if "*" in part or "?" in part:
                    break
                base = base / part
            if base.exists() and base.is_dir():
                watched_dirs.add(base)
            else:
                watched_dirs.add(config.root_dir)

    for dir_path in watched_dirs:
        observer.schedule(handler, str(dir_path), recursive=True)
        logger.debug(f"Watching: {dir_path}")

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\nStopping watch mode...")
        stop_event.set()
        observer.stop()
    observer.join()


def pre_commit_check(config: TrackConfig, logger: Logger) -> int:
    """Check binaries for pre-commit hook. Returns exit code."""
    if config.pre_commit_policy == PreCommitPolicy.IGNORE:
        return 0

    result = check_all_binaries(config, logger)

    if result.all_current:
        return 0

    if config.pre_commit_policy == PreCommitPolicy.WARN:
        if result.stale_count > 0:
            logger.warn(f"{result.stale_count} stale binary(ies) detected. Consider running: binary-track --rebuild")
        return 0

    elif config.pre_commit_policy == PreCommitPolicy.BLOCK:
        if result.stale_count > 0:
            logger.error(f"Commit blocked: {result.stale_count} stale binary(ies). Run: binary-track --rebuild")
            return 1

    return 0


def add_binary_interactive(config: TrackConfig, logger: Logger) -> None:
    """Interactively add a new binary to track."""
    logger.header("Add New Binary")

    print()
    name = input("Binary name: ").strip()
    if not name:
        logger.error("Binary name is required")
        return

    if name in config.binaries:
        logger.error(f"Binary '{name}' already exists in configuration")
        return

    source_patterns_input = input("Source patterns (comma-separated, e.g. 'src/**/*.go,go.mod'): ").strip()
    source_patterns = [p.strip() for p in source_patterns_input.split(",") if p.strip()]

    build_cmd = input("Build command: ").strip()
    install_path = input("Install path (e.g. ~/.local/bin/mytool): ").strip()

    language = input("Language (go/rust/python/node/other) [optional]: ").strip()

    # Create configuration
    new_binary = BinaryConfig(
        name=name,
        source_patterns=source_patterns,
        build_cmd=build_cmd,
        install_path=install_path,
        language=language,
    )

    # Show preview
    print()
    logger.info("Configuration preview:")
    print(f"  Name: {name}")
    print(f"  Source patterns: {source_patterns}")
    print(f"  Build command: {build_cmd}")
    print(f"  Install path: {install_path}")
    if language:
        print(f"  Language: {language}")

    print()
    confirm = input("Save this configuration? [y/N]: ").strip().lower()
    if confirm != "y":
        logger.info("Cancelled")
        return

    # Load existing config file or create new one
    config_path = config.root_dir / CONFIG_FILE_NAMES[0]
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            file_config = json.load(f)
    else:
        file_config = {"binaries": {}}

    if "binaries" not in file_config:
        file_config["binaries"] = {}

    file_config["binaries"][name] = {
        "source_patterns": source_patterns,
        "build_cmd": build_cmd,
        "install_path": install_path,
        "language": language,
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(file_config, f, indent=2)

    logger.success(f"Added '{name}' to {config_path}")


def remove_binary(config: TrackConfig, logger: Logger, name: str) -> None:
    """Remove a binary from tracking."""
    if name not in config.binaries:
        logger.error(f"Binary '{name}' not found in configuration")
        return

    config_path = config.root_dir / CONFIG_FILE_NAMES[0]
    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        return

    with open(config_path, encoding="utf-8") as f:
        file_config = json.load(f)

    if "binaries" in file_config and name in file_config["binaries"]:
        del file_config["binaries"][name]

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(file_config, f, indent=2)

        # Also remove from manifest
        manifest = load_manifest(config.root_dir)
        if name in manifest.records:
            del manifest.records[name]
            save_manifest(manifest, config.root_dir)

        logger.success(f"Removed '{name}' from tracking")
    else:
        logger.error(f"Binary '{name}' not found in configuration file")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="binary-track",
        description="Keep locally-installed binaries up to date with source code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  binary-track --status           # Show status of all tracked binaries
  binary-track --check            # Check for stale binaries (exit 1 if stale)
  binary-track --rebuild          # Rebuild stale binaries
  binary-track --rebuild-all      # Rebuild all binaries
  binary-track --watch            # Watch sources and auto-rebuild
  binary-track --verify           # Check binary health (exists, executable, PATH)
  binary-track --add              # Interactively add a new binary
  binary-track --remove mytool    # Remove a binary from tracking

Configuration:
  Create .binariesrc.json in your project root with binary definitions.

Tracking Methods:
  - git_commit: Track source by git commit (recommended)
  - mtime: Track source file modification times
  - hash: Track source file content hashes

Pre-commit Integration:
  Add to .pre-commit-config.yaml:
    - repo: local
      hooks:
        - id: binary-track
          name: Check binary freshness
          entry: binary-track --check
          language: python
          pass_filenames: false
          always_run: true
""",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to configuration file (default: .binariesrc.json)",
    )

    # Actions (mutually exclusive main commands)
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--status",
        action="store_true",
        help="Show status of all tracked binaries",
    )
    action_group.add_argument(
        "--check",
        action="store_true",
        help="Check for stale binaries (exit 1 if any stale)",
    )
    action_group.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild stale binaries",
    )
    action_group.add_argument(
        "--rebuild-all",
        action="store_true",
        help="Rebuild all tracked binaries",
    )
    action_group.add_argument(
        "--watch",
        action="store_true",
        help="Watch source files and rebuild on change",
    )
    action_group.add_argument(
        "--verify",
        action="store_true",
        help="Verify binaries exist and are executable",
    )
    action_group.add_argument(
        "--health",
        action="store_true",
        help="Alias for --verify",
    )
    action_group.add_argument(
        "--add",
        action="store_true",
        help="Interactively add a new binary to track",
    )
    action_group.add_argument(
        "--remove",
        metavar="NAME",
        help="Remove a binary from tracking",
    )
    action_group.add_argument(
        "--pre-commit",
        action="store_true",
        help="Run pre-commit check (respects policy setting)",
    )

    # Options
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without executing",
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
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output in JSON format",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)

    # Load configuration
    root_dir = Path.cwd()
    try:
        file_config = load_config_file(args.config, root_dir)
    except FileNotFoundError as e:
        print(f"{Colors.RED}Error:{Colors.RESET} {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"{Colors.RED}Error:{Colors.RESET} Invalid JSON in config file: {e}", file=sys.stderr)
        return 1

    env_config = load_env_config()
    merged_config: ConfigDict = {**file_config, **env_config}
    config = TrackConfig.from_dict(merged_config, root_dir)

    # Apply CLI overrides
    if args.dry_run or os.environ.get("BINARY_TRACK_DRY_RUN") == "true":
        config.dry_run = True
    if args.verbose or os.environ.get("BINARY_TRACK_VERBOSE") == "true":
        config.verbose = True
    if args.quiet:
        config.quiet = True
    if args.json_output:
        config.json_output = True

    logger = Logger(config.verbose, config.quiet, config.json_output)

    # Check if we have any binaries configured (for most actions)
    if not config.binaries and not args.add:
        if not args.json_output:
            logger.info("No binaries configured. Use --add to add a binary or create .binariesrc.json")
            logger.info("\nExample configuration:")
            example = {
                "binaries": {
                    "mytool": {
                        "source_patterns": ["cmd/mytool/**/*.go", "internal/**/*.go"],
                        "build_cmd": "go build -o ~/.local/bin/mytool ./cmd/mytool",
                        "install_path": "~/.local/bin/mytool",
                        "language": "go",
                    }
                },
                "track_by": "git_commit",
                "pre_commit_policy": "warn",
            }
            print(json.dumps(example, indent=2))
        return 0

    # Execute action
    try:
        if args.add:
            add_binary_interactive(config, logger)
            return 0

        elif args.remove:
            remove_binary(config, logger, args.remove)
            return 0

        elif args.watch:
            watch_sources(config, logger)
            return 0

        elif args.rebuild:
            result = rebuild_stale_binaries(config, logger, rebuild_all=False)
            if config.json_output:
                print(json.dumps(result.to_dict(), indent=2))
            return 0 if result.all_current else 1

        elif args.rebuild_all:
            result = rebuild_stale_binaries(config, logger, rebuild_all=True)
            if config.json_output:
                print(json.dumps(result.to_dict(), indent=2))
            return 0 if result.all_current else 1

        elif args.verify or args.health:
            result = verify_binaries(config, logger)
            if config.json_output:
                print(json.dumps(result.to_dict(), indent=2))
            return 0 if result.all_current else 1

        elif args.pre_commit:
            return pre_commit_check(config, logger)

        elif args.check:
            result = check_all_binaries(config, logger)
            if config.json_output:
                print(json.dumps(result.to_dict(), indent=2))
            return 0 if result.all_current else 1

        else:
            # Default action: show status
            result = check_all_binaries(config, logger)
            if config.json_output:
                print(json.dumps(result.to_dict(), indent=2))
            return 0

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if config.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
