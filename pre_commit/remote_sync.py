"""Remote Sync - Keep multiple git remotes in sync automatically.

Provides automated synchronization of git commits across multiple remotes
with parallel pushing, health checks, divergence detection, and offline queuing.

Usage:
    remote-sync [options]

Options:
    --config PATH       Path to configuration file (default: .remotesyncrc.json)
    --push              Push current branch to all configured remotes
    --push-all          Push all branches to their configured remotes
    --status            Show sync status dashboard
    --health-check      Check connectivity to all remotes
    --process-queue     Process offline queue of failed pushes
    --clear-queue       Clear the offline queue
    --dry-run           Preview changes without executing
    --verbose           Show detailed output
    --quiet             Suppress all output except errors
    --remote NAME       Target specific remote(s), comma-separated
    --branch NAME       Target specific branch (default: current branch)
    --force             Allow force push (requires explicit flag)
    --no-parallel       Disable parallel pushing
    --help              Show this help message
    --version           Show version number

Configuration:
    Create a .remotesyncrc.json file in your project root:

    {
        "remotes": {
            "origin": {
                "priority": 1,
                "branches": ["*"],
                "force_push": "block",
                "retry": 3,
                "timeout": 30
            },
            "github-mirror": {
                "priority": 2,
                "branches": ["main", "develop"],
                "force_push": "warn"
            },
            "backup": {
                "priority": 3,
                "branches": ["main"],
                "retry": 5,
                "group": "backups"
            }
        },
        "parallel": true,
        "max_workers": 4,
        "offline_queue": true,
        "health_check_timeout": 5,
        "retry_base_delay": 1.0,
        "retry_max_delay": 30.0
    }

Environment Variables:
    REMOTE_SYNC_PARALLEL        Set to 'false' to disable parallel push
    REMOTE_SYNC_DRY_RUN         Set to 'true' for dry run
    REMOTE_SYNC_VERBOSE         Set to 'true' for verbose output
    REMOTE_SYNC_OFFLINE_QUEUE   Set to 'true' to enable offline queue
    REMOTE_SYNC_MAX_WORKERS     Maximum parallel workers (default: 4)
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import random
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
CONFIG_FILE_NAMES = [".remotesyncrc.json", ".remotesyncrc", "remote-sync.config.json"]

# Offline queue file
QUEUE_FILE = ".remote-sync-queue.json"

# Lock file for queue operations
QUEUE_LOCK_FILE = ".remote-sync-queue.lock"


class ForcePushPolicy(Enum):
    """Policy for handling force pushes."""

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class PushStatus(Enum):
    """Status of a push operation."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    QUEUED = "queued"
    BLOCKED = "blocked"


class RemoteStatus(Enum):
    """Status of a remote."""

    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class SyncState(Enum):
    """Sync state between local and remote."""

    IN_SYNC = "in_sync"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    NO_REMOTE = "no_remote"
    UNKNOWN = "unknown"


class VpnConfigDict(TypedDict, total=False):
    """Type definition for VPN configuration."""

    connect_cmd: str
    disconnect_cmd: str
    check_cmd: str
    timeout: int
    auto_connect: bool


class RemoteConfigDict(TypedDict, total=False):
    """Type definition for remote configuration."""

    priority: int
    branches: list[str]
    force_push: str
    retry: int
    timeout: int
    group: str
    url: str
    vpn: str | VpnConfigDict  # VPN name or inline config


class ConfigDict(TypedDict, total=False):
    """Type definition for configuration dictionary."""

    remotes: dict[str, RemoteConfigDict]
    parallel: bool
    max_workers: int
    offline_queue: bool
    health_check_timeout: int
    retry_base_delay: float
    retry_max_delay: float
    auto_fetch: bool
    vpn: dict[str, VpnConfigDict]  # Named VPN configurations


@dataclass
class VpnConfig:
    """Configuration for a VPN connection."""

    name: str
    connect_cmd: str
    disconnect_cmd: str
    check_cmd: str = ""  # Command to check if VPN is connected
    timeout: int = 30
    auto_connect: bool = True  # Auto-connect if remote is unreachable

    @classmethod
    def from_dict(cls, name: str, data: VpnConfigDict) -> VpnConfig:
        """Create from dictionary."""
        return cls(
            name=name,
            connect_cmd=data.get("connect_cmd", ""),
            disconnect_cmd=data.get("disconnect_cmd", ""),
            check_cmd=data.get("check_cmd", ""),
            timeout=data.get("timeout", 30),
            auto_connect=data.get("auto_connect", True),
        )


@dataclass
class RemoteConfig:
    """Configuration for a single remote."""

    name: str
    priority: int = 1
    branches: list[str] = field(default_factory=lambda: ["*"])
    force_push: ForcePushPolicy = ForcePushPolicy.BLOCK
    retry: int = 3
    timeout: int = 30
    group: str = "default"
    url: str | None = None
    vpn: str | None = None  # VPN name to use for this remote

    @classmethod
    def from_dict(cls, name: str, data: RemoteConfigDict) -> RemoteConfig:
        """Create from dictionary."""
        force_push_str = data.get("force_push", "block")
        try:
            force_push = ForcePushPolicy(force_push_str)
        except ValueError:
            force_push = ForcePushPolicy.BLOCK

        # Handle vpn field - can be string name or inline config
        vpn_value = data.get("vpn")
        vpn_name = None
        if isinstance(vpn_value, str):
            vpn_name = vpn_value
        elif isinstance(vpn_value, dict):
            # Inline VPN config - use remote name as VPN name
            vpn_name = f"_inline_{name}"

        return cls(
            name=name,
            priority=data.get("priority", 1),
            branches=data.get("branches", ["*"]),
            force_push=force_push,
            retry=data.get("retry", 3),
            timeout=data.get("timeout", 30),
            group=data.get("group", "default"),
            url=data.get("url"),
            vpn=vpn_name,
        )


@dataclass
class SyncConfig:
    """Configuration for remote sync operations."""

    remotes: dict[str, RemoteConfig] = field(default_factory=dict)
    vpn_configs: dict[str, VpnConfig] = field(default_factory=dict)
    parallel: bool = True
    max_workers: int = 4
    offline_queue: bool = True
    health_check_timeout: int = 5
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0
    auto_fetch: bool = True
    dry_run: bool = False
    verbose: bool = False
    quiet: bool = False

    @classmethod
    def from_dict(cls, data: ConfigDict) -> SyncConfig:
        """Create from dictionary."""
        remotes = {}
        vpn_configs = {}

        # Parse global VPN configurations
        for vpn_name, vpn_data in data.get("vpn", {}).items():
            vpn_configs[vpn_name] = VpnConfig.from_dict(vpn_name, vpn_data)

        # Parse remote configurations
        for name, remote_data in data.get("remotes", {}).items():
            remotes[name] = RemoteConfig.from_dict(name, remote_data)

            # Handle inline VPN config in remote
            vpn_value = remote_data.get("vpn")
            if isinstance(vpn_value, dict):
                inline_vpn_name = f"_inline_{name}"
                vpn_configs[inline_vpn_name] = VpnConfig.from_dict(inline_vpn_name, vpn_value)

        return cls(
            remotes=remotes,
            vpn_configs=vpn_configs,
            parallel=data.get("parallel", True),
            max_workers=data.get("max_workers", 4),
            offline_queue=data.get("offline_queue", True),
            health_check_timeout=data.get("health_check_timeout", 5),
            retry_base_delay=data.get("retry_base_delay", 1.0),
            retry_max_delay=data.get("retry_max_delay", 30.0),
            auto_fetch=data.get("auto_fetch", True),
        )


@dataclass
class PushResult:
    """Result of a single push operation."""

    remote: str
    branch: str
    status: PushStatus
    message: str = ""
    duration: float = 0.0
    retries: int = 0
    commit_sha: str = ""
    vpn_used: str | None = None  # VPN name if VPN was used


@dataclass
class VpnResult:
    """Result of a VPN operation."""

    vpn_name: str
    connected: bool
    message: str = ""
    duration: float = 0.0


@dataclass
class HealthCheckResult:
    """Result of a health check for a remote."""

    remote: str
    status: RemoteStatus
    url: str = ""
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class SyncStatusResult:
    """Status of sync between local and remote branches."""

    remote: str
    branch: str
    state: SyncState
    local_commit: str = ""
    remote_commit: str = ""
    ahead_count: int = 0
    behind_count: int = 0


@dataclass
class QueuedPush:
    """A queued push operation for offline processing."""

    remote: str
    branch: str
    commit_sha: str
    queued_at: str
    retries: int = 0
    last_error: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "remote": self.remote,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "queued_at": self.queued_at,
            "retries": self.retries,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> QueuedPush:
        """Create from dictionary."""
        return cls(
            remote=data["remote"],
            branch=data["branch"],
            commit_sha=data["commit_sha"],
            queued_at=data["queued_at"],
            retries=data.get("retries", 0),
            last_error=data.get("last_error", ""),
        )


@dataclass
class OfflineQueue:
    """Queue of push operations to retry later."""

    items: list[QueuedPush] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict) -> OfflineQueue:
        """Create from dictionary."""
        return cls(
            items=[QueuedPush.from_dict(item) for item in data.get("items", [])],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class SyncResult:
    """Result of a sync operation."""

    push_results: list[PushResult] = field(default_factory=list)
    health_checks: list[HealthCheckResult] = field(default_factory=list)
    sync_statuses: list[SyncStatusResult] = field(default_factory=list)
    queued: list[QueuedPush] = field(default_factory=list)
    dry_run: bool = False

    @property
    def success_count(self) -> int:
        """Count of successful pushes."""
        return sum(1 for r in self.push_results if r.status == PushStatus.SUCCESS)

    @property
    def failed_count(self) -> int:
        """Count of failed pushes."""
        return sum(1 for r in self.push_results if r.status == PushStatus.FAILED)

    @property
    def all_succeeded(self) -> bool:
        """Check if all pushes succeeded."""
        return all(r.status == PushStatus.SUCCESS for r in self.push_results)


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


class Logger:
    """Logger with colored output and verbosity levels."""

    def __init__(self, verbose: bool = False, quiet: bool = False) -> None:
        self.verbose = verbose
        self.quiet = quiet
        if not sys.stdout.isatty():
            Colors.disable()

    def info(self, message: str) -> None:
        """Print info message."""
        if not self.quiet:
            print(f"{Colors.BLUE}ℹ{Colors.RESET} {message}")

    def success(self, message: str) -> None:
        """Print success message."""
        if not self.quiet:
            print(f"{Colors.GREEN}✓{Colors.RESET} {message}")

    def warning(self, message: str) -> None:
        """Print warning message."""
        if not self.quiet:
            print(f"{Colors.YELLOW}⚠{Colors.RESET} {message}")

    def error(self, message: str) -> None:
        """Print error message (always shown)."""
        print(f"{Colors.RED}✗{Colors.RESET} {message}", file=sys.stderr)

    def debug(self, message: str) -> None:
        """Print debug message (only in verbose mode)."""
        if self.verbose:
            print(f"{Colors.DIM}  {message}{Colors.RESET}")

    def header(self, message: str) -> None:
        """Print header message."""
        if not self.quiet:
            print(f"\n{Colors.BOLD}{Colors.CYAN}{message}{Colors.RESET}")

    def status_line(self, label: str, value: str, color: str = "") -> None:
        """Print a status line with label and value."""
        if not self.quiet:
            print(f"  {Colors.DIM}{label}:{Colors.RESET} {color}{value}{Colors.RESET}")


# Global logger instance
logger = Logger()


def run_git_command(
    args: list[str],
    timeout: int | None = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result
    except subprocess.TimeoutExpired as e:
        # Return a fake result for timeout
        return subprocess.CompletedProcess(
            cmd, 124, stdout="", stderr=f"Command timed out after {timeout}s"
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            cmd, 127, stdout="", stderr="git command not found"
        )


def get_current_branch() -> str | None:
    """Get the current git branch name."""
    result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"])
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def get_current_commit() -> str | None:
    """Get the current commit SHA."""
    result = run_git_command(["rev-parse", "HEAD"])
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def get_remote_commit(remote: str, branch: str) -> str | None:
    """Get the commit SHA of a remote branch."""
    result = run_git_command(["rev-parse", f"{remote}/{branch}"])
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def get_configured_remotes() -> list[str]:
    """Get list of configured git remotes."""
    result = run_git_command(["remote"])
    if result.returncode == 0:
        return [r.strip() for r in result.stdout.strip().split("\n") if r.strip()]
    return []


def get_remote_url(remote: str) -> str | None:
    """Get the URL of a remote."""
    result = run_git_command(["remote", "get-url", remote])
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def fetch_remote(remote: str, timeout: int = 30) -> bool:
    """Fetch from a remote."""
    result = run_git_command(["fetch", remote], timeout=timeout)
    return result.returncode == 0


def is_force_push_required(remote: str, branch: str) -> bool:
    """Check if a force push would be required."""
    # Get local and remote commits
    local_result = run_git_command(["rev-parse", branch])
    remote_result = run_git_command(["rev-parse", f"{remote}/{branch}"])

    if local_result.returncode != 0 or remote_result.returncode != 0:
        return False

    local_commit = local_result.stdout.strip()
    remote_commit = remote_result.stdout.strip()

    if local_commit == remote_commit:
        return False

    # Check if remote commit is an ancestor of local
    merge_base = run_git_command(["merge-base", local_commit, remote_commit])
    if merge_base.returncode != 0:
        return True

    # If merge base equals remote, it's a fast-forward
    return merge_base.stdout.strip() != remote_commit


def get_sync_state(remote: str, branch: str) -> tuple[SyncState, int, int]:
    """
    Get the sync state between local and remote branch.

    Returns (state, ahead_count, behind_count).
    """
    # First, try to get the comparison
    result = run_git_command(
        ["rev-list", "--left-right", "--count", f"{branch}...{remote}/{branch}"]
    )

    if result.returncode != 0:
        # Remote branch might not exist
        return SyncState.NO_REMOTE, 0, 0

    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return SyncState.UNKNOWN, 0, 0

    try:
        ahead = int(parts[0])
        behind = int(parts[1])
    except ValueError:
        return SyncState.UNKNOWN, 0, 0

    if ahead == 0 and behind == 0:
        return SyncState.IN_SYNC, 0, 0
    elif ahead > 0 and behind == 0:
        return SyncState.AHEAD, ahead, 0
    elif ahead == 0 and behind > 0:
        return SyncState.BEHIND, 0, behind
    else:
        return SyncState.DIVERGED, ahead, behind


def branch_matches_pattern(branch: str, patterns: list[str]) -> bool:
    """Check if a branch name matches any of the patterns."""
    for pattern in patterns:
        if pattern == "*":
            return True
        if fnmatch.fnmatch(branch, pattern):
            return True
    return False


def load_config_file(config_path: Path | None = None) -> ConfigDict:
    """Load configuration from file."""
    if config_path and config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load config file {config_path}: {e}")
            return {}

    # Search for default config files
    for name in CONFIG_FILE_NAMES:
        path = Path(name)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue

    return {}


def load_env_config() -> ConfigDict:
    """Load configuration from environment variables."""
    config: ConfigDict = {}

    parallel = os.environ.get("REMOTE_SYNC_PARALLEL", "").lower()
    if parallel == "false":
        config["parallel"] = False
    elif parallel == "true":
        config["parallel"] = True

    offline_queue = os.environ.get("REMOTE_SYNC_OFFLINE_QUEUE", "").lower()
    if offline_queue == "true":
        config["offline_queue"] = True
    elif offline_queue == "false":
        config["offline_queue"] = False

    max_workers = os.environ.get("REMOTE_SYNC_MAX_WORKERS")
    if max_workers:
        try:
            config["max_workers"] = int(max_workers)
        except ValueError:
            pass

    return config


def merge_configs(*configs: ConfigDict) -> ConfigDict:
    """Merge multiple configurations, later ones override earlier."""
    result: ConfigDict = {}
    for config in configs:
        for key, value in config.items():
            if key == "remotes" and "remotes" in result:
                # Deep merge remotes
                result["remotes"].update(value)  # type: ignore
            else:
                result[key] = value  # type: ignore
    return result


def discover_remotes(config: SyncConfig) -> SyncConfig:
    """Auto-discover remotes if none configured."""
    if config.remotes:
        return config

    # Get all configured git remotes
    git_remotes = get_configured_remotes()
    if not git_remotes:
        return config

    # Create default configuration for each remote
    for i, remote in enumerate(git_remotes):
        url = get_remote_url(remote)
        # Give origin highest priority
        priority = 1 if remote == "origin" else i + 2
        config.remotes[remote] = RemoteConfig(
            name=remote,
            priority=priority,
            branches=["*"],
            force_push=ForcePushPolicy.BLOCK,
            url=url,
        )

    return config


def load_queue(queue_path: Path | None = None) -> OfflineQueue:
    """Load the offline queue from disk."""
    path = queue_path or Path(QUEUE_FILE)
    if not path.exists():
        return OfflineQueue()

    try:
        data = json.loads(path.read_text())
        return OfflineQueue.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return OfflineQueue()


def save_queue(queue: OfflineQueue, queue_path: Path | None = None) -> bool:
    """Save the offline queue to disk."""
    path = queue_path or Path(QUEUE_FILE)
    queue.updated_at = datetime.now(timezone.utc).isoformat()
    if not queue.created_at:
        queue.created_at = queue.updated_at

    try:
        path.write_text(json.dumps(queue.to_dict(), indent=2))
        return True
    except OSError as e:
        logger.error(f"Failed to save queue: {e}")
        return False


def clear_queue(queue_path: Path | None = None) -> bool:
    """Clear the offline queue."""
    path = queue_path or Path(QUEUE_FILE)
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.error(f"Failed to clear queue: {e}")
            return False
    return True


def add_to_queue(
    remote: str,
    branch: str,
    commit_sha: str,
    error: str = "",
    queue_path: Path | None = None,
) -> None:
    """Add a failed push to the offline queue."""
    queue = load_queue(queue_path)

    # Check if already queued
    for item in queue.items:
        if item.remote == remote and item.branch == branch:
            item.commit_sha = commit_sha
            item.retries += 1
            item.last_error = error
            save_queue(queue, queue_path)
            return

    # Add new item
    queue.items.append(
        QueuedPush(
            remote=remote,
            branch=branch,
            commit_sha=commit_sha,
            queued_at=datetime.now(timezone.utc).isoformat(),
            last_error=error,
        )
    )
    save_queue(queue, queue_path)


def remove_from_queue(remote: str, branch: str, queue_path: Path | None = None) -> None:
    """Remove an item from the offline queue."""
    queue = load_queue(queue_path)
    queue.items = [
        item for item in queue.items if not (item.remote == remote and item.branch == branch)
    ]
    save_queue(queue, queue_path)


# Track active VPN connections for cleanup
_active_vpn_connections: dict[str, VpnConfig] = {}
_vpn_lock = threading.Lock()


def run_shell_command(
    command: str,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command and return the result."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command, 124, stdout="", stderr=f"Command timed out after {timeout}s"
        )
    except Exception as e:
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr=str(e)
        )


def is_vpn_connected(vpn_config: VpnConfig) -> bool:
    """Check if a VPN is currently connected."""
    if not vpn_config.check_cmd:
        # No check command - assume not connected
        return False

    result = run_shell_command(vpn_config.check_cmd, timeout=10)
    return result.returncode == 0


def connect_vpn(vpn_config: VpnConfig, dry_run: bool = False) -> VpnResult:
    """Connect to a VPN."""
    start_time = time.time()

    if not vpn_config.connect_cmd:
        return VpnResult(
            vpn_name=vpn_config.name,
            connected=False,
            message="No connect command configured",
        )

    # Check if already connected
    if vpn_config.check_cmd and is_vpn_connected(vpn_config):
        logger.debug(f"VPN '{vpn_config.name}' is already connected")
        return VpnResult(
            vpn_name=vpn_config.name,
            connected=True,
            message="Already connected",
        )

    if dry_run:
        return VpnResult(
            vpn_name=vpn_config.name,
            connected=True,
            message=f"[DRY RUN] Would connect VPN: {vpn_config.connect_cmd}",
        )

    logger.info(f"Connecting to VPN '{vpn_config.name}'...")
    result = run_shell_command(vpn_config.connect_cmd, timeout=vpn_config.timeout)
    duration = time.time() - start_time

    if result.returncode == 0:
        # Track this connection for cleanup
        with _vpn_lock:
            _active_vpn_connections[vpn_config.name] = vpn_config

        # Wait a moment for connection to stabilize
        time.sleep(1)

        # Verify connection if check command is available
        if vpn_config.check_cmd:
            if not is_vpn_connected(vpn_config):
                return VpnResult(
                    vpn_name=vpn_config.name,
                    connected=False,
                    message="VPN connect command succeeded but connection check failed",
                    duration=round(duration, 2),
                )

        logger.success(f"Connected to VPN '{vpn_config.name}' in {duration:.1f}s")
        return VpnResult(
            vpn_name=vpn_config.name,
            connected=True,
            message="Connected successfully",
            duration=round(duration, 2),
        )
    else:
        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        logger.error(f"Failed to connect VPN '{vpn_config.name}': {error_msg}")
        return VpnResult(
            vpn_name=vpn_config.name,
            connected=False,
            message=error_msg,
            duration=round(duration, 2),
        )


def disconnect_vpn(vpn_config: VpnConfig, dry_run: bool = False) -> VpnResult:
    """Disconnect from a VPN."""
    start_time = time.time()

    if not vpn_config.disconnect_cmd:
        return VpnResult(
            vpn_name=vpn_config.name,
            connected=True,  # Assume still connected since we can't disconnect
            message="No disconnect command configured",
        )

    if dry_run:
        return VpnResult(
            vpn_name=vpn_config.name,
            connected=False,
            message=f"[DRY RUN] Would disconnect VPN: {vpn_config.disconnect_cmd}",
        )

    logger.debug(f"Disconnecting from VPN '{vpn_config.name}'...")
    result = run_shell_command(vpn_config.disconnect_cmd, timeout=vpn_config.timeout)
    duration = time.time() - start_time

    # Remove from active connections
    with _vpn_lock:
        _active_vpn_connections.pop(vpn_config.name, None)

    if result.returncode == 0:
        logger.debug(f"Disconnected from VPN '{vpn_config.name}'")
        return VpnResult(
            vpn_name=vpn_config.name,
            connected=False,
            message="Disconnected successfully",
            duration=round(duration, 2),
        )
    else:
        error_msg = result.stderr.strip() or "Unknown error"
        return VpnResult(
            vpn_name=vpn_config.name,
            connected=True,  # Assume still connected on failure
            message=f"Disconnect failed: {error_msg}",
            duration=round(duration, 2),
        )


def disconnect_all_vpns(dry_run: bool = False) -> list[VpnResult]:
    """Disconnect all active VPN connections."""
    results = []
    with _vpn_lock:
        active = list(_active_vpn_connections.values())

    for vpn_config in active:
        results.append(disconnect_vpn(vpn_config, dry_run))

    return results


def get_vpn_for_remote(remote_config: RemoteConfig, config: SyncConfig) -> VpnConfig | None:
    """Get the VPN configuration for a remote, if any."""
    if not remote_config.vpn:
        return None

    return config.vpn_configs.get(remote_config.vpn)


class VpnContext:
    """Context manager for VPN connections with automatic cleanup."""

    def __init__(
        self,
        vpn_config: VpnConfig | None,
        dry_run: bool = False,
        auto_connect: bool = True,
    ):
        self.vpn_config = vpn_config
        self.dry_run = dry_run
        self.auto_connect = auto_connect
        self.connected = False
        self.was_already_connected = False

    def __enter__(self) -> VpnContext:
        if not self.vpn_config or not self.auto_connect:
            return self

        # Check if already connected
        if self.vpn_config.check_cmd and is_vpn_connected(self.vpn_config):
            self.was_already_connected = True
            self.connected = True
            return self

        result = connect_vpn(self.vpn_config, self.dry_run)
        self.connected = result.connected
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Only disconnect if we connected (not if it was already connected)
        if self.vpn_config and self.connected and not self.was_already_connected:
            disconnect_vpn(self.vpn_config, self.dry_run)
        return None


def check_remote_health(
    remote: str,
    timeout: int = 5,
) -> HealthCheckResult:
    """Check if a remote is reachable."""
    url = get_remote_url(remote) or ""
    start_time = time.time()

    # Use ls-remote to check connectivity
    result = run_git_command(["ls-remote", "--heads", remote], timeout=timeout)
    latency = (time.time() - start_time) * 1000  # Convert to ms

    if result.returncode == 0:
        return HealthCheckResult(
            remote=remote,
            status=RemoteStatus.REACHABLE,
            url=url,
            latency_ms=round(latency, 2),
        )
    else:
        return HealthCheckResult(
            remote=remote,
            status=RemoteStatus.UNREACHABLE,
            url=url,
            latency_ms=round(latency, 2),
            error=result.stderr.strip(),
        )


def check_all_remotes_health(
    config: SyncConfig,
) -> list[HealthCheckResult]:
    """Check health of all configured remotes."""
    results: list[HealthCheckResult] = []

    if config.parallel and len(config.remotes) > 1:
        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            futures = {
                executor.submit(
                    check_remote_health, remote, config.health_check_timeout
                ): remote
                for remote in config.remotes
            }
            for future in as_completed(futures):
                results.append(future.result())
    else:
        for remote in config.remotes:
            results.append(check_remote_health(remote, config.health_check_timeout))

    return results


def push_to_remote(
    remote: str,
    branch: str,
    remote_config: RemoteConfig,
    force: bool = False,
    dry_run: bool = False,
    vpn_config: VpnConfig | None = None,
) -> PushResult:
    """Push a branch to a remote with retry logic and optional VPN support."""
    start_time = time.time()
    commit_sha = get_current_commit() or ""
    retries = 0
    last_error = ""
    vpn_used = None

    # Check force push policy
    if not dry_run and is_force_push_required(remote, branch):
        if remote_config.force_push == ForcePushPolicy.BLOCK and not force:
            return PushResult(
                remote=remote,
                branch=branch,
                status=PushStatus.BLOCKED,
                message=f"Force push blocked by policy for {remote}",
                commit_sha=commit_sha,
            )
        elif remote_config.force_push == ForcePushPolicy.WARN and not force:
            logger.warning(f"Force push required for {remote}/{branch}")

    # Build push command
    push_args = ["push", remote, branch]
    if force:
        push_args.insert(1, "--force-with-lease")

    if dry_run:
        vpn_msg = f" (via VPN '{vpn_config.name}')" if vpn_config else ""
        return PushResult(
            remote=remote,
            branch=branch,
            status=PushStatus.SUCCESS,
            message=f"[DRY RUN] Would push {branch} to {remote}{vpn_msg}",
            duration=0,
            commit_sha=commit_sha,
            vpn_used=vpn_config.name if vpn_config else None,
        )

    # Retry loop with exponential backoff
    base_delay = 1.0
    max_delay = 30.0

    # First, try without VPN if auto_connect is enabled
    should_try_vpn = vpn_config is not None and vpn_config.auto_connect
    tried_without_vpn = False

    for attempt in range(remote_config.retry + 1):
        # On first attempt, try without VPN to see if remote is reachable
        if attempt == 0 and should_try_vpn and not tried_without_vpn:
            # Quick health check without VPN
            health = check_remote_health(remote, timeout=5)
            if health.status == RemoteStatus.REACHABLE:
                # Remote is reachable without VPN, proceed normally
                should_try_vpn = False
            tried_without_vpn = True

        # Connect VPN if needed
        if should_try_vpn and vpn_config:
            vpn_result = connect_vpn(vpn_config, dry_run=False)
            if vpn_result.connected:
                vpn_used = vpn_config.name
            else:
                logger.warning(f"VPN connection failed, trying without VPN")
                should_try_vpn = False

        result = run_git_command(push_args, timeout=remote_config.timeout)

        if result.returncode == 0:
            duration = time.time() - start_time
            return PushResult(
                remote=remote,
                branch=branch,
                status=PushStatus.SUCCESS,
                message=f"Successfully pushed {branch} to {remote}",
                duration=round(duration, 2),
                retries=retries,
                commit_sha=commit_sha,
                vpn_used=vpn_used,
            )

        last_error = result.stderr.strip()
        retries += 1

        # If push failed and we haven't tried VPN yet, try connecting
        if not vpn_used and vpn_config and not should_try_vpn:
            logger.debug(f"Push failed, attempting with VPN '{vpn_config.name}'...")
            should_try_vpn = True

        if attempt < remote_config.retry:
            # Exponential backoff with jitter
            delay = min(base_delay * (2**attempt) + random.uniform(0, 1), max_delay)
            logger.debug(f"Push to {remote} failed, retrying in {delay:.1f}s...")
            time.sleep(delay)

    duration = time.time() - start_time
    return PushResult(
        remote=remote,
        branch=branch,
        status=PushStatus.FAILED,
        message=last_error,
        duration=round(duration, 2),
        retries=retries,
        commit_sha=commit_sha,
        vpn_used=vpn_used,
    )


def get_sync_status(
    remote: str,
    branch: str,
) -> SyncStatusResult:
    """Get sync status for a remote/branch pair."""
    local_commit = get_current_commit() or ""
    remote_commit = get_remote_commit(remote, branch) or ""
    state, ahead, behind = get_sync_state(remote, branch)

    return SyncStatusResult(
        remote=remote,
        branch=branch,
        state=state,
        local_commit=local_commit[:8] if local_commit else "",
        remote_commit=remote_commit[:8] if remote_commit else "",
        ahead_count=ahead,
        behind_count=behind,
    )


def sync_to_remotes(
    config: SyncConfig,
    branch: str | None = None,
    remotes: list[str] | None = None,
    force: bool = False,
) -> SyncResult:
    """Sync (push) to configured remotes."""
    result = SyncResult(dry_run=config.dry_run)

    # Get current branch if not specified
    if branch is None:
        branch = get_current_branch()
        if not branch:
            logger.error("Could not determine current branch")
            return result

    # Filter remotes
    target_remotes = remotes or list(config.remotes.keys())
    if not target_remotes:
        logger.warning("No remotes configured")
        return result

    # Sort by priority
    sorted_remotes = sorted(
        [(name, config.remotes.get(name, RemoteConfig(name=name))) for name in target_remotes],
        key=lambda x: x[1].priority,
    )

    # Filter remotes that match the branch pattern
    matching_remotes = [
        (name, cfg)
        for name, cfg in sorted_remotes
        if branch_matches_pattern(branch, cfg.branches)
    ]

    if not matching_remotes:
        logger.info(f"No remotes configured for branch '{branch}'")
        return result

    # Auto-fetch if enabled
    if config.auto_fetch and not config.dry_run:
        logger.debug("Fetching from remotes...")
        for name, _ in matching_remotes:
            fetch_remote(name)

    # Push to remotes
    # Note: VPN connections are NOT parallelized to avoid conflicts
    # Remotes requiring VPN are pushed sequentially, others can be parallel
    vpn_remotes = [(n, c) for n, c in matching_remotes if c.vpn and c.vpn in config.vpn_configs]
    non_vpn_remotes = [(n, c) for n, c in matching_remotes if not c.vpn or c.vpn not in config.vpn_configs]

    # Push non-VPN remotes in parallel
    if config.parallel and len(non_vpn_remotes) > 1:
        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            futures = {
                executor.submit(
                    push_to_remote, name, branch, cfg, force, config.dry_run, None
                ): name
                for name, cfg in non_vpn_remotes
            }
            for future in as_completed(futures):
                push_result = future.result()
                result.push_results.append(push_result)

                # Queue failed pushes if offline queue is enabled
                if push_result.status == PushStatus.FAILED and config.offline_queue:
                    add_to_queue(
                        push_result.remote,
                        push_result.branch,
                        push_result.commit_sha,
                        push_result.message,
                    )
                    result.queued.append(
                        QueuedPush(
                            remote=push_result.remote,
                            branch=push_result.branch,
                            commit_sha=push_result.commit_sha,
                            queued_at=datetime.now(timezone.utc).isoformat(),
                            last_error=push_result.message,
                        )
                    )
    else:
        for name, cfg in non_vpn_remotes:
            push_result = push_to_remote(name, branch, cfg, force, config.dry_run, None)
            result.push_results.append(push_result)

            if push_result.status == PushStatus.FAILED and config.offline_queue:
                add_to_queue(
                    push_result.remote,
                    push_result.branch,
                    push_result.commit_sha,
                    push_result.message,
                )
                result.queued.append(
                    QueuedPush(
                        remote=push_result.remote,
                        branch=push_result.branch,
                        commit_sha=push_result.commit_sha,
                        queued_at=datetime.now(timezone.utc).isoformat(),
                        last_error=push_result.message,
                    )
                )

    # Push VPN remotes sequentially (VPN connections can conflict if parallelized)
    for name, cfg in vpn_remotes:
            vpn_cfg = get_vpn_for_remote(cfg, config)
            push_result = push_to_remote(name, branch, cfg, force, config.dry_run, vpn_cfg)
            result.push_results.append(push_result)

            # Queue failed pushes
            if push_result.status == PushStatus.FAILED and config.offline_queue:
                add_to_queue(
                    push_result.remote,
                    push_result.branch,
                    push_result.commit_sha,
                    push_result.message,
                )
                result.queued.append(
                    QueuedPush(
                        remote=push_result.remote,
                        branch=push_result.branch,
                        commit_sha=push_result.commit_sha,
                        queued_at=datetime.now(timezone.utc).isoformat(),
                        last_error=push_result.message,
                    )
                )

    # Disconnect any VPNs that were connected
    disconnect_all_vpns(config.dry_run)

    return result


def process_queue(config: SyncConfig, force: bool = False) -> SyncResult:
    """Process the offline queue of failed pushes."""
    result = SyncResult(dry_run=config.dry_run)
    queue = load_queue()

    if not queue.items:
        logger.info("Offline queue is empty")
        return result

    logger.info(f"Processing {len(queue.items)} queued push(es)...")

    for item in list(queue.items):  # Copy list to allow modification
        remote_config = config.remotes.get(item.remote, RemoteConfig(name=item.remote))
        vpn_cfg = get_vpn_for_remote(remote_config, config)
        push_result = push_to_remote(
            item.remote,
            item.branch,
            remote_config,
            force,
            config.dry_run,
            vpn_cfg,
        )
        result.push_results.append(push_result)

        if push_result.status == PushStatus.SUCCESS:
            remove_from_queue(item.remote, item.branch)
        else:
            # Update queue item with new error
            add_to_queue(
                item.remote,
                item.branch,
                item.commit_sha,
                push_result.message,
            )

    # Disconnect any VPNs that were connected
    disconnect_all_vpns(config.dry_run)

    return result


def get_all_sync_statuses(config: SyncConfig, branch: str | None = None) -> list[SyncStatusResult]:
    """Get sync status for all remotes."""
    if branch is None:
        branch = get_current_branch()
        if not branch:
            return []

    results: list[SyncStatusResult] = []

    # Fetch first if enabled
    if config.auto_fetch:
        for remote in config.remotes:
            fetch_remote(remote)

    for remote in config.remotes:
        results.append(get_sync_status(remote, branch))

    return results


def print_health_check_results(results: list[HealthCheckResult]) -> None:
    """Print health check results in a nice format."""
    logger.header("Remote Health Check")

    for result in results:
        if result.status == RemoteStatus.REACHABLE:
            status_color = Colors.GREEN
            status_icon = "✓"
            status_text = f"reachable ({result.latency_ms:.0f}ms)"
        else:
            status_color = Colors.RED
            status_icon = "✗"
            status_text = f"unreachable"

        print(f"  {status_color}{status_icon}{Colors.RESET} {Colors.BOLD}{result.remote}{Colors.RESET}")
        print(f"    URL: {Colors.DIM}{result.url}{Colors.RESET}")
        print(f"    Status: {status_color}{status_text}{Colors.RESET}")
        if result.error:
            print(f"    Error: {Colors.RED}{result.error}{Colors.RESET}")


def print_sync_status_dashboard(
    statuses: list[SyncStatusResult],
    config: SyncConfig,
) -> None:
    """Print sync status dashboard."""
    logger.header("Sync Status Dashboard")

    branch = statuses[0].branch if statuses else get_current_branch() or "unknown"
    print(f"  Branch: {Colors.CYAN}{branch}{Colors.RESET}")
    print()

    # Sort by priority
    sorted_statuses = sorted(
        statuses,
        key=lambda s: config.remotes.get(s.remote, RemoteConfig(name=s.remote)).priority,
    )

    for status in sorted_statuses:
        remote_config = config.remotes.get(status.remote, RemoteConfig(name=status.remote))

        # Determine color and icon based on state
        if status.state == SyncState.IN_SYNC:
            icon = "✓"
            color = Colors.GREEN
            state_text = "in sync"
        elif status.state == SyncState.AHEAD:
            icon = "↑"
            color = Colors.YELLOW
            state_text = f"ahead by {status.ahead_count} commit(s)"
        elif status.state == SyncState.BEHIND:
            icon = "↓"
            color = Colors.YELLOW
            state_text = f"behind by {status.behind_count} commit(s)"
        elif status.state == SyncState.DIVERGED:
            icon = "⚠"
            color = Colors.RED
            state_text = f"diverged (+{status.ahead_count}/-{status.behind_count})"
        elif status.state == SyncState.NO_REMOTE:
            icon = "○"
            color = Colors.DIM
            state_text = "no remote branch"
        else:
            icon = "?"
            color = Colors.DIM
            state_text = "unknown"

        # Print remote status
        print(f"  {color}{icon}{Colors.RESET} {Colors.BOLD}{status.remote}{Colors.RESET} ", end="")
        print(f"{Colors.DIM}(priority: {remote_config.priority}){Colors.RESET}")
        print(f"    State: {color}{state_text}{Colors.RESET}")
        if status.local_commit:
            print(f"    Local:  {Colors.DIM}{status.local_commit}{Colors.RESET}")
        if status.remote_commit:
            print(f"    Remote: {Colors.DIM}{status.remote_commit}{Colors.RESET}")
        print()


def print_push_results(result: SyncResult) -> None:
    """Print push results summary."""
    if result.dry_run:
        logger.header("[DRY RUN] Push Results")
    else:
        logger.header("Push Results")

    for push in result.push_results:
        if push.status == PushStatus.SUCCESS:
            icon = "✓"
            color = Colors.GREEN
        elif push.status == PushStatus.BLOCKED:
            icon = "⊘"
            color = Colors.YELLOW
        elif push.status == PushStatus.QUEUED:
            icon = "⏳"
            color = Colors.BLUE
        else:
            icon = "✗"
            color = Colors.RED

        print(f"  {color}{icon}{Colors.RESET} {Colors.BOLD}{push.remote}/{push.branch}{Colors.RESET}")
        print(f"    {push.message}")
        if push.duration > 0:
            print(f"    {Colors.DIM}Duration: {push.duration:.2f}s{Colors.RESET}")
        if push.retries > 0:
            print(f"    {Colors.DIM}Retries: {push.retries}{Colors.RESET}")
        if push.vpn_used:
            print(f"    {Colors.MAGENTA}🔒 VPN: {push.vpn_used}{Colors.RESET}")
        print()

    # Summary
    print(f"  {Colors.BOLD}Summary:{Colors.RESET} ", end="")
    print(f"{Colors.GREEN}{result.success_count} succeeded{Colors.RESET}, ", end="")
    print(f"{Colors.RED}{result.failed_count} failed{Colors.RESET}")

    if result.queued:
        print(f"  {Colors.BLUE}{len(result.queued)} push(es) added to offline queue{Colors.RESET}")


def print_queue_status(queue: OfflineQueue) -> None:
    """Print offline queue status."""
    logger.header("Offline Queue")

    if not queue.items:
        print(f"  {Colors.DIM}Queue is empty{Colors.RESET}")
        return

    print(f"  {Colors.BOLD}{len(queue.items)}{Colors.RESET} item(s) in queue")
    print()

    for item in queue.items:
        print(f"  • {Colors.CYAN}{item.remote}/{item.branch}{Colors.RESET}")
        print(f"    Commit: {Colors.DIM}{item.commit_sha[:8]}{Colors.RESET}")
        print(f"    Queued: {Colors.DIM}{item.queued_at}{Colors.RESET}")
        if item.retries > 0:
            print(f"    Retries: {Colors.YELLOW}{item.retries}{Colors.RESET}")
        if item.last_error:
            print(f"    Error: {Colors.RED}{item.last_error[:60]}...{Colors.RESET}")
        print()


def create_argument_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="remote-sync",
        description="Keep multiple git remotes in sync automatically.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  remote-sync --push                 Push current branch to all remotes
  remote-sync --push --remote origin Push current branch to origin only
  remote-sync --status               Show sync status dashboard
  remote-sync --health-check         Check connectivity to all remotes
  remote-sync --process-queue        Retry failed pushes from queue
  remote-sync --push --dry-run       Preview what would be pushed

Configuration file (.remotesyncrc.json):
  {
    "remotes": {
      "origin": {"priority": 1, "branches": ["*"], "force_push": "block"},
      "mirror": {"priority": 2, "branches": ["main"], "force_push": "warn"}
    },
    "parallel": true,
    "offline_queue": true
  }
        """,
    )

    # Actions
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--push",
        action="store_true",
        help="Push current branch to all configured remotes",
    )
    action_group.add_argument(
        "--push-all",
        action="store_true",
        help="Push all branches to their configured remotes",
    )
    action_group.add_argument(
        "--status",
        action="store_true",
        help="Show sync status dashboard",
    )
    action_group.add_argument(
        "--health-check",
        action="store_true",
        help="Check connectivity to all remotes",
    )
    action_group.add_argument(
        "--process-queue",
        action="store_true",
        help="Process offline queue of failed pushes",
    )
    action_group.add_argument(
        "--clear-queue",
        action="store_true",
        help="Clear the offline queue",
    )
    action_group.add_argument(
        "--show-queue",
        action="store_true",
        help="Show offline queue contents",
    )

    # Options
    parser.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help="Path to configuration file",
    )
    parser.add_argument(
        "--remote",
        type=str,
        metavar="NAME",
        help="Target specific remote(s), comma-separated",
    )
    parser.add_argument(
        "--branch",
        type=str,
        metavar="NAME",
        help="Target specific branch (default: current branch)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow force push (requires explicit flag)",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Disable parallel pushing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without executing",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress all output except errors",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI."""
    global logger

    parser = create_argument_parser()
    args = parser.parse_args(argv)

    # Initialize logger
    logger = Logger(verbose=args.verbose, quiet=args.quiet)

    # Load configuration
    file_config = load_config_file(args.config)
    env_config = load_env_config()
    merged_config = merge_configs(file_config, env_config)

    config = SyncConfig.from_dict(merged_config)
    config.dry_run = args.dry_run or os.environ.get("REMOTE_SYNC_DRY_RUN", "").lower() == "true"
    config.verbose = args.verbose or os.environ.get("REMOTE_SYNC_VERBOSE", "").lower() == "true"
    config.quiet = args.quiet

    if args.no_parallel:
        config.parallel = False

    # Auto-discover remotes if none configured
    config = discover_remotes(config)

    if not config.remotes:
        logger.error("No git remotes found. Add a remote with 'git remote add <name> <url>'")
        return 1

    # Parse target remotes
    target_remotes = None
    if args.remote:
        target_remotes = [r.strip() for r in args.remote.split(",")]
        # Validate remotes exist
        for remote in target_remotes:
            if remote not in config.remotes:
                logger.error(f"Remote '{remote}' not found in configuration")
                return 1

    # Execute requested action
    if args.health_check:
        results = check_all_remotes_health(config)
        print_health_check_results(results)
        # Return error if any remote is unreachable
        unreachable = [r for r in results if r.status == RemoteStatus.UNREACHABLE]
        return 1 if unreachable else 0

    elif args.status:
        statuses = get_all_sync_statuses(config, args.branch)
        if not statuses:
            logger.error("Could not determine sync status")
            return 1
        print_sync_status_dashboard(statuses, config)

        # Check for diverged remotes
        diverged = [s for s in statuses if s.state == SyncState.DIVERGED]
        if diverged:
            logger.warning(f"{len(diverged)} remote(s) have diverged!")
            return 1
        return 0

    elif args.push or args.push_all:
        result = sync_to_remotes(config, args.branch, target_remotes, args.force)
        print_push_results(result)
        return 0 if result.all_succeeded else 1

    elif args.process_queue:
        result = process_queue(config, args.force)
        if result.push_results:
            print_push_results(result)
        return 0 if result.all_succeeded else 1

    elif args.clear_queue:
        if clear_queue():
            logger.success("Offline queue cleared")
            return 0
        return 1

    elif args.show_queue:
        queue = load_queue()
        print_queue_status(queue)
        return 0

    else:
        # Default: show status
        statuses = get_all_sync_statuses(config, args.branch)
        if statuses:
            print_sync_status_dashboard(statuses, config)
        else:
            parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
