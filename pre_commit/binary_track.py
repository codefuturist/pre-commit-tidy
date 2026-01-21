"""Binary Track - Keep locally-installed binaries up to date with source code.

Tracks binaries developers build from their own source code and detects when
they become stale (out of sync with source changes). Provides rebuild triggers,
watch mode, and pre-commit integration.

Usage:
    binary-track [options]

Options:
    --config PATH       Path to configuration file (default: .binariesrc.yaml)
    --status            Show status of all tracked binaries
    --check             Check for stale binaries (exit 1 if any stale)
    --rebuild           Rebuild all stale binaries
    --rebuild-all       Rebuild all tracked binaries
    --watch             Watch source files and rebuild on change
    --add               Interactive add a new binary to track
    --remove NAME       Remove a binary from tracking
    --verify            Verify binaries exist and are executable
    --health            Check binary health (exists, executable, in PATH)
    --codesign          Sign all binaries (or re-sign after rebuild)
    --verify-signature  Verify codesigning status of all binaries
    --dry-run           Preview changes without executing
    --verbose           Show detailed output
    --quiet             Suppress all output except errors
    --json              Output in JSON format
    --help              Show this help message
    --version           Show version number

Configuration:
    Create a .binariesrc.yaml file in your project root:

    {
        "binaries": {
            "mytool": {
                "source_patterns": ["cmd/mytool/**/*.go", "internal/**/*.go"],
                "build_cmd": "go build -o ~/.local/bin/mytool ./cmd/mytool",
                "install_path": "~/.local/bin/mytool",
                "binary_type": "cli",
                "install_scope": "user",
                "language": "go",
                "rebuild_on_commit": true,
                "check_in_path": true
            },
            "myapp": {
                "source_patterns": ["src/**/*.swift"],
                "build_cmd": "xcodebuild -scheme MyApp -configuration Release",
                "install_path": "~/Applications/MyApp.app",
                "binary_type": "gui",
                "install_scope": "user",
                "language": "swift",
                "codesign": {
                    "enabled": true,
                    "identity": "Developer ID Application: Your Name"
                }
            }
        },
        "codesign": {
            "enabled": false,
            "identity": "-",
            "entitlements": null,
            "options": ["runtime"],
            "force": true
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

    Binary Type & Install Scope:
    - "binary_type": "cli" (command-line tool) or "gui" (graphical application)
    - "install_scope": "user" (local to user) or "system" (system-wide)

    Default Install Locations (auto-detected based on binary_type and install_scope):

    macOS:
      - CLI/user:   ~/.local/bin (recommended)
      - CLI/system: /usr/local/bin
      - GUI/user:   ~/Applications
      - GUI/system: /Applications

    Linux:
      - CLI/user:   ~/.local/bin (XDG standard)
      - CLI/system: /usr/local/bin
      - GUI/user:   ~/.local/opt
      - GUI/system: /opt

    Windows:
      - CLI/user:   %LOCALAPPDATA%\\Programs
      - CLI/system: %ProgramFiles%
      - GUI/user:   %LOCALAPPDATA%\\Programs
      - GUI/system: %ProgramFiles%

    Pre-commit Policies:
    - "warn": Print warning but allow commit
    - "block": Prevent commit if binaries are stale
    - "ignore": Don't check during pre-commit

    Codesigning (macOS):
    - "enabled": Whether to sign binaries after build (default: false)
    - "identity": Signing identity ("-" for ad-hoc, or certificate name)
    - "entitlements": Path to entitlements plist file (optional)
    - "options": codesign options like ["runtime"] for hardened runtime
    - "force": Replace existing signatures (default: true)

    Executable Permissions:
    - "ensure_executable": Automatically set executable permissions after build
      (default: true for CLI binaries, skipped for .app bundles)

    Test Commands:
    - "test_cmd": Command to run after successful build to verify the binary
    - "test_timeout": Timeout for test command in seconds (default: 60)
    - Tests run after build succeeds; test failure marks build as TEST_FAILED

    Retry & Recovery:
    - "retry_count": Number of times to retry failed builds (default: 0)
    - "retry_delay_seconds": Delay between retries (default: 1.0)
    - Failed builds are recorded in manifest for tracking failure history

    Service Management:
    - For binaries running as system services (daemons, background agents)
    - Automatically stops service before rebuild and restarts after
    - Supports launchd (macOS), systemd (Linux), and custom commands

    Service Configuration:
    {
        "service": {
            "enabled": true,
            "type": "launchd",           // "launchd", "systemd", or "custom"
            "name": "com.example.mydaemon",  // Service identifier
            "restart_after_build": true,  // Auto-restart after successful build
            "stop_timeout_seconds": 30,   // Wait time for graceful shutdown
            "start_timeout_seconds": 10,  // Wait time for service to start
            "stop_cmd": null,             // Custom stop command (for type: custom)
            "start_cmd": null,            // Custom start command (for type: custom)
            "status_cmd": null            // Custom status command (for type: custom)
        }
    }

    Custom Binary Paths:
    - Override paths to git and codesign binaries
    - Useful when binaries are in non-standard locations
    - Configure in "system_binaries" section:
    {
        "system_binaries": {
            "git": "/usr/local/bin/git",
            "codesign": "/usr/bin/codesign"
        }
    }

Environment Variables:
    BINARY_TRACK_DRY_RUN       Set to 'true' for dry run
    BINARY_TRACK_VERBOSE       Set to 'true' for verbose output
    BINARY_TRACK_AUTO_REBUILD  Set to 'true' to auto-rebuild stale binaries
    BINARY_TRACK_POLICY        Pre-commit policy (warn/block/ignore)
    BINARY_TRACK_CODESIGN      Set to 'true' to enable codesigning
    BINARY_TRACK_CODESIGN_ID   Codesigning identity (default: "-" for ad-hoc)
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TypedDict

import yaml

# Version
__version__ = "1.0.0"

# Default configuration file names
CONFIG_FILE_NAMES = [".binariesrc.yaml", ".binariesrc.yml", "binaries.config.yaml"]

# Manifest file for tracking build state
BUILD_MANIFEST_FILE = ".binary-track-manifest.json"

# Pre-commit config file
PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"


class Platform(Enum):
    """Supported operating system platforms."""

    MACOS = "macos"
    LINUX = "linux"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class BinaryType(Enum):
    """Type of binary application."""

    CLI = "cli"  # Command-line tool
    GUI = "gui"  # Graphical application


class InstallScope(Enum):
    """Installation scope for binaries."""

    USER = "user"      # User-local installation
    SYSTEM = "system"  # System-wide installation


@dataclass
class InstallLocation:
    """Platform-specific installation location details."""

    path: Path
    scope: InstallScope
    binary_type: BinaryType
    platform: Platform
    description: str = ""
    requires_admin: bool = False

    def exists(self) -> bool:
        """Check if the install location directory exists."""
        return self.path.exists()

    def is_writable(self) -> bool:
        """Check if the install location is writable by current user."""
        if not self.path.exists():
            # Check if parent is writable
            parent = self.path.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            return os.access(parent, os.W_OK)
        return os.access(self.path, os.W_OK)


def get_current_platform() -> Platform:
    """Detect the current operating system platform."""
    system = platform.system().lower()
    if system == "darwin":
        return Platform.MACOS
    elif system == "linux":
        return Platform.LINUX
    elif system == "windows":
        return Platform.WINDOWS
    return Platform.UNKNOWN


def get_default_install_locations(
    binary_type: BinaryType | None = None,
    scope: InstallScope | None = None,
    target_platform: Platform | None = None,
) -> list[InstallLocation]:
    """Get default installation locations for the given criteria.

    Args:
        binary_type: Filter by CLI or GUI applications
        scope: Filter by user or system installation
        target_platform: Target platform (defaults to current platform)

    Returns:
        List of matching InstallLocation objects, ordered by preference
    """
    if target_platform is None:
        target_platform = get_current_platform()

    locations: list[InstallLocation] = []

    if target_platform == Platform.MACOS:
        locations = [
            # User CLI tools
            InstallLocation(
                path=Path.home() / ".local" / "bin",
                scope=InstallScope.USER,
                binary_type=BinaryType.CLI,
                platform=Platform.MACOS,
                description="User-local CLI tools (recommended)",
            ),
            # User GUI apps
            InstallLocation(
                path=Path.home() / "Applications",
                scope=InstallScope.USER,
                binary_type=BinaryType.GUI,
                platform=Platform.MACOS,
                description="User-local GUI applications",
            ),
            # System CLI tools
            InstallLocation(
                path=Path("/usr/local/bin"),
                scope=InstallScope.SYSTEM,
                binary_type=BinaryType.CLI,
                platform=Platform.MACOS,
                description="System-wide CLI tools (Homebrew default)",
                requires_admin=True,
            ),
            # System GUI apps
            InstallLocation(
                path=Path("/Applications"),
                scope=InstallScope.SYSTEM,
                binary_type=BinaryType.GUI,
                platform=Platform.MACOS,
                description="System-wide GUI applications",
                requires_admin=True,
            ),
            # Alternative user CLI location
            InstallLocation(
                path=Path.home() / "bin",
                scope=InstallScope.USER,
                binary_type=BinaryType.CLI,
                platform=Platform.MACOS,
                description="User bin directory (legacy)",
            ),
        ]

    elif target_platform == Platform.LINUX:
        locations = [
            # User CLI tools (XDG standard)
            InstallLocation(
                path=Path.home() / ".local" / "bin",
                scope=InstallScope.USER,
                binary_type=BinaryType.CLI,
                platform=Platform.LINUX,
                description="User-local CLI tools (XDG standard)",
            ),
            # User GUI apps (application launchers)
            InstallLocation(
                path=Path.home() / ".local" / "share" / "applications",
                scope=InstallScope.USER,
                binary_type=BinaryType.GUI,
                platform=Platform.LINUX,
                description="User-local application launchers (.desktop files)",
            ),
            # User GUI app binaries
            InstallLocation(
                path=Path.home() / ".local" / "opt",
                scope=InstallScope.USER,
                binary_type=BinaryType.GUI,
                platform=Platform.LINUX,
                description="User-local GUI application binaries",
            ),
            # System CLI tools (local)
            InstallLocation(
                path=Path("/usr/local/bin"),
                scope=InstallScope.SYSTEM,
                binary_type=BinaryType.CLI,
                platform=Platform.LINUX,
                description="System-wide CLI tools (local)",
                requires_admin=True,
            ),
            # System CLI tools (distro)
            InstallLocation(
                path=Path("/usr/bin"),
                scope=InstallScope.SYSTEM,
                binary_type=BinaryType.CLI,
                platform=Platform.LINUX,
                description="System-wide CLI tools (distro-managed)",
                requires_admin=True,
            ),
            # System GUI apps
            InstallLocation(
                path=Path("/opt"),
                scope=InstallScope.SYSTEM,
                binary_type=BinaryType.GUI,
                platform=Platform.LINUX,
                description="System-wide optional applications",
                requires_admin=True,
            ),
            # System GUI app launchers
            InstallLocation(
                path=Path("/usr/share/applications"),
                scope=InstallScope.SYSTEM,
                binary_type=BinaryType.GUI,
                platform=Platform.LINUX,
                description="System-wide application launchers",
                requires_admin=True,
            ),
            # Alternative user CLI location
            InstallLocation(
                path=Path.home() / "bin",
                scope=InstallScope.USER,
                binary_type=BinaryType.CLI,
                platform=Platform.LINUX,
                description="User bin directory (legacy)",
            ),
        ]

    elif target_platform == Platform.WINDOWS:
        # Get Windows environment paths
        localappdata = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        programfiles = os.environ.get("ProgramFiles", "C:\\Program Files")
        programfiles_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")

        locations = [
            # User CLI tools
            InstallLocation(
                path=Path(localappdata) / "Programs",
                scope=InstallScope.USER,
                binary_type=BinaryType.CLI,
                platform=Platform.WINDOWS,
                description="User-local programs",
            ),
            # User CLI tools (alternative)
            InstallLocation(
                path=Path(localappdata) / "Microsoft" / "WindowsApps",
                scope=InstallScope.USER,
                binary_type=BinaryType.CLI,
                platform=Platform.WINDOWS,
                description="Windows Apps (user)",
            ),
            # User GUI apps
            InstallLocation(
                path=Path(localappdata) / "Programs",
                scope=InstallScope.USER,
                binary_type=BinaryType.GUI,
                platform=Platform.WINDOWS,
                description="User-local GUI applications",
            ),
            # User apps (Roaming)
            InstallLocation(
                path=Path(appdata),
                scope=InstallScope.USER,
                binary_type=BinaryType.GUI,
                platform=Platform.WINDOWS,
                description="User roaming applications",
            ),
            # System CLI/GUI apps (64-bit)
            InstallLocation(
                path=Path(programfiles),
                scope=InstallScope.SYSTEM,
                binary_type=BinaryType.CLI,
                platform=Platform.WINDOWS,
                description="System-wide programs (64-bit)",
                requires_admin=True,
            ),
            InstallLocation(
                path=Path(programfiles),
                scope=InstallScope.SYSTEM,
                binary_type=BinaryType.GUI,
                platform=Platform.WINDOWS,
                description="System-wide programs (64-bit)",
                requires_admin=True,
            ),
            # System apps (32-bit)
            InstallLocation(
                path=Path(programfiles_x86),
                scope=InstallScope.SYSTEM,
                binary_type=BinaryType.GUI,
                platform=Platform.WINDOWS,
                description="System-wide programs (32-bit)",
                requires_admin=True,
            ),
            # User bin directory (custom convention)
            InstallLocation(
                path=Path.home() / ".local" / "bin",
                scope=InstallScope.USER,
                binary_type=BinaryType.CLI,
                platform=Platform.WINDOWS,
                description="User-local CLI tools (Unix-style)",
            ),
        ]

    # Filter by criteria
    if binary_type is not None:
        locations = [loc for loc in locations if loc.binary_type == binary_type]
    if scope is not None:
        locations = [loc for loc in locations if loc.scope == scope]

    return locations


def get_recommended_install_path(
    binary_type: BinaryType = BinaryType.CLI,
    scope: InstallScope = InstallScope.USER,
    target_platform: Platform | None = None,
) -> Path:
    """Get the recommended installation path for the given criteria.

    Args:
        binary_type: CLI or GUI application
        scope: User or system installation
        target_platform: Target platform (defaults to current platform)

    Returns:
        Recommended Path for installation
    """
    locations = get_default_install_locations(binary_type, scope, target_platform)
    if locations:
        return locations[0].path
    # Fallback to ~/.local/bin
    return Path.home() / ".local" / "bin"


def ensure_install_path_exists(path: Path) -> bool:
    """Ensure the installation directory exists, creating it if needed.

    Args:
        path: Path to the installation directory

    Returns:
        True if directory exists or was created, False otherwise
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except PermissionError:
        return False


def is_path_in_system_path(path: Path) -> bool:
    """Check if a directory is in the system PATH.

    Args:
        path: Directory path to check

    Returns:
        True if path is in system PATH
    """
    path_str = str(path.resolve())
    system_path = os.environ.get("PATH", "")

    if get_current_platform() == Platform.WINDOWS:
        # Case-insensitive comparison on Windows
        return path_str.lower() in system_path.lower().split(os.pathsep)
    else:
        return path_str in system_path.split(os.pathsep)


def get_path_setup_instructions(path: Path) -> str:
    """Get shell instructions to add a path to the system PATH.

    Args:
        path: Directory path to add

    Returns:
        Shell command or instructions as a string
    """
    current_platform = get_current_platform()
    path_str = str(path)

    if current_platform == Platform.MACOS:
        return f'''# Add to ~/.zshrc or ~/.bash_profile:
export PATH="{path_str}:$PATH"'''

    elif current_platform == Platform.LINUX:
        return f'''# Add to ~/.bashrc or ~/.profile:
export PATH="{path_str}:$PATH"'''

    elif current_platform == Platform.WINDOWS:
        return f'''# PowerShell (user PATH):
[Environment]::SetEnvironmentVariable("Path", "{path_str};" + [Environment]::GetEnvironmentVariable("Path", "User"), "User")

# Or via System Properties > Environment Variables'''

    return f"Add {path_str} to your PATH environment variable"


def find_shadow_conflicts(
    binary_name: str,
    install_path: Path,
    binary_type: BinaryType = BinaryType.CLI,
) -> list[tuple[Path, InstallScope, str]]:
    """Find other binaries with the same name in standard install locations.

    Checks all default install locations for the current platform to find
    binaries or applications with the same name that could shadow or be
    shadowed by the binary being installed.

    Args:
        binary_name: Name of the binary (e.g., "mytool" or "MyApp.app")
        install_path: The path where the binary is/will be installed
        binary_type: Type of binary (CLI or GUI)

    Returns:
        List of tuples (path, scope, description) for each conflicting binary found
    """
    conflicts: list[tuple[Path, InstallScope, str]] = []
    current_platform = get_current_platform()
    install_path_resolved = install_path.resolve()

    # Get all install locations for this binary type
    locations = get_default_install_locations(binary_type=binary_type)

    for location in locations:
        # Determine the candidate path based on binary type
        if binary_type == BinaryType.GUI:
            # For GUI apps, check for .app bundles on macOS or similar
            if current_platform == Platform.MACOS:
                if not binary_name.endswith(".app"):
                    candidate = location.path / f"{binary_name}.app"
                else:
                    candidate = location.path / binary_name
            else:
                candidate = location.path / binary_name
        else:
            # For CLI tools
            candidate = location.path / binary_name
            # On Windows, also check for .exe
            if current_platform == Platform.WINDOWS and not binary_name.endswith(".exe"):
                candidate_exe = location.path / f"{binary_name}.exe"
                if candidate_exe.exists() and candidate_exe.resolve() != install_path_resolved:
                    conflicts.append((
                        candidate_exe,
                        location.scope,
                        location.description,
                    ))

        # Skip if this is the same as our install path
        if candidate.exists():
            try:
                if candidate.resolve() == install_path_resolved:
                    continue
            except OSError:
                # Handle permission errors on resolve
                if candidate == install_path:
                    continue

            conflicts.append((
                candidate,
                location.scope,
                location.description,
            ))

    return conflicts


def get_path_priority(path: Path) -> int:
    """Get the priority of a path in the system PATH.

    Lower numbers mean higher priority (earlier in PATH).

    Args:
        path: Directory path to check

    Returns:
        Index in PATH, or -1 if not found
    """
    system_path = os.environ.get("PATH", "")
    path_str = str(path.resolve())

    if get_current_platform() == Platform.WINDOWS:
        paths = [p.lower() for p in system_path.split(os.pathsep)]
        path_str = path_str.lower()
    else:
        paths = system_path.split(os.pathsep)

    try:
        return paths.index(path_str)
    except ValueError:
        return -1


def check_shadow_priority(
    install_path: Path,
    conflict_path: Path,
) -> str:
    """Determine which binary takes priority based on PATH order.

    Args:
        install_path: Path where our binary is installed
        conflict_path: Path where conflicting binary exists

    Returns:
        Description of which takes priority, or empty if neither in PATH
    """
    install_dir = install_path.parent
    conflict_dir = conflict_path.parent

    install_priority = get_path_priority(install_dir)
    conflict_priority = get_path_priority(conflict_dir)

    if install_priority == -1 and conflict_priority == -1:
        return "neither in PATH"
    elif install_priority == -1:
        return f"shadowed by {conflict_path} (yours not in PATH)"
    elif conflict_priority == -1:
        return f"takes priority (conflict not in PATH)"
    elif install_priority < conflict_priority:
        return f"takes priority over {conflict_path}"
    elif install_priority > conflict_priority:
        return f"shadowed by {conflict_path}"
    else:
        return "same directory priority"


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
    TEST_FAILED = "test_failed"  # Build succeeded but tests failed
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


class BuildFailureReason(Enum):
    """Categorized reason for build failure."""

    COMMAND_NOT_FOUND = "command_not_found"
    COMPILATION_ERROR = "compilation_error"
    LINKER_ERROR = "linker_error"
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    MISSING_DEPENDENCY = "missing_dependency"
    TEST_FAILED = "test_failed"
    SERVICE_STOP_FAILED = "service_stop_failed"
    SERVICE_START_FAILED = "service_start_failed"
    UNKNOWN = "unknown"


class ServiceType(Enum):
    """Type of service manager."""

    LAUNCHD = "launchd"    # macOS launchd
    SYSTEMD = "systemd"    # Linux systemd
    CUSTOM = "custom"      # Custom stop/start commands
    NONE = "none"          # Not a service


class ServiceStatus(Enum):
    """Status of a service."""

    RUNNING = "running"
    STOPPED = "stopped"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class CodesignStatus(Enum):
    """Status of a codesigning operation."""

    SIGNED = "signed"
    VALID = "valid"
    INVALID = "invalid"
    UNSIGNED = "unsigned"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_SUPPORTED = "not_supported"


class CodesignConfigDict(TypedDict, total=False):
    """Type definition for codesigning configuration."""

    enabled: bool
    identity: str
    entitlements: str | None
    options: list[str]
    force: bool


class ServiceConfigDict(TypedDict, total=False):
    """Type definition for service configuration."""

    enabled: bool
    type: str  # "launchd", "systemd", or "custom"
    name: str  # Service identifier (launchd label, systemd unit name)
    restart_after_build: bool
    stop_timeout_seconds: int
    start_timeout_seconds: int
    stop_cmd: str | None  # Custom stop command
    start_cmd: str | None  # Custom start command
    status_cmd: str | None  # Custom status check command


class BinaryConfigDict(TypedDict, total=False):
    """Type definition for binary configuration."""

    source_patterns: list[str]
    build_cmd: str
    install_path: str
    binary_type: str  # "cli" or "gui"
    install_scope: str  # "user" or "system"
    language: str
    rebuild_on_commit: bool
    check_in_path: bool
    working_dir: str
    env: dict[str, str]
    timeout: int
    codesign: CodesignConfigDict
    service: ServiceConfigDict  # Service management configuration
    ensure_executable: bool  # Set executable permissions after build
    test_cmd: str  # Command to run after build to verify binary
    test_timeout: int  # Timeout for test command
    retry_count: int  # Number of retries on build failure
    retry_delay_seconds: float  # Delay between retries


class ConfigDict(TypedDict, total=False):
    """Type definition for configuration dictionary."""

    binaries: dict[str, BinaryConfigDict]
    system_binaries: dict[str, str]  # Custom paths for system binaries (git, codesign)
    auto_rebuild: bool
    stale_threshold_hours: int
    watch_debounce_ms: int
    pre_commit_policy: str
    track_by: str
    parallel_builds: bool
    max_workers: int
    codesign: CodesignConfigDict
    ensure_executable: bool  # Global default for setting executable permissions


@dataclass
class CodesignConfig:
    """Configuration for codesigning binaries."""

    enabled: bool = False
    identity: str = "-"  # "-" for ad-hoc signing
    entitlements: str | None = None
    options: list[str] = field(default_factory=list)
    force: bool = True  # Replace existing signatures

    @classmethod
    def from_dict(cls, data: CodesignConfigDict | None) -> CodesignConfig:
        """Create from dictionary."""
        if not data:
            return cls()
        return cls(
            enabled=data.get("enabled", False),
            identity=data.get("identity", "-"),
            entitlements=data.get("entitlements"),
            options=data.get("options", []),
            force=data.get("force", True),
        )

    def merge_with(self, other: CodesignConfig) -> CodesignConfig:
        """Merge with another config (self takes precedence for explicit values)."""
        return CodesignConfig(
            enabled=self.enabled if self.enabled else other.enabled,
            identity=self.identity if self.identity != "-" else other.identity,
            entitlements=self.entitlements or other.entitlements,
            options=self.options if self.options else other.options,
            force=self.force,
        )


@dataclass
class ServiceConfig:
    """Configuration for service management."""

    enabled: bool = False
    service_type: ServiceType = ServiceType.NONE
    name: str = ""  # Service identifier (launchd label, systemd unit)
    restart_after_build: bool = True
    stop_timeout_seconds: int = 30
    start_timeout_seconds: int = 10
    stop_cmd: str | None = None  # Custom stop command
    start_cmd: str | None = None  # Custom start command
    status_cmd: str | None = None  # Custom status check command

    @classmethod
    def from_dict(cls, data: ServiceConfigDict | None) -> ServiceConfig:
        """Create from dictionary."""
        if not data:
            return cls()

        # Parse service type
        service_type_str = data.get("type", "none")
        try:
            service_type = ServiceType(service_type_str)
        except ValueError:
            service_type = ServiceType.NONE

        return cls(
            enabled=data.get("enabled", False),
            service_type=service_type,
            name=data.get("name", ""),
            restart_after_build=data.get("restart_after_build", True),
            stop_timeout_seconds=data.get("stop_timeout_seconds", 30),
            start_timeout_seconds=data.get("start_timeout_seconds", 10),
            stop_cmd=data.get("stop_cmd"),
            start_cmd=data.get("start_cmd"),
            status_cmd=data.get("status_cmd"),
        )


@dataclass
class BinaryConfig:
    """Configuration for a single tracked binary."""

    name: str
    source_patterns: list[str] = field(default_factory=list)
    build_cmd: str = ""
    install_path: str = ""
    binary_type: BinaryType = BinaryType.CLI
    install_scope: InstallScope = InstallScope.USER
    language: str = ""
    rebuild_on_commit: bool = True
    check_in_path: bool = True
    working_dir: str = "."
    env: dict[str, str] = field(default_factory=dict)
    timeout: int = 300  # 5 minutes default
    codesign: CodesignConfig = field(default_factory=CodesignConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    ensure_executable: bool = True  # Set executable permissions after build
    test_cmd: str = ""  # Command to run after build to verify binary
    test_timeout: int = 60  # Timeout for test command in seconds
    retry_count: int = 0  # Number of retries on build failure
    retry_delay_seconds: float = 1.0  # Delay between retries

    @classmethod
    def from_dict(cls, name: str, data: BinaryConfigDict, global_ensure_executable: bool = True) -> BinaryConfig:
        """Create from dictionary."""
        # Parse binary type
        binary_type_str = data.get("binary_type", "cli")
        try:
            binary_type = BinaryType(binary_type_str)
        except ValueError:
            binary_type = BinaryType.CLI

        # Parse install scope
        install_scope_str = data.get("install_scope", "user")
        try:
            install_scope = InstallScope(install_scope_str)
        except ValueError:
            install_scope = InstallScope.USER

        # Determine install path (use default if not specified)
        install_path = data.get("install_path", "")
        if not install_path:
            default_dir = get_recommended_install_path(binary_type, install_scope)
            install_path = str(default_dir / name)

        return cls(
            name=name,
            source_patterns=data.get("source_patterns", []),
            build_cmd=data.get("build_cmd", ""),
            install_path=install_path,
            binary_type=binary_type,
            install_scope=install_scope,
            language=data.get("language", ""),
            rebuild_on_commit=data.get("rebuild_on_commit", True),
            check_in_path=data.get("check_in_path", True),
            working_dir=data.get("working_dir", "."),
            env=data.get("env", {}),
            timeout=data.get("timeout", 300),
            codesign=CodesignConfig.from_dict(data.get("codesign")),
            service=ServiceConfig.from_dict(data.get("service")),
            ensure_executable=data.get("ensure_executable", global_ensure_executable),
            test_cmd=data.get("test_cmd", ""),
            test_timeout=data.get("test_timeout", 60),
            retry_count=data.get("retry_count", 0),
            retry_delay_seconds=data.get("retry_delay_seconds", 1.0),
        )

    def get_expanded_install_path(self) -> Path:
        """Get install path with ~ expanded."""
        return Path(os.path.expanduser(self.install_path))

    def get_install_directory(self) -> Path:
        """Get the directory containing the binary."""
        return self.get_expanded_install_path().parent

    def ensure_install_directory(self) -> bool:
        """Ensure the install directory exists."""
        return ensure_install_path_exists(self.get_install_directory())


@dataclass
class TrackConfig:
    """Configuration for binary tracking operations."""

    root_dir: Path = field(default_factory=Path.cwd)
    binaries: dict[str, BinaryConfig] = field(default_factory=dict)
    system_binaries: dict[str, str] = field(default_factory=lambda: {"git": "git", "codesign": "codesign"})
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
    codesign: CodesignConfig = field(default_factory=CodesignConfig)
    ensure_executable: bool = True  # Global default for setting executable permissions

    @classmethod
    def from_dict(cls, data: ConfigDict, root_dir: Path | None = None) -> TrackConfig:
        """Create from dictionary."""
        # Parse global codesign config first
        global_codesign = CodesignConfig.from_dict(data.get("codesign"))

        # Parse global ensure_executable setting (default: True)
        global_ensure_executable = data.get("ensure_executable", True)

        binaries = {}
        for name, binary_data in data.get("binaries", {}).items():
            binary = BinaryConfig.from_dict(name, binary_data, global_ensure_executable)
            # Merge per-binary codesign with global config
            binary.codesign = binary.codesign.merge_with(global_codesign)
            binaries[name] = binary

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

        # Parse system binaries with defaults
        system_binaries = {
            "git": "git",
            "codesign": "codesign",
        }
        if "system_binaries" in data:
            system_binaries.update(data["system_binaries"])

        config = cls(
            binaries=binaries,
            system_binaries=system_binaries,
            auto_rebuild=data.get("auto_rebuild", False),
            stale_threshold_hours=data.get("stale_threshold_hours", 24),
            watch_debounce_ms=data.get("watch_debounce_ms", 500),
            pre_commit_policy=policy,
            track_by=track_by,
            parallel_builds=data.get("parallel_builds", True),
            max_workers=data.get("max_workers", 4),
            codesign=global_codesign,
            ensure_executable=global_ensure_executable,
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

    def to_dict(self) -> dict[str, Any]:
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
    def from_dict(cls, data: dict[str, Any]) -> BuildRecord:
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

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "records": {name: record.to_dict() for name, record in self.records.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildManifest:
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
class ShadowConflict:
    """Represents a binary with the same name at a different location."""

    name: str
    path: Path
    scope: InstallScope
    binary_type: BinaryType
    is_executable: bool = False
    description: str = ""

    def __str__(self) -> str:
        scope_str = "system" if self.scope == InstallScope.SYSTEM else "user"
        return f"{self.path} ({scope_str})"


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
    shadow_conflicts: list[ShadowConflict] = field(default_factory=list)


@dataclass
class RebuildResult:
    """Result of a rebuild operation."""

    name: str
    status: RebuildStatus
    duration: float = 0.0
    message: str = ""
    output: str = ""
    # Enhanced failure tracking
    failure_reason: BuildFailureReason | None = None
    exit_code: int | None = None
    test_output: str = ""
    test_duration: float = 0.0
    suggestion: str = ""  # Actionable hint for the user
    retry_attempt: int = 0  # Which attempt this was (0 = first try)
    # Service management tracking
    service_stopped: bool = False
    service_started: bool = False
    service_status: ServiceStatus = ServiceStatus.UNKNOWN


@dataclass
class ServiceResult:
    """Result of a service operation."""

    name: str
    service_type: ServiceType
    status: ServiceStatus
    operation: str = ""  # "stop", "start", "status"
    success: bool = False
    message: str = ""
    duration: float = 0.0


@dataclass
class CodesignResult:
    """Result of a codesigning operation."""

    name: str
    status: CodesignStatus
    identity: str = ""
    message: str = ""
    details: str = ""


@dataclass
class TrackResult:
    """Result of a tracking operation."""

    statuses: list[BinaryStatusResult] = field(default_factory=list)
    rebuilds: list[RebuildResult] = field(default_factory=list)
    all_current: bool = False
    stale_count: int = 0
    missing_count: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
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
                    "shadow_conflicts": [
                        {
                            "name": c.name,
                            "path": str(c.path),
                            "scope": c.scope.value,
                            "binary_type": c.binary_type.value,
                            "is_executable": c.is_executable,
                            "description": c.description,
                        }
                        for c in s.shadow_conflicts
                    ],
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


# Global config instance for binary paths
_global_track_config: TrackConfig | None = None


def set_global_track_config(config: TrackConfig) -> None:
    """Set the global track config instance."""
    global _global_track_config
    _global_track_config = config


def get_git_binary_for_track() -> str:
    """Get the configured git binary path."""
    if _global_track_config:
        return _global_track_config.system_binaries.get("git", "git")
    return "git"


def get_codesign_binary() -> str:
    """Get the configured codesign binary path."""
    if _global_track_config:
        return _global_track_config.system_binaries.get("codesign", "codesign")
    return "codesign"


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
    """Load configuration from YAML file."""
    if root_dir is None:
        root_dir = Path.cwd()

    # If explicit config path provided, try to load it
    if config_path:
        full_path = root_dir / config_path
        if full_path.exists():
            with open(full_path, encoding="utf-8") as f:
                data: ConfigDict = yaml.safe_load(f) or {}
                return data
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # Try default config file names
    for filename in CONFIG_FILE_NAMES:
        full_path = root_dir / filename
        if full_path.exists():
            with open(full_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return data

    return {}


def load_env_config() -> ConfigDict:
    """Load configuration from environment variables."""
    config: ConfigDict = {}

    if os.environ.get("BINARY_TRACK_AUTO_REBUILD") == "true":
        config["auto_rebuild"] = True
    if os.environ.get("BINARY_TRACK_POLICY"):
        config["pre_commit_policy"] = os.environ["BINARY_TRACK_POLICY"]

    # Codesigning environment variables
    codesign_enabled = os.environ.get("BINARY_TRACK_CODESIGN") == "true"
    codesign_identity = os.environ.get("BINARY_TRACK_CODESIGN_ID")
    if codesign_enabled or codesign_identity:
        config["codesign"] = {
            "enabled": codesign_enabled,
        }
        if codesign_identity:
            config["codesign"]["identity"] = codesign_identity

    return config


def save_manifest(manifest: BuildManifest, root_dir: Path) -> None:
    """Save the build manifest to disk."""
    manifest.updated_at = datetime.now(timezone.utc).isoformat()
    if not manifest.created_at:
        manifest.created_at = manifest.updated_at

    manifest_path = root_dir / BUILD_MANIFEST_FILE
    with open(manifest_path, "w", encoding="utf-8") as f:
        yaml.dump(manifest.to_dict(), f, default_flow_style=False, sort_keys=False)


def load_manifest(root_dir: Path) -> BuildManifest:
    """Load the build manifest from disk."""
    manifest_path = root_dir / BUILD_MANIFEST_FILE
    if not manifest_path.exists():
        return BuildManifest()

    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return BuildManifest.from_dict(data)
    except (yaml.YAMLError, KeyError):
        return BuildManifest()


def get_git_root(path: Path) -> Path | None:
    """Get the git repository root."""
    try:
        result = subprocess.run(
            [get_git_binary_for_track(), "rev-parse", "--show-toplevel"],
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
            [get_git_binary_for_track(), "rev-parse", "HEAD"],
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
            [get_git_binary_for_track(), "log", "-1", "--format=%H", "--"] + [str(f) for f in files],
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
            [get_git_binary_for_track(), "rev-list", "--count", f"{old_commit}..{new_commit}"],
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

    # Check for shadow conflicts (binaries with same name elsewhere)
    binary_name = path.name
    conflicts = find_shadow_conflicts(
        binary_name,
        path,
        binary_config.binary_type,
    )
    for conflict_path, conflict_scope, description in conflicts:
        result.shadow_conflicts.append(ShadowConflict(
            name=binary_name,
            path=conflict_path,
            scope=conflict_scope,
            binary_type=binary_config.binary_type,
            is_executable=os.access(conflict_path, os.X_OK) if conflict_path.exists() else False,
            description=description,
        ))

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


def ensure_binary_executable(
    binary_path: Path,
    logger: Logger,
    dry_run: bool = False,
) -> bool:
    """Ensure a binary file has executable permissions.

    Sets the executable bit for user, group, and others while preserving
    existing read/write permissions. Skips directories (e.g., .app bundles)
    and files that are already executable.

    Args:
        binary_path: Path to the binary file
        logger: Logger instance for output
        dry_run: If True, only log what would be done

    Returns:
        True if the file is now executable (or was already), False on failure
    """
    # Skip directories (e.g., macOS .app bundles)
    if not binary_path.exists():
        logger.debug(f"Cannot set executable: {binary_path} does not exist")
        return False

    if binary_path.is_dir():
        logger.debug(f"Skipping directory: {binary_path}")
        return True  # .app bundles handle permissions internally

    # Check if already executable
    if os.access(binary_path, os.X_OK):
        logger.debug(f"Already executable: {binary_path}")
        return True

    if dry_run:
        logger.info(f"[DRY-RUN] Would set executable permissions: {binary_path}")
        return True

    try:
        current_mode = binary_path.stat().st_mode
        # Add execute permission for user, group, and others
        new_mode = current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        binary_path.chmod(new_mode)
        logger.debug(f"Set executable permissions on {binary_path}")
        return True
    except PermissionError as e:
        logger.warn(f"Could not set executable permissions on {binary_path}: {e}")
        return False
    except OSError as e:
        logger.warn(f"Error setting permissions on {binary_path}: {e}")
        return False


# =============================================================================
# Service Management Functions
# =============================================================================


def get_service_status(
    service_config: ServiceConfig,
    logger: Logger,
) -> ServiceResult:
    """Check the current status of a service.

    Args:
        service_config: Service configuration
        logger: Logger instance

    Returns:
        ServiceResult with current status
    """
    result = ServiceResult(
        name=service_config.name,
        service_type=service_config.service_type,
        status=ServiceStatus.UNKNOWN,
        operation="status",
    )

    if not service_config.enabled or not service_config.name:
        result.status = ServiceStatus.UNKNOWN
        result.message = "Service not configured"
        return result

    start_time = time.time()

    try:
        if service_config.service_type == ServiceType.LAUNCHD:
            # macOS launchd
            proc = subprocess.run(
                ["launchctl", "list", service_config.name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            result.duration = time.time() - start_time

            if proc.returncode == 0:
                result.status = ServiceStatus.RUNNING
                result.success = True
                result.message = "Service is running"
            else:
                # Check if it's not found vs stopped
                if "could not find service" in proc.stderr.lower():
                    result.status = ServiceStatus.NOT_FOUND
                    result.message = f"Service '{service_config.name}' not found"
                else:
                    result.status = ServiceStatus.STOPPED
                    result.message = "Service is stopped"

        elif service_config.service_type == ServiceType.SYSTEMD:
            # Linux systemd
            proc = subprocess.run(
                ["systemctl", "is-active", service_config.name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            result.duration = time.time() - start_time

            status_output = proc.stdout.strip().lower()
            if status_output == "active":
                result.status = ServiceStatus.RUNNING
                result.success = True
                result.message = "Service is running"
            elif status_output in ("inactive", "dead"):
                result.status = ServiceStatus.STOPPED
                result.message = "Service is stopped"
            elif "could not be found" in proc.stderr.lower():
                result.status = ServiceStatus.NOT_FOUND
                result.message = f"Service '{service_config.name}' not found"
            else:
                result.status = ServiceStatus.UNKNOWN
                result.message = f"Unknown status: {status_output}"

        elif service_config.service_type == ServiceType.CUSTOM:
            # Custom status command
            if service_config.status_cmd:
                proc = subprocess.run(
                    service_config.status_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                result.duration = time.time() - start_time

                if proc.returncode == 0:
                    result.status = ServiceStatus.RUNNING
                    result.success = True
                    result.message = "Service is running"
                else:
                    result.status = ServiceStatus.STOPPED
                    result.message = "Service is stopped"
            else:
                result.status = ServiceStatus.UNKNOWN
                result.message = "No status command configured"

        else:
            result.status = ServiceStatus.UNKNOWN
            result.message = "Unknown service type"

    except subprocess.TimeoutExpired:
        result.duration = time.time() - start_time
        result.status = ServiceStatus.UNKNOWN
        result.message = "Status check timed out"

    except FileNotFoundError as e:
        result.duration = time.time() - start_time
        result.status = ServiceStatus.UNKNOWN
        result.message = f"Service manager not found: {e}"

    except Exception as e:
        result.duration = time.time() - start_time
        result.status = ServiceStatus.UNKNOWN
        result.message = f"Error checking status: {e}"

    return result


def stop_service(
    service_config: ServiceConfig,
    logger: Logger,
    dry_run: bool = False,
) -> ServiceResult:
    """Stop a running service.

    Args:
        service_config: Service configuration
        logger: Logger instance
        dry_run: If True, only log what would be done

    Returns:
        ServiceResult indicating success/failure
    """
    result = ServiceResult(
        name=service_config.name,
        service_type=service_config.service_type,
        status=ServiceStatus.UNKNOWN,
        operation="stop",
    )

    if not service_config.enabled or not service_config.name:
        result.success = True
        result.message = "Service not configured, skipping"
        return result

    logger.info(f"Stopping service '{service_config.name}'...")

    if dry_run:
        result.success = True
        result.message = f"[DRY-RUN] Would stop service '{service_config.name}'"
        logger.info(result.message)
        return result

    start_time = time.time()

    try:
        cmd: list[str] | str

        if service_config.service_type == ServiceType.LAUNCHD:
            # macOS: launchctl stop
            cmd = ["launchctl", "stop", service_config.name]
        elif service_config.service_type == ServiceType.SYSTEMD:
            # Linux: systemctl stop
            cmd = ["systemctl", "stop", service_config.name]
        elif service_config.service_type == ServiceType.CUSTOM:
            if service_config.stop_cmd:
                cmd = service_config.stop_cmd
            else:
                result.success = False
                result.message = "No stop command configured"
                logger.error(result.message)
                return result
        else:
            result.success = False
            result.message = "Unknown service type"
            return result

        # Execute stop command
        if isinstance(cmd, list):
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=service_config.stop_timeout_seconds,
            )
        else:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=service_config.stop_timeout_seconds,
            )

        result.duration = time.time() - start_time

        if proc.returncode == 0:
            result.success = True
            result.status = ServiceStatus.STOPPED
            result.message = f"Service stopped in {result.duration:.1f}s"
            logger.success(result.message)
        else:
            result.success = False
            result.message = f"Failed to stop service: {proc.stderr or proc.stdout}"
            logger.error(result.message)

    except subprocess.TimeoutExpired:
        result.duration = time.time() - start_time
        result.success = False
        result.message = f"Stop timed out after {service_config.stop_timeout_seconds}s"
        logger.error(result.message)

    except FileNotFoundError as e:
        result.duration = time.time() - start_time
        result.success = False
        result.message = f"Service manager not found: {e}"
        logger.error(result.message)

    except Exception as e:
        result.duration = time.time() - start_time
        result.success = False
        result.message = f"Error stopping service: {e}"
        logger.error(result.message)

    return result


def start_service(
    service_config: ServiceConfig,
    logger: Logger,
    dry_run: bool = False,
) -> ServiceResult:
    """Start a service.

    Args:
        service_config: Service configuration
        logger: Logger instance
        dry_run: If True, only log what would be done

    Returns:
        ServiceResult indicating success/failure
    """
    result = ServiceResult(
        name=service_config.name,
        service_type=service_config.service_type,
        status=ServiceStatus.UNKNOWN,
        operation="start",
    )

    if not service_config.enabled or not service_config.name:
        result.success = True
        result.message = "Service not configured, skipping"
        return result

    logger.info(f"Starting service '{service_config.name}'...")

    if dry_run:
        result.success = True
        result.message = f"[DRY-RUN] Would start service '{service_config.name}'"
        logger.info(result.message)
        return result

    start_time = time.time()

    try:
        cmd: list[str] | str

        if service_config.service_type == ServiceType.LAUNCHD:
            # macOS: launchctl start
            cmd = ["launchctl", "start", service_config.name]
        elif service_config.service_type == ServiceType.SYSTEMD:
            # Linux: systemctl start
            cmd = ["systemctl", "start", service_config.name]
        elif service_config.service_type == ServiceType.CUSTOM:
            if service_config.start_cmd:
                cmd = service_config.start_cmd
            else:
                result.success = False
                result.message = "No start command configured"
                logger.error(result.message)
                return result
        else:
            result.success = False
            result.message = "Unknown service type"
            return result

        # Execute start command
        if isinstance(cmd, list):
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=service_config.start_timeout_seconds,
            )
        else:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=service_config.start_timeout_seconds,
            )

        result.duration = time.time() - start_time

        if proc.returncode == 0:
            # Give service a moment to start, then verify
            time.sleep(0.5)
            status = get_service_status(service_config, logger)

            if status.status == ServiceStatus.RUNNING:
                result.success = True
                result.status = ServiceStatus.RUNNING
                result.message = f"Service started in {result.duration:.1f}s"
                logger.success(result.message)
            else:
                result.success = False
                result.status = status.status
                result.message = f"Service started but not running: {status.message}"
                logger.warn(result.message)
        else:
            result.success = False
            result.message = f"Failed to start service: {proc.stderr or proc.stdout}"
            logger.error(result.message)

    except subprocess.TimeoutExpired:
        result.duration = time.time() - start_time
        result.success = False
        result.message = f"Start timed out after {service_config.start_timeout_seconds}s"
        logger.error(result.message)

    except FileNotFoundError as e:
        result.duration = time.time() - start_time
        result.success = False
        result.message = f"Service manager not found: {e}"
        logger.error(result.message)

    except Exception as e:
        result.duration = time.time() - start_time
        result.success = False
        result.message = f"Error starting service: {e}"
        logger.error(result.message)

    return result


def restart_service(
    service_config: ServiceConfig,
    logger: Logger,
    dry_run: bool = False,
) -> ServiceResult:
    """Restart a service (stop then start).

    Args:
        service_config: Service configuration
        logger: Logger instance
        dry_run: If True, only log what would be done

    Returns:
        ServiceResult indicating success/failure
    """
    result = ServiceResult(
        name=service_config.name,
        service_type=service_config.service_type,
        status=ServiceStatus.UNKNOWN,
        operation="restart",
    )

    if not service_config.enabled or not service_config.name:
        result.success = True
        result.message = "Service not configured, skipping"
        return result

    # Stop the service
    stop_result = stop_service(service_config, logger, dry_run)
    if not stop_result.success:
        result.success = False
        result.message = f"Failed to stop: {stop_result.message}"
        return result

    # Small delay between stop and start
    if not dry_run:
        time.sleep(0.5)

    # Start the service
    start_result = start_service(service_config, logger, dry_run)
    result.success = start_result.success
    result.status = start_result.status
    result.duration = stop_result.duration + start_result.duration
    result.message = start_result.message

    return result


def _get_stop_command_hint(service_config: ServiceConfig) -> str:
    """Get a hint for how to manually stop the service."""
    if service_config.service_type == ServiceType.LAUNCHD:
        return f"launchctl stop {service_config.name}"
    elif service_config.service_type == ServiceType.SYSTEMD:
        return f"sudo systemctl stop {service_config.name}"
    elif service_config.service_type == ServiceType.CUSTOM and service_config.stop_cmd:
        return service_config.stop_cmd
    return f"stop service '{service_config.name}'"


def _get_start_command_hint(service_config: ServiceConfig) -> str:
    """Get a hint for how to manually start the service."""
    if service_config.service_type == ServiceType.LAUNCHD:
        return f"launchctl start {service_config.name}"
    elif service_config.service_type == ServiceType.SYSTEMD:
        return f"sudo systemctl start {service_config.name}"
    elif service_config.service_type == ServiceType.CUSTOM and service_config.start_cmd:
        return service_config.start_cmd
    return f"start service '{service_config.name}'"


def categorize_build_failure(
    exit_code: int,
    stderr: str,
    stdout: str,
    language: str,
) -> tuple[BuildFailureReason, str]:
    """Analyze build failure output and return categorized reason with suggestion.

    Args:
        exit_code: The process exit code
        stderr: Standard error output
        stdout: Standard output
        language: The programming language (go, rust, swift, etc.)

    Returns:
        Tuple of (BuildFailureReason, actionable suggestion string)
    """
    output = (stderr + stdout).lower()

    # Command not found (highest priority - tool isn't even installed)
    if "command not found" in output or "not recognized" in output:
        tool = language or "build"
        return (
            BuildFailureReason.COMMAND_NOT_FOUND,
            f"Build tool not found. Ensure {tool} toolchain is installed and in PATH",
        )

    # Permission denied (system-level issue)
    if "permission denied" in output:
        return (
            BuildFailureReason.PERMISSION_DENIED,
            "Permission denied. Check file permissions or run with appropriate privileges",
        )

    # Language-specific compilation errors (check BEFORE generic patterns)
    if language == "go":
        if "undefined:" in output or "cannot refer to" in output:
            return (
                BuildFailureReason.COMPILATION_ERROR,
                "Go compilation error. Check for undefined references or import issues",
            )
    elif language == "rust":
        if "error[e" in output:
            return (
                BuildFailureReason.COMPILATION_ERROR,
                "Rust compilation error. Run 'cargo check' for detailed diagnostics",
            )
    elif language == "swift":
        if "error:" in output and "swift" in output:
            return (
                BuildFailureReason.COMPILATION_ERROR,
                "Swift compilation error. Check Xcode build logs for details",
            )
    elif language in ("c", "cpp", "c++"):
        if "undefined reference" in output or "unresolved external" in output:
            return (
                BuildFailureReason.LINKER_ERROR,
                "Linker error. Check library paths and ensure all dependencies are linked",
            )
        if "error:" in output:
            return (
                BuildFailureReason.COMPILATION_ERROR,
                "C/C++ compilation error. Check syntax and include paths",
            )

    # Generic compilation/linker patterns
    if any(x in output for x in ["syntax error", "parse error", "unexpected token"]):
        return (
            BuildFailureReason.COMPILATION_ERROR,
            "Syntax error in source code. Check the build output for line numbers",
        )

    if any(x in output for x in ["undefined reference", "unresolved symbol", "linker error"]):
        return (
            BuildFailureReason.LINKER_ERROR,
            "Linker error. Verify library dependencies and link flags",
        )

    # Missing files/dependencies (check after compilation errors)
    if any(x in output for x in ["cannot find", "no such file", "not found", "cannot open"]):
        return (
            BuildFailureReason.MISSING_DEPENDENCY,
            "Missing file or dependency. Check that all required files exist and paths are correct",
        )

    return (
        BuildFailureReason.UNKNOWN,
        "Build failed. Check the output above for details",
    )


def run_test_command(
    binary_config: BinaryConfig,
    config: TrackConfig,
    logger: Logger,
) -> tuple[bool, str, float]:
    """Run the test command for a binary after successful build.

    Args:
        binary_config: The binary configuration
        config: The track configuration
        logger: Logger instance

    Returns:
        Tuple of (success, output, duration)
    """
    if not binary_config.test_cmd:
        return True, "", 0.0

    if config.dry_run:
        logger.info(f"[DRY-RUN] Would run tests: {binary_config.test_cmd}")
        return True, "", 0.0

    logger.info(f"Running tests for {binary_config.name}...")
    logger.debug(f"Test command: {binary_config.test_cmd}")

    env = os.environ.copy()
    env.update(binary_config.env)
    working_dir = config.root_dir / binary_config.working_dir

    start_time = time.time()
    try:
        proc = subprocess.run(
            binary_config.test_cmd,
            shell=True,
            cwd=working_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=binary_config.test_timeout,
        )
        duration = time.time() - start_time

        if proc.returncode == 0:
            logger.success(f"Tests passed in {duration:.1f}s")
            return True, proc.stdout, duration
        else:
            output = proc.stderr or proc.stdout
            logger.error(f"Tests failed with exit code {proc.returncode}")
            return False, output, duration

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        logger.error(f"Tests timed out after {binary_config.test_timeout}s")
        return False, f"Test timed out after {binary_config.test_timeout}s", duration

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Test error: {e}")
        return False, str(e), duration


def rebuild_binary(
    binary_config: BinaryConfig,
    config: TrackConfig,
    manifest: BuildManifest,
    logger: Logger,
) -> RebuildResult:
    """Rebuild a single binary with retry support and test validation.

    Handles the complete build lifecycle:
    1. Stop service if binary is a service (with configurable timeout)
    2. Build the binary (with retries if configured)
    3. Set executable permissions
    4. Run tests (if configured)
    5. Codesign (if enabled)
    6. Restart service if configured
    7. Update manifest (success or failure)
    """
    result = RebuildResult(name=binary_config.name, status=RebuildStatus.SKIPPED)

    if not binary_config.build_cmd:
        result.message = "No build command configured"
        return result

    # Handle service-aware builds
    service_config = binary_config.service
    service_was_running = False

    if service_config.enabled:
        # Check if service is running before we stop it
        status_result = get_service_status(service_config, logger)
        service_was_running = status_result.status == ServiceStatus.RUNNING

        if service_was_running:
            logger.info(f"Service '{service_config.name}' is running, will stop before rebuild")

    if config.dry_run:
        result.status = RebuildStatus.DRY_RUN
        result.message = f"Would run: {binary_config.build_cmd}"
        logger.info(f"[DRY-RUN] Would rebuild {binary_config.name}: {binary_config.build_cmd}")

        # Show what service operations would happen
        if service_config.enabled and service_was_running:
            logger.info(f"[DRY-RUN] Would stop service '{service_config.name}'")
            if service_config.restart_after_build:
                logger.info(f"[DRY-RUN] Would restart service '{service_config.name}'")

        if binary_config.test_cmd:
            run_test_command(binary_config, config, logger)
        return result

    # Stop service before rebuild if needed
    if service_config.enabled and service_was_running:
        stop_result = stop_service(service_config, logger, config.dry_run)
        result.service_stopped = stop_result.success

        if not stop_result.success:
            result.status = RebuildStatus.FAILED
            result.failure_reason = BuildFailureReason.SERVICE_STOP_FAILED
            result.message = f"Failed to stop service: {stop_result.message}"
            result.suggestion = f"Manually stop the service with: {_get_stop_command_hint(service_config)}"
            logger.error(result.message)
            return result

    # Prepare environment
    env = os.environ.copy()
    env.update(binary_config.env)
    working_dir = config.root_dir / binary_config.working_dir

    # Retry loop
    max_attempts = binary_config.retry_count + 1
    last_error = ""

    for attempt in range(max_attempts):
        result.retry_attempt = attempt

        if attempt > 0:
            logger.info(f"Retrying {binary_config.name} (attempt {attempt + 1}/{max_attempts})...")
            time.sleep(binary_config.retry_delay_seconds)
        else:
            logger.info(f"Rebuilding {binary_config.name}...")

        logger.debug(f"Command: {binary_config.build_cmd}")

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
            result.exit_code = proc.returncode

            if proc.returncode == 0:
                result.status = RebuildStatus.SUCCESS
                result.message = f"Built in {duration:.1f}s"
                result.output = proc.stdout
                logger.success(f"Rebuilt {binary_config.name} in {duration:.1f}s")

                # Ensure executable permissions if enabled
                if binary_config.ensure_executable:
                    binary_path = binary_config.get_expanded_install_path()
                    if not ensure_binary_executable(binary_path, logger, config.dry_run):
                        result.output += "\nWarning: Could not set executable permissions"

                # Run tests if configured
                if binary_config.test_cmd:
                    test_success, test_output, test_duration = run_test_command(
                        binary_config, config, logger
                    )
                    result.test_output = test_output
                    result.test_duration = test_duration

                    if not test_success:
                        result.status = RebuildStatus.TEST_FAILED
                        result.failure_reason = BuildFailureReason.TEST_FAILED
                        result.message = f"Build succeeded but tests failed"
                        result.suggestion = "Review test output and fix failing tests"

                        # Still restart service even if tests failed (binary was built)
                        if service_config.enabled and service_was_running and service_config.restart_after_build:
                            start_result = start_service(service_config, logger, config.dry_run)
                            result.service_started = start_result.success
                            result.service_status = start_result.status

                        # Record failed test in manifest
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
                            success=False,
                            error=f"Tests failed: {test_output[:200]}",
                        )
                        return result

                # Codesign if enabled
                if binary_config.codesign.enabled:
                    cs_result = codesign_binary(binary_config, config, logger)
                    if cs_result.status == CodesignStatus.FAILED:
                        result.output += f"\nCodesign warning: {cs_result.message}"

                # Restart service if it was running and restart is enabled
                if service_config.enabled and service_was_running and service_config.restart_after_build:
                    start_result = start_service(service_config, logger, config.dry_run)
                    result.service_started = start_result.success
                    result.service_status = start_result.status

                    if not start_result.success:
                        # Build succeeded but service failed to restart
                        result.output += f"\nWarning: Service restart failed: {start_result.message}"
                        logger.warn(f"Build succeeded but service restart failed")
                        logger.info(f"  💡 Start manually: {_get_start_command_hint(service_config)}")

                # Update manifest with success
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
                return result

            else:
                # Build failed
                stderr = proc.stderr or ""
                stdout = proc.stdout or ""
                result.output = stderr or stdout
                result.exit_code = proc.returncode
                last_error = f"Build failed with exit code {proc.returncode}"

                # Categorize the failure
                failure_reason, suggestion = categorize_build_failure(
                    proc.returncode, stderr, stdout, binary_config.language
                )
                result.failure_reason = failure_reason
                result.suggestion = suggestion

                if attempt < max_attempts - 1:
                    logger.warn(f"Build failed, will retry: {last_error}")
                    continue

        except subprocess.TimeoutExpired:
            result.duration = time.time() - start_time
            result.failure_reason = BuildFailureReason.TIMEOUT
            result.suggestion = f"Build exceeded {binary_config.timeout}s timeout. Consider increasing 'timeout' setting"
            last_error = f"Build timed out after {binary_config.timeout}s"

            if attempt < max_attempts - 1:
                logger.warn(f"Build timed out, will retry")
                continue

        except Exception as e:
            result.duration = time.time() - start_time
            result.failure_reason = BuildFailureReason.UNKNOWN
            result.suggestion = "Unexpected error. Check system logs and build environment"
            last_error = str(e)

            if attempt < max_attempts - 1:
                logger.warn(f"Build error, will retry: {e}")
                continue

    # All attempts failed
    result.status = RebuildStatus.FAILED
    result.message = last_error
    logger.error(f"Failed to rebuild {binary_config.name}: {last_error}")

    if result.suggestion:
        logger.info(f"  💡 Suggestion: {result.suggestion}")

    if result.output and config.verbose:
        for line in result.output.strip().split("\n")[:20]:  # Limit output
            logger.debug(f"  {line}")

    # Record failure in manifest
    current_commit, current_hashes, current_mtimes = get_source_fingerprint(
        config.root_dir, binary_config.source_patterns, config.track_by
    )
    manifest.records[binary_config.name] = BuildRecord(
        binary_name=binary_config.name,
        built_at=datetime.now(timezone.utc).isoformat(),
        source_commit=current_commit,
        source_hashes=current_hashes,
        source_mtimes=current_mtimes,
        build_duration=result.duration,
        success=False,
        error=last_error[:500],  # Truncate long errors
    )

    return result


def is_codesign_available() -> bool:
    """Check if codesign tool is available (macOS only)."""
    return shutil.which(get_codesign_binary()) is not None


def verify_signature(binary_path: Path, logger: Logger) -> CodesignResult:
    """Verify the codesign signature of a binary."""
    result = CodesignResult(
        name=binary_path.name,
        status=CodesignStatus.UNSIGNED,
    )

    if not is_codesign_available():
        result.status = CodesignStatus.NOT_SUPPORTED
        result.message = "codesign not available (macOS only)"
        return result

    if not binary_path.exists():
        result.status = CodesignStatus.FAILED
        result.message = f"Binary not found: {binary_path}"
        return result

    try:
        # Check if signed
        proc = subprocess.run(
            [get_codesign_binary(), "-v", "--verbose=2", str(binary_path)],
            capture_output=True,
            text=True,
        )

        if proc.returncode == 0:
            result.status = CodesignStatus.VALID
            result.message = "Valid signature"
            result.details = proc.stderr.strip()  # codesign outputs to stderr

            # Extract identity
            display_proc = subprocess.run(
                [get_codesign_binary(), "-d", "--verbose=2", str(binary_path)],
                capture_output=True,
                text=True,
            )
            for line in display_proc.stderr.split("\n"):
                if line.startswith("Authority="):
                    result.identity = line.split("=", 1)[1]
                    break
                elif "signed with a" in line.lower():
                    result.identity = "ad-hoc"
                    break
        else:
            # Check if unsigned or invalid
            if "not signed" in proc.stderr.lower():
                result.status = CodesignStatus.UNSIGNED
                result.message = "Binary is not signed"
            else:
                result.status = CodesignStatus.INVALID
                result.message = "Invalid signature"
                result.details = proc.stderr.strip()

    except Exception as e:
        result.status = CodesignStatus.FAILED
        result.message = f"Verification error: {e}"

    return result


def codesign_binary(
    binary_config: BinaryConfig,
    config: TrackConfig,
    logger: Logger,
) -> CodesignResult:
    """Sign a binary using macOS codesign."""
    result = CodesignResult(
        name=binary_config.name,
        status=CodesignStatus.SKIPPED,
    )

    # Get effective codesign config (per-binary merged with global)
    cs_config = binary_config.codesign

    if not cs_config.enabled:
        result.message = "Codesigning not enabled for this binary"
        return result

    if not is_codesign_available():
        result.status = CodesignStatus.NOT_SUPPORTED
        result.message = "codesign not available (macOS only)"
        return result

    binary_path = binary_config.get_expanded_install_path()

    if not binary_path.exists():
        result.status = CodesignStatus.FAILED
        result.message = f"Binary not found: {binary_path}"
        return result

    if config.dry_run:
        result.status = CodesignStatus.SKIPPED
        cmd_preview = f"codesign -s '{cs_config.identity}'"
        if cs_config.force:
            cmd_preview += " -f"
        if cs_config.options:
            cmd_preview += f" --options={','.join(cs_config.options)}"
        if cs_config.entitlements:
            cmd_preview += f" --entitlements={cs_config.entitlements}"
        cmd_preview += f" {binary_path}"
        result.message = f"Would run: {cmd_preview}"
        logger.info(f"[DRY-RUN] Would sign {binary_config.name}: {cmd_preview}")
        return result

    logger.info(f"Signing {binary_config.name}...")

    # Build codesign command
    cmd = [get_codesign_binary(), "-s", cs_config.identity]

    if cs_config.force:
        cmd.append("-f")

    if cs_config.options:
        cmd.append(f"--options={','.join(cs_config.options)}")

    if cs_config.entitlements:
        entitlements_path = Path(os.path.expanduser(cs_config.entitlements))
        if not entitlements_path.exists():
            result.status = CodesignStatus.FAILED
            result.message = f"Entitlements file not found: {entitlements_path}"
            return result
        cmd.append(f"--entitlements={entitlements_path}")

    cmd.append(str(binary_path))

    logger.debug(f"Command: {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if proc.returncode == 0:
            result.status = CodesignStatus.SIGNED
            result.identity = cs_config.identity
            result.message = "Successfully signed"
            logger.success(f"Signed {binary_config.name} with identity '{cs_config.identity}'")
        else:
            result.status = CodesignStatus.FAILED
            result.message = f"codesign failed: {proc.stderr.strip()}"
            result.details = proc.stderr
            logger.error(f"Failed to sign {binary_config.name}: {result.message}")

    except Exception as e:
        result.status = CodesignStatus.FAILED
        result.message = f"Signing error: {e}"
        logger.error(f"Signing error for {binary_config.name}: {e}")

    return result


def codesign_all_binaries(config: TrackConfig, logger: Logger) -> list[CodesignResult]:
    """Sign all binaries that have codesigning enabled."""
    results: list[CodesignResult] = []

    if not is_codesign_available():
        logger.warn("codesign not available (macOS only)")
        return results

    logger.header("Codesigning Binaries")

    for name, binary_config in config.binaries.items():
        if not binary_config.codesign.enabled:
            logger.debug(f"Skipping {name} (codesigning not enabled)")
            continue

        result = codesign_binary(binary_config, config, logger)
        results.append(result)

    # Summary
    if not config.quiet and not config.json_output:
        signed = sum(1 for r in results if r.status == CodesignStatus.SIGNED)
        failed = sum(1 for r in results if r.status == CodesignStatus.FAILED)
        print()
        if signed > 0:
            logger.success(f"Signed {signed} binary(ies)")
        if failed > 0:
            logger.error(f"Failed to sign {failed} binary(ies)")
        if not results:
            logger.info("No binaries configured for codesigning")

    return results


def verify_all_signatures(config: TrackConfig, logger: Logger) -> list[CodesignResult]:
    """Verify codesign signatures of all binaries."""
    results: list[CodesignResult] = []

    if not is_codesign_available():
        logger.warn("codesign not available (macOS only)")
        return results

    logger.header("Signature Verification")

    for name, binary_config in config.binaries.items():
        binary_path = binary_config.get_expanded_install_path()
        result = verify_signature(binary_path, logger)
        result.name = name  # Use config name, not filename
        results.append(result)

        # Display status
        if result.status == CodesignStatus.VALID:
            icon = "✓"
            color = Colors.GREEN
            detail = f"Signed ({result.identity or 'valid'})"
        elif result.status == CodesignStatus.UNSIGNED:
            icon = "○"
            color = Colors.YELLOW
            detail = "Unsigned"
        elif result.status == CodesignStatus.INVALID:
            icon = "✗"
            color = Colors.RED
            detail = f"Invalid: {result.message}"
        else:
            icon = "?"
            color = Colors.GRAY
            detail = result.message

        logger.status_line(icon, color, name, detail)

    # Summary
    if not config.quiet and not config.json_output:
        valid = sum(1 for r in results if r.status == CodesignStatus.VALID)
        unsigned = sum(1 for r in results if r.status == CodesignStatus.UNSIGNED)
        invalid = sum(1 for r in results if r.status == CodesignStatus.INVALID)
        print()
        if valid > 0:
            logger.success(f"{valid} binary(ies) have valid signatures")
        if unsigned > 0:
            logger.warn(f"{unsigned} binary(ies) are unsigned")
        if invalid > 0:
            logger.error(f"{invalid} binary(ies) have invalid signatures")

    return results


def show_install_locations(logger: Logger, json_output: bool = False) -> None:
    """Display default install locations for the current platform."""
    current_platform = get_current_platform()
    locations = get_default_install_locations()

    if json_output:
        output = {
            "platform": current_platform.value,
            "locations": [
                {
                    "path": str(loc.path),
                    "scope": loc.scope.value,
                    "binary_type": loc.binary_type.value,
                    "description": loc.description,
                    "exists": loc.exists(),
                    "writable": loc.is_writable(),
                    "in_path": is_path_in_system_path(loc.path) if loc.binary_type == BinaryType.CLI else None,
                    "requires_admin": loc.requires_admin,
                }
                for loc in locations
            ],
        }
        print(json.dumps(output, indent=2))
        return

    logger.header(f"Default Install Locations ({current_platform.value})")
    print()

    # Group by binary type
    for bt in [BinaryType.CLI, BinaryType.GUI]:
        bt_locations = [loc for loc in locations if loc.binary_type == bt]
        if not bt_locations:
            continue

        type_label = "CLI Tools" if bt == BinaryType.CLI else "GUI Applications"
        print(f"{Colors.BOLD}{type_label}:{Colors.RESET}")
        print()

        for loc in bt_locations:
            scope_label = "[user]  " if loc.scope == InstallScope.USER else "[system]"
            path_str = str(loc.path)

            # Status indicators
            indicators = []
            if loc.exists():
                indicators.append(f"{Colors.GREEN}exists{Colors.RESET}")
            else:
                indicators.append(f"{Colors.GRAY}missing{Colors.RESET}")

            if loc.is_writable():
                indicators.append(f"{Colors.GREEN}writable{Colors.RESET}")
            else:
                indicators.append(f"{Colors.YELLOW}read-only{Colors.RESET}")

            if bt == BinaryType.CLI and is_path_in_system_path(loc.path):
                indicators.append(f"{Colors.GREEN}in PATH{Colors.RESET}")
            elif bt == BinaryType.CLI:
                indicators.append(f"{Colors.YELLOW}not in PATH{Colors.RESET}")

            status_str = ", ".join(indicators)
            print(f"  {scope_label} {path_str}")
            print(f"           {Colors.DIM}{loc.description}{Colors.RESET}")
            print(f"           {status_str}")
            print()

    # Show PATH setup instructions for recommended CLI location
    recommended = get_recommended_install_path(BinaryType.CLI, InstallScope.USER)
    if not is_path_in_system_path(recommended):
        print(f"{Colors.YELLOW}Tip:{Colors.RESET} Recommended CLI path is not in PATH.")
        print(get_path_setup_instructions(recommended))
        print()


def check_all_binaries(config: TrackConfig, logger: Logger) -> TrackResult:
    """Check status of all tracked binaries."""
    result = TrackResult(dry_run=config.dry_run)
    manifest = load_manifest(config.root_dir)

    logger.header("Binary Status")

    shadow_warnings: list[tuple[str, list[ShadowConflict]]] = []

    for name, binary_config in config.binaries.items():
        status = get_binary_status(binary_config, config, manifest, logger)
        result.statuses.append(status)

        # Collect shadow conflicts for later display
        if status.shadow_conflicts:
            shadow_warnings.append((name, status.shadow_conflicts))

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

    # Display shadow warnings
    if shadow_warnings and not config.quiet and not config.json_output:
        print()
        logger.header("Shadow Conflicts")
        print(f"  {Colors.YELLOW}Binaries with the same name exist in multiple locations:{Colors.RESET}")
        print()
        for name, conflicts in shadow_warnings:
            install_path = next(
                (s.install_path for s in result.statuses if s.name == name), ""
            )
            print(f"  {Colors.BOLD}{name}{Colors.RESET} (installed at {install_path})")
            for conflict in conflicts:
                priority = check_shadow_priority(
                    Path(install_path),
                    conflict.path,
                )
                scope_label = f"[{conflict.scope.value}]"
                exec_status = "executable" if conflict.is_executable else "not executable"
                print(f"    {Colors.YELLOW}⚠{Colors.RESET} Also at: {conflict.path}")
                print(f"      {scope_label} {exec_status} - {priority}")
            print()

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

        if shadow_warnings:
            total_conflicts = sum(len(c) for _, c in shadow_warnings)
            logger.warn(f"{total_conflicts} shadow conflict(s) detected - review above")

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
    test_failed_count = sum(1 for r in result.rebuilds if r.status == RebuildStatus.TEST_FAILED)

    if not config.quiet and not config.json_output:
        print()
        if failed_count == 0 and test_failed_count == 0:
            logger.success(f"Successfully rebuilt {success_count} binary(ies)")
        else:
            parts = [f"{success_count} succeeded"]
            if failed_count > 0:
                parts.append(f"{failed_count} failed")
            if test_failed_count > 0:
                parts.append(f"{test_failed_count} tests failed")
            logger.warn(", ".join(parts))

            # Show suggestions for failures
            for r in result.rebuilds:
                if r.status in (RebuildStatus.FAILED, RebuildStatus.TEST_FAILED) and r.suggestion:
                    logger.info(f"  💡 {r.name}: {r.suggestion}")

    result.stale_count = failed_count + test_failed_count
    result.all_current = failed_count == 0 and test_failed_count == 0

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
        def on_modified(self, event: Any) -> None:
            if event.is_directory:
                return
            self._handle_change(event.src_path)

        def on_created(self, event: Any) -> None:
            if event.is_directory:
                return
            self._handle_change(event.src_path)

        def _handle_change(self, file_path: str) -> None:
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

    def rebuild_worker() -> None:
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
            file_config = yaml.safe_load(f) or {}
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
        yaml.dump(file_config, f, default_flow_style=False, sort_keys=False)

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
  binary-track --codesign         # Sign all binaries with codesigning enabled
  binary-track --verify-signature # Verify codesign signatures
  binary-track --add              # Interactively add a new binary
  binary-track --remove mytool    # Remove a binary from tracking

Configuration:
  Create .binariesrc.yaml in your project root with binary definitions.

Tracking Methods:
  - git_commit: Track source by git commit (recommended)
  - mtime: Track source file modification times
  - hash: Track source file content hashes

Codesigning (macOS):
  Enable per-binary or globally in config:
    "codesign": {"enabled": true, "identity": "-"}
  Use identity "-" for ad-hoc signing, or specify a certificate name.

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
        help="Path to configuration file (default: .binariesrc.yaml)",
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
    action_group.add_argument(
        "--codesign",
        action="store_true",
        help="Sign all binaries that have codesigning enabled (macOS)",
    )
    action_group.add_argument(
        "--verify-signature",
        action="store_true",
        help="Verify codesign signatures of all binaries (macOS)",
    )
    action_group.add_argument(
        "--show-paths",
        action="store_true",
        help="Show default install locations for current platform",
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
    except yaml.YAMLError as e:
        print(f"{Colors.RED}Error:{Colors.RESET} Invalid YAML in config file: {e}", file=sys.stderr)
        return 1

    env_config = load_env_config()
    merged_config: ConfigDict = {**file_config, **env_config}
    config = TrackConfig.from_dict(merged_config, root_dir)

    # Set global config for binary path resolution
    set_global_track_config(config)

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

    # Handle --show-paths early (doesn't require binaries config)
    if args.show_paths:
        show_install_locations(logger, config.json_output)
        return 0

    # Check if we have any binaries configured (for most actions)
    if not config.binaries and not args.add:
        if not args.json_output:
            logger.info("No binaries configured. Use --add to add a binary or create .binariesrc.yaml")
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
            print(yaml.dump(example, default_flow_style=False, sort_keys=False))
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

        elif args.codesign:
            results = codesign_all_binaries(config, logger)
            if config.json_output:
                output = [
                    {"name": r.name, "status": r.status.value, "identity": r.identity, "message": r.message}
                    for r in results
                ]
                print(json.dumps(output, indent=2))
            failed = sum(1 for r in results if r.status == CodesignStatus.FAILED)
            return 1 if failed > 0 else 0

        elif args.verify_signature:
            results = verify_all_signatures(config, logger)
            if config.json_output:
                output = [
                    {"name": r.name, "status": r.status.value, "identity": r.identity, "message": r.message}
                    for r in results
                ]
                print(json.dumps(output, indent=2))
            invalid = sum(1 for r in results if r.status in (CodesignStatus.INVALID, CodesignStatus.UNSIGNED))
            return 1 if invalid > 0 else 0

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
