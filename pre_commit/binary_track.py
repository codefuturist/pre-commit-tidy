"""Binary Track - Keep locally-installed binaries up to date with source code.

A simple tool for pre-commit that detects when source files change and notifies
developers that their locally-built binaries need to be rebuilt.

Usage:
    binary-track --check-staged --project=apps/cli/mytool [files...]
    binary-track --rebuild --project=apps/cli/mytool
    binary-track --health --project=apps/cli/mytool

Key Features:
    --check-staged    Check if staged files affect any tracked binary (pre-commit)
    --project         Auto-detect project: binary name from dir, language from files
    --rebuild         Rebuild binaries (runs build command)
    --health          Check binary health (exists, executable, in PATH)
    --codesign        Sign binaries after building (macOS)
    --verify-signature Verify codesigning status

Examples:
    # Simple: auto-detect everything from project path
    binary-track --check-staged --project=apps/cli/mytool src/main.rs

    # Monorepo: multiple projects
    binary-track --check-staged --project=services/api --project=tools/cli

    # Explicit configuration
    binary-track --check-staged --binary=mytool --language=rust

    # Pre-commit integration
    - repo: local
      hooks:
        - id: binary-track
          entry: binary-track --check-staged --project=apps/cli/mytool
          language: system
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from pathlib import Path
from typing import Any
from typing import TypedDict

__version__ = "3.0.0"

# Default config file name
DEFAULT_CONFIG_FILE = ".binariesrc.yaml"


# =============================================================================
# Language Presets
# =============================================================================


@dataclass
class LanguagePreset:
    """Preset configuration for a programming language."""

    name: str
    source_patterns: list[str]
    default_build_cmd: str
    default_install_path_template: str
    file_extensions: list[str]
    description: str

    def get_build_cmd(self, name: str, install_path: str | None = None) -> str:
        """Get build command with placeholders replaced."""
        path = install_path or self.default_install_path_template.format(name=name)
        return self.default_build_cmd.format(name=name, install_path=path)

    def get_install_path(self, name: str) -> str:
        """Get default install path for this language."""
        return self.default_install_path_template.format(name=name)


LANGUAGE_PRESETS: dict[str, LanguagePreset] = {
    "go": LanguagePreset(
        name="go",
        source_patterns=["**/*.go", "go.mod", "go.sum"],
        default_build_cmd="go build -o {install_path} ./cmd/{name}",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".go"],
        description="Go (Golang)",
    ),
    "rust": LanguagePreset(
        name="rust",
        source_patterns=["**/*.rs", "Cargo.toml", "Cargo.lock"],
        default_build_cmd="cargo build --release && cp target/release/{name} {install_path}",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".rs"],
        description="Rust",
    ),
    "python": LanguagePreset(
        name="python",
        source_patterns=["**/*.py", "pyproject.toml", "setup.py", "setup.cfg"],
        default_build_cmd="pip install --user -e .",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".py"],
        description="Python",
    ),
    "uv": LanguagePreset(
        name="uv",
        source_patterns=["**/*.py", "pyproject.toml", "uv.lock"],
        default_build_cmd="uv tool install --force -e .",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".py"],
        description="Python (uv)",
    ),
    "node": LanguagePreset(
        name="node",
        source_patterns=["**/*.ts", "**/*.js", "package.json", "package-lock.json"],
        default_build_cmd="npm run build && npm link",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".ts", ".js"],
        description="Node.js / TypeScript",
    ),
    "pnpm": LanguagePreset(
        name="pnpm",
        source_patterns=["**/*.ts", "**/*.js", "package.json", "pnpm-lock.yaml"],
        default_build_cmd="pnpm run build && pnpm link --global",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".ts", ".js"],
        description="Node.js / TypeScript (pnpm)",
    ),
    "swift": LanguagePreset(
        name="swift",
        source_patterns=["**/*.swift", "Package.swift"],
        default_build_cmd="swift build -c release && cp .build/release/{name} {install_path}",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".swift"],
        description="Swift",
    ),
    "swift-app": LanguagePreset(
        name="swift-app",
        source_patterns=["**/*.swift", "**/*.xcodeproj/**", "**/*.xcworkspace/**"],
        default_build_cmd="xcodebuild -scheme {name} -configuration Release -archivePath build/{name}.xcarchive archive",
        default_install_path_template="~/Applications/{name}.app",
        file_extensions=[".swift"],
        description="Swift macOS/iOS App",
    ),
    "c": LanguagePreset(
        name="c",
        source_patterns=["**/*.c", "**/*.h", "Makefile", "CMakeLists.txt"],
        default_build_cmd="make && cp {name} {install_path}",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".c", ".h"],
        description="C",
    ),
    "cpp": LanguagePreset(
        name="cpp",
        source_patterns=["**/*.cpp", "**/*.cc", "**/*.cxx", "**/*.hpp", "**/*.h", "Makefile", "CMakeLists.txt"],
        default_build_cmd="cmake -B build && cmake --build build --config Release && cp build/{name} {install_path}",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".cpp", ".cc", ".cxx", ".hpp"],
        description="C++",
    ),
    "zig": LanguagePreset(
        name="zig",
        source_patterns=["**/*.zig", "build.zig"],
        default_build_cmd="zig build -Doptimize=ReleaseFast && cp zig-out/bin/{name} {install_path}",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".zig"],
        description="Zig",
    ),
    "haskell": LanguagePreset(
        name="haskell",
        source_patterns=["**/*.hs", "**/*.cabal", "stack.yaml"],
        default_build_cmd="stack build && stack install --local-bin-path $(dirname {install_path})",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".hs"],
        description="Haskell",
    ),
    "elixir": LanguagePreset(
        name="elixir",
        source_patterns=["**/*.ex", "**/*.exs", "mix.exs"],
        default_build_cmd="mix escript.build && cp {name} {install_path}",
        default_install_path_template="~/.local/bin/{name}",
        file_extensions=[".ex", ".exs"],
        description="Elixir",
    ),
}


def get_language_preset(language: str) -> LanguagePreset | None:
    """Get a language preset by name."""
    return LANGUAGE_PRESETS.get(language.lower())


def list_language_presets() -> list[tuple[str, str]]:
    """List available language presets as (name, description) tuples."""
    return [(name, preset.description) for name, preset in sorted(LANGUAGE_PRESETS.items())]


def detect_project_language(project_path: Path) -> str | None:
    """Auto-detect programming language from project files."""
    detection_rules: list[tuple[str, str]] = [
        ("Package.swift", "swift"),
        ("Cargo.toml", "rust"),
        ("go.mod", "go"),
        ("uv.lock", "uv"),
        ("pyproject.toml", "python"),
        ("setup.py", "python"),
        ("pnpm-lock.yaml", "pnpm"),
        ("package-lock.json", "node"),
        ("package.json", "node"),
        ("CMakeLists.txt", "cpp"),
        ("Makefile", "c"),
        ("build.zig", "zig"),
        ("stack.yaml", "haskell"),
        ("mix.exs", "elixir"),
    ]

    for marker_file, language in detection_rules:
        if (project_path / marker_file).exists():
            if language == "swift":
                xcodeproj_dirs = list(project_path.glob("*.xcodeproj"))
                xcworkspace_dirs = list(project_path.glob("*.xcworkspace"))
                if xcodeproj_dirs or xcworkspace_dirs:
                    return "swift-app"
            return language

    return None


def derive_binary_name(project_path: Path) -> str:
    """Derive binary name from project directory name."""
    return project_path.name


# =============================================================================
# Enums
# =============================================================================


class PreCommitPolicy(Enum):
    """Policy for pre-commit behavior when binaries are stale."""

    WARN = "warn"
    BLOCK = "block"
    IGNORE = "ignore"


class BinaryStatus(Enum):
    """Status of a binary."""

    UP_TO_DATE = "up_to_date"
    STALE = "stale"
    MISSING = "missing"
    NOT_EXECUTABLE = "not_executable"
    UNKNOWN = "unknown"


class CodesignStatus(Enum):
    """Status of code signature."""

    SIGNED = "signed"
    NOT_SIGNED = "not_signed"
    INVALID = "invalid"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"


# =============================================================================
# Type Definitions
# =============================================================================


class CodesignConfigDict(TypedDict, total=False):
    """Codesigning configuration dictionary."""

    enabled: bool
    identity: str
    entitlements: str | None
    options: list[str]
    force: bool


class BinaryConfigDict(TypedDict, total=False):
    """Binary configuration dictionary."""

    source_patterns: list[str]
    build_cmd: str
    install_path: str
    language: str
    working_dir: str
    test_cmd: str
    codesign: CodesignConfigDict


class ConfigDict(TypedDict, total=False):
    """Top-level configuration dictionary."""

    binaries: dict[str, BinaryConfigDict]
    codesign: CodesignConfigDict
    pre_commit_policy: str


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class CodesignConfig:
    """Codesigning configuration."""

    enabled: bool = False
    identity: str = "-"
    entitlements: str | None = None
    options: list[str] = field(default_factory=lambda: ["runtime"])
    force: bool = True

    @classmethod
    def from_dict(cls, data: CodesignConfigDict | None) -> CodesignConfig:
        """Create from dictionary."""
        if not data:
            return cls()
        return cls(
            enabled=data.get("enabled", False),
            identity=data.get("identity", "-"),
            entitlements=data.get("entitlements"),
            options=data.get("options", ["runtime"]),
            force=data.get("force", True),
        )


@dataclass
class BinaryConfig:
    """Configuration for a single binary."""

    name: str
    source_patterns: list[str] = field(default_factory=list)
    build_cmd: str = ""
    install_path: str = ""
    language: str | None = None
    working_dir: str = "."
    test_cmd: str | None = None
    codesign: CodesignConfig = field(default_factory=CodesignConfig)

    @classmethod
    def from_dict(cls, name: str, data: BinaryConfigDict) -> BinaryConfig:
        """Create from dictionary."""
        return cls(
            name=name,
            source_patterns=data.get("source_patterns", []),
            build_cmd=data.get("build_cmd", ""),
            install_path=data.get("install_path", ""),
            language=data.get("language"),
            working_dir=data.get("working_dir", "."),
            test_cmd=data.get("test_cmd"),
            codesign=CodesignConfig.from_dict(data.get("codesign")),
        )

    def get_expanded_install_path(self) -> Path:
        """Get install path with ~ expanded."""
        return Path(os.path.expanduser(self.install_path))


@dataclass
class TrackConfig:
    """Main configuration for binary tracking."""

    root_dir: Path
    binaries: dict[str, BinaryConfig] = field(default_factory=dict)
    codesign: CodesignConfig = field(default_factory=CodesignConfig)
    pre_commit_policy: PreCommitPolicy = PreCommitPolicy.WARN

    @classmethod
    def from_dict(cls, data: ConfigDict, root_dir: Path | None = None) -> TrackConfig:
        """Create from dictionary."""
        if root_dir is None:
            root_dir = Path.cwd()

        binaries: dict[str, BinaryConfig] = {}
        for name, binary_data in data.get("binaries", {}).items():
            binaries[name] = BinaryConfig.from_dict(name, binary_data)

        policy_str = data.get("pre_commit_policy", "warn")
        try:
            policy = PreCommitPolicy(policy_str)
        except ValueError:
            policy = PreCommitPolicy.WARN

        return cls(
            root_dir=root_dir,
            binaries=binaries,
            codesign=CodesignConfig.from_dict(data.get("codesign")),
            pre_commit_policy=policy,
        )


# =============================================================================
# Result Classes
# =============================================================================


@dataclass
class HealthResult:
    """Result of a binary health check."""

    name: str
    exists: bool = False
    executable: bool = False
    in_path: bool = False
    install_path: str = ""
    status: BinaryStatus = BinaryStatus.UNKNOWN
    message: str = ""

    def is_healthy(self) -> bool:
        """Check if binary is healthy."""
        return self.exists and self.executable


@dataclass
class CodesignResult:
    """Result of a codesigning operation."""

    name: str
    success: bool = False
    status: CodesignStatus = CodesignStatus.NOT_APPLICABLE
    message: str = ""


@dataclass
class StagedCheckResult:
    """Result of checking staged files."""

    affected_binaries: list[str] = field(default_factory=list)
    file_matches: dict[str, list[str]] = field(default_factory=dict)
    total_files_checked: int = 0

    @property
    def has_affected(self) -> bool:
        """Check if any binaries are affected."""
        return len(self.affected_binaries) > 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "affected_binaries": self.affected_binaries,
            "file_matches": self.file_matches,
            "total_files_checked": self.total_files_checked,
            "has_affected": self.has_affected,
        }


@dataclass
class RebuildResult:
    """Result of rebuilding a binary."""

    name: str
    success: bool = False
    message: str = ""
    duration_seconds: float = 0.0


# =============================================================================
# Logging
# =============================================================================


class Colors:
    """ANSI color codes."""

    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls) -> None:
        """Disable colors."""
        cls.RED = ""
        cls.GREEN = ""
        cls.YELLOW = ""
        cls.BLUE = ""
        cls.CYAN = ""
        cls.BOLD = ""
        cls.RESET = ""


class Logger:
    """Simple logger with verbosity control."""

    def __init__(self, verbose: bool = False, quiet: bool = False) -> None:
        self.verbose = verbose
        self.quiet = quiet

    def info(self, message: str) -> None:
        """Log info message."""
        if not self.quiet:
            print(message)

    def success(self, message: str) -> None:
        """Log success message."""
        if not self.quiet:
            print(f"{Colors.GREEN}✓{Colors.RESET} {message}")

    def warn(self, message: str) -> None:
        """Log warning message."""
        if not self.quiet:
            print(f"{Colors.YELLOW}⚠{Colors.RESET} {message}")

    def error(self, message: str) -> None:
        """Log error message."""
        print(f"{Colors.RED}✗{Colors.RESET} {message}", file=sys.stderr)

    def debug(self, message: str) -> None:
        """Log debug message (only if verbose)."""
        if self.verbose:
            print(f"{Colors.BLUE}→{Colors.RESET} {message}")


# =============================================================================
# Configuration Loading
# =============================================================================


def load_config_file(config_path: Path | None = None, root_dir: Path | None = None) -> ConfigDict:
    """Load configuration from YAML file."""
    if root_dir is None:
        root_dir = Path.cwd()

    if config_path is None:
        config_path = root_dir / DEFAULT_CONFIG_FILE

    if not config_path.exists():
        return {"binaries": {}}

    try:
        import yaml

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return data
    except ImportError:
        return {"binaries": {}}
    except Exception:
        return {"binaries": {}}


def merge_configs(*configs: ConfigDict) -> ConfigDict:
    """Merge multiple configurations, later ones override earlier."""
    result: ConfigDict = {"binaries": {}}

    for config in configs:
        # Merge binaries
        for name, binary_config in config.get("binaries", {}).items():
            if name not in result["binaries"]:
                result["binaries"][name] = {}
            result["binaries"][name].update(binary_config)

        # Merge top-level settings
        if "pre_commit_policy" in config:
            result["pre_commit_policy"] = config["pre_commit_policy"]
        if "codesign" in config:
            result["codesign"] = config["codesign"]

    return result


# =============================================================================
# File Pattern Matching
# =============================================================================


def file_matches_pattern(file_path: str, pattern: str, root_dir: Path) -> bool:
    """Check if a file path matches a glob pattern."""
    file_path_normalized = file_path.replace("\\", "/").lstrip("/")

    if pattern.startswith("**/"):
        suffix_pattern = pattern[3:]
        if fnmatch.fnmatch(file_path_normalized, pattern):
            return True
        if fnmatch.fnmatch(Path(file_path_normalized).name, suffix_pattern):
            return True
        parts = file_path_normalized.split("/")
        for i in range(len(parts)):
            partial = "/".join(parts[i:])
            if fnmatch.fnmatch(partial, suffix_pattern):
                return True
            if fnmatch.fnmatch(partial, pattern):
                return True
        return False
    elif "**" in pattern:
        return fnmatch.fnmatch(file_path_normalized, pattern)
    else:
        if fnmatch.fnmatch(file_path_normalized, pattern):
            return True
        if fnmatch.fnmatch(Path(file_path_normalized).name, pattern):
            return True
        return False


# =============================================================================
# Core Functions: Check Staged Files
# =============================================================================


def check_staged_files(
    config: TrackConfig,
    logger: Logger,
    files: list[str],
) -> StagedCheckResult:
    """Check if staged files affect any tracked binaries."""
    result = StagedCheckResult(total_files_checked=len(files))

    for binary_name, binary_config in config.binaries.items():
        matched_files: list[str] = []

        # Determine project directory scope
        project_dir = config.root_dir
        if binary_config.working_dir and binary_config.working_dir != ".":
            project_dir = config.root_dir / binary_config.working_dir

        for file_path in files:
            # Normalize file path
            file_path_normalized = file_path.replace("\\", "/").lstrip("/")

            # Check if file is within the binary's project directory
            if binary_config.working_dir and binary_config.working_dir != ".":
                working_dir_normalized = binary_config.working_dir.replace("\\", "/").rstrip("/")
                if not file_path_normalized.startswith(working_dir_normalized + "/"):
                    continue

                # Get relative path within project
                relative_path = file_path_normalized[len(working_dir_normalized) + 1:]
            else:
                relative_path = file_path_normalized

            # Check against source patterns
            for pattern in binary_config.source_patterns:
                if file_matches_pattern(relative_path, pattern, project_dir):
                    matched_files.append(file_path)
                    break

        if matched_files:
            result.affected_binaries.append(binary_name)
            result.file_matches[binary_name] = matched_files
            logger.debug(f"{binary_name}: {len(matched_files)} file(s) matched")

    return result


def display_staged_check_result(
    result: StagedCheckResult,
    config: TrackConfig,
    logger: Logger,
    json_output: bool = False,
) -> None:
    """Display the result of checking staged files."""
    if json_output:
        print(json.dumps(result.to_dict(), indent=2))
        return

    if not result.has_affected:
        logger.success(f"No binaries affected by {result.total_files_checked} staged file(s)")
        return

    print(f"\n{Colors.BOLD}=== Binaries Need Rebuild ==={Colors.RESET}")
    for binary_name in result.affected_binaries:
        count = len(result.file_matches.get(binary_name, []))
        print(f"  {Colors.YELLOW}⚠{Colors.RESET} {binary_name} - {count} file(s) changed")

    # Show rebuild hint
    if len(result.affected_binaries) == 1:
        print(f"\n{Colors.CYAN}ℹ{Colors.RESET} Rebuild with: binary-track --binary={result.affected_binaries[0]} --rebuild")
    else:
        print(f"\n{Colors.CYAN}ℹ{Colors.RESET} Rebuild all with: binary-track --rebuild")

    # Policy message
    if config.pre_commit_policy == PreCommitPolicy.BLOCK:
        print(f"{Colors.RED}✗ Commit blocked until binaries are rebuilt{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ Commit will proceed, but binaries need rebuild{Colors.RESET}")


# =============================================================================
# Core Functions: Health Check
# =============================================================================


def check_binary_health(binary_config: BinaryConfig, logger: Logger) -> HealthResult:
    """Check the health of a binary."""
    result = HealthResult(
        name=binary_config.name,
        install_path=binary_config.install_path,
    )

    if not binary_config.install_path:
        result.status = BinaryStatus.UNKNOWN
        result.message = "No install path configured"
        return result

    install_path = binary_config.get_expanded_install_path()
    result.exists = install_path.exists()

    if not result.exists:
        result.status = BinaryStatus.MISSING
        result.message = f"Binary not found at {install_path}"
        return result

    # Check if executable
    result.executable = os.access(install_path, os.X_OK)
    if not result.executable:
        result.status = BinaryStatus.NOT_EXECUTABLE
        result.message = "Binary exists but is not executable"
        return result

    # Check if in PATH
    binary_name = install_path.name
    which_result = shutil.which(binary_name)
    result.in_path = which_result is not None

    result.status = BinaryStatus.UP_TO_DATE
    result.message = "Binary is healthy"

    return result


def display_health_results(results: list[HealthResult], logger: Logger, json_output: bool = False) -> None:
    """Display health check results."""
    if json_output:
        output = [
            {
                "name": r.name,
                "exists": r.exists,
                "executable": r.executable,
                "in_path": r.in_path,
                "install_path": r.install_path,
                "status": r.status.value,
                "message": r.message,
            }
            for r in results
        ]
        print(json.dumps(output, indent=2))
        return

    print(f"\n{Colors.BOLD}=== Binary Health Check ==={Colors.RESET}")
    for result in results:
        if result.is_healthy():
            status_icon = f"{Colors.GREEN}✓{Colors.RESET}"
            path_status = f"{Colors.GREEN}in PATH{Colors.RESET}" if result.in_path else f"{Colors.YELLOW}not in PATH{Colors.RESET}"
            print(f"  {status_icon} {result.name}: healthy ({path_status})")
        elif result.status == BinaryStatus.MISSING:
            print(f"  {Colors.RED}✗{Colors.RESET} {result.name}: {Colors.RED}missing{Colors.RESET}")
        elif result.status == BinaryStatus.NOT_EXECUTABLE:
            print(f"  {Colors.YELLOW}⚠{Colors.RESET} {result.name}: {Colors.YELLOW}not executable{Colors.RESET}")
        else:
            print(f"  {Colors.YELLOW}?{Colors.RESET} {result.name}: {result.message}")


# =============================================================================
# Core Functions: Rebuild
# =============================================================================


def rebuild_binary(
    binary_config: BinaryConfig,
    root_dir: Path,
    logger: Logger,
    dry_run: bool = False,
) -> RebuildResult:
    """Rebuild a single binary."""
    import time

    result = RebuildResult(name=binary_config.name)

    if not binary_config.build_cmd:
        result.message = "No build command configured"
        logger.warn(f"{binary_config.name}: No build command configured")
        return result

    # Determine working directory
    work_dir = root_dir
    if binary_config.working_dir and binary_config.working_dir != ".":
        work_dir = root_dir / binary_config.working_dir

    if dry_run:
        logger.info(f"Would run: {binary_config.build_cmd}")
        logger.info(f"  in: {work_dir}")
        result.success = True
        result.message = "Dry run - no changes made"
        return result

    logger.info(f"Building {binary_config.name}...")
    logger.debug(f"Command: {binary_config.build_cmd}")
    logger.debug(f"Working dir: {work_dir}")

    start_time = time.time()

    try:
        proc = subprocess.run(
            binary_config.build_cmd,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
        )

        result.duration_seconds = time.time() - start_time

        if proc.returncode == 0:
            result.success = True
            result.message = f"Built successfully in {result.duration_seconds:.1f}s"
            logger.success(f"{binary_config.name}: {result.message}")

            # Run test command if configured
            if binary_config.test_cmd:
                logger.debug(f"Running test: {binary_config.test_cmd}")
                test_proc = subprocess.run(
                    binary_config.test_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                )
                if test_proc.returncode != 0:
                    result.success = False
                    result.message = f"Build succeeded but test failed: {test_proc.stderr}"
                    logger.error(f"{binary_config.name}: Test failed")
        else:
            result.message = f"Build failed: {proc.stderr}"
            logger.error(f"{binary_config.name}: Build failed")
            if logger.verbose:
                logger.error(proc.stderr)

    except Exception as e:
        result.message = f"Error: {e}"
        logger.error(f"{binary_config.name}: {e}")

    return result


def rebuild_all(
    config: TrackConfig,
    logger: Logger,
    dry_run: bool = False,
    binary_names: list[str] | None = None,
) -> list[RebuildResult]:
    """Rebuild all (or specified) binaries."""
    results: list[RebuildResult] = []

    binaries_to_build = binary_names or list(config.binaries.keys())

    for name in binaries_to_build:
        if name not in config.binaries:
            logger.warn(f"Unknown binary: {name}")
            continue

        binary_config = config.binaries[name]
        result = rebuild_binary(binary_config, config.root_dir, logger, dry_run)
        results.append(result)

    return results


# =============================================================================
# Core Functions: Codesigning
# =============================================================================


def is_codesign_available() -> bool:
    """Check if codesign is available (macOS only)."""
    return shutil.which("codesign") is not None


def codesign_binary(
    binary_config: BinaryConfig,
    global_codesign: CodesignConfig,
    logger: Logger,
    dry_run: bool = False,
) -> CodesignResult:
    """Sign a binary with codesign."""
    result = CodesignResult(name=binary_config.name)

    if sys.platform != "darwin":
        result.status = CodesignStatus.NOT_APPLICABLE
        result.message = "Codesigning is only available on macOS"
        return result

    if not is_codesign_available():
        result.status = CodesignStatus.ERROR
        result.message = "codesign command not found"
        return result

    # Determine codesign config (binary-specific overrides global)
    codesign_config = binary_config.codesign if binary_config.codesign.enabled else global_codesign

    if not codesign_config.enabled:
        result.status = CodesignStatus.NOT_APPLICABLE
        result.message = "Codesigning not enabled"
        return result

    install_path = binary_config.get_expanded_install_path()

    if not install_path.exists():
        result.status = CodesignStatus.ERROR
        result.message = f"Binary not found: {install_path}"
        return result

    # Build codesign command
    cmd = ["codesign"]
    if codesign_config.force:
        cmd.append("--force")
    cmd.extend(["--sign", codesign_config.identity])

    for option in codesign_config.options:
        cmd.extend(["--options", option])

    if codesign_config.entitlements:
        entitlements_path = Path(codesign_config.entitlements).expanduser()
        if entitlements_path.exists():
            cmd.extend(["--entitlements", str(entitlements_path)])

    cmd.append(str(install_path))

    if dry_run:
        logger.info(f"Would run: {' '.join(cmd)}")
        result.success = True
        result.message = "Dry run"
        return result

    logger.debug(f"Signing {binary_config.name}: {' '.join(cmd)}")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode == 0:
            result.success = True
            result.status = CodesignStatus.SIGNED
            result.message = "Signed successfully"
            logger.success(f"{binary_config.name}: Signed")
        else:
            result.status = CodesignStatus.ERROR
            result.message = f"Signing failed: {proc.stderr}"
            logger.error(f"{binary_config.name}: Signing failed - {proc.stderr}")

    except Exception as e:
        result.status = CodesignStatus.ERROR
        result.message = str(e)
        logger.error(f"{binary_config.name}: {e}")

    return result


def verify_signature(binary_config: BinaryConfig, logger: Logger) -> CodesignResult:
    """Verify a binary's code signature."""
    result = CodesignResult(name=binary_config.name)

    if sys.platform != "darwin":
        result.status = CodesignStatus.NOT_APPLICABLE
        result.message = "Codesigning is only available on macOS"
        return result

    install_path = binary_config.get_expanded_install_path()

    if not install_path.exists():
        result.status = CodesignStatus.ERROR
        result.message = f"Binary not found: {install_path}"
        return result

    try:
        proc = subprocess.run(
            ["codesign", "--verify", "--verbose=2", str(install_path)],
            capture_output=True,
            text=True,
        )

        if proc.returncode == 0:
            result.success = True
            result.status = CodesignStatus.SIGNED
            result.message = "Valid signature"
        else:
            if "not signed" in proc.stderr.lower():
                result.status = CodesignStatus.NOT_SIGNED
                result.message = "Not signed"
            else:
                result.status = CodesignStatus.INVALID
                result.message = f"Invalid signature: {proc.stderr}"

    except Exception as e:
        result.status = CodesignStatus.ERROR
        result.message = str(e)

    return result


def codesign_all(
    config: TrackConfig,
    logger: Logger,
    dry_run: bool = False,
) -> list[CodesignResult]:
    """Sign all binaries that have codesigning enabled."""
    results: list[CodesignResult] = []

    for name, binary_config in config.binaries.items():
        if binary_config.codesign.enabled or config.codesign.enabled:
            result = codesign_binary(binary_config, config.codesign, logger, dry_run)
            results.append(result)

    return results


def verify_all_signatures(config: TrackConfig, logger: Logger) -> list[CodesignResult]:
    """Verify signatures of all binaries."""
    results: list[CodesignResult] = []

    for name, binary_config in config.binaries.items():
        result = verify_signature(binary_config, logger)
        results.append(result)

    return results


# =============================================================================
# CLI Argument Parsing
# =============================================================================


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="binary-track",
        description="Track locally-built binaries and detect when they need rebuild.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Smart project detection (recommended)
  binary-track --check-staged --project=apps/cli/mytool

  # Multiple projects in monorepo
  binary-track --check-staged --project=services/api --project=tools/cli

  # Explicit binary configuration
  binary-track --check-staged --binary=mytool --language=rust

  # Rebuild binaries
  binary-track --rebuild --project=apps/cli/mytool

  # Health check
  binary-track --health --project=apps/cli/mytool

  # Codesign (macOS)
  binary-track --codesign --project=apps/cli/mytool

Pre-commit Integration:
  - repo: local
    hooks:
      - id: binary-track
        name: Check if binaries need rebuild
        entry: binary-track --check-staged --project=apps/cli/mytool
        language: system
""",
    )

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="Path to configuration file")

    # Actions
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--check-staged", action="store_true", help="Check if staged files affect any binary")
    action_group.add_argument("--rebuild", action="store_true", help="Rebuild binaries")
    action_group.add_argument("--health", action="store_true", help="Check binary health")
    action_group.add_argument("--codesign", action="store_true", help="Sign binaries (macOS)")
    action_group.add_argument("--verify-signature", action="store_true", help="Verify code signatures")
    action_group.add_argument("--list-languages", action="store_true", help="List available language presets")

    # Project/Binary configuration
    config_group = parser.add_argument_group("configuration")
    config_group.add_argument(
        "--project",
        action="append",
        metavar="PATH",
        dest="projects",
        help="Project path (auto-detects binary name and language)",
    )
    config_group.add_argument(
        "--binary",
        action="append",
        metavar="NAME",
        dest="binaries",
        help="Binary name (explicit configuration)",
    )
    config_group.add_argument(
        "--language",
        action="append",
        metavar="LANG",
        dest="languages",
        help="Language preset for preceding --binary",
    )
    config_group.add_argument(
        "--project-dir",
        action="append",
        metavar="DIR",
        dest="project_dirs",
        help="Project directory for preceding --binary",
    )
    config_group.add_argument(
        "--policy",
        choices=["warn", "block", "ignore"],
        help="Pre-commit policy",
    )

    # Options
    parser.add_argument("--dry-run", action="store_true", help="Preview without executing")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress output")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")

    # Positional (staged files from pre-commit)
    parser.add_argument("files", nargs="*", help="Files to check")

    return parser.parse_args(argv)


def build_config_from_args(args: argparse.Namespace, root_dir: Path) -> ConfigDict:
    """Build configuration from CLI arguments."""
    config: ConfigDict = {"binaries": {}}

    # Handle --project arguments (smart auto-detection)
    projects = getattr(args, "projects", None) or []
    for project_path_str in projects:
        project_path = Path(project_path_str)
        name = derive_binary_name(project_path)

        full_project_path = project_path if project_path.is_absolute() else root_dir / project_path
        language = detect_project_language(full_project_path)

        binary_config: BinaryConfigDict = {"working_dir": project_path_str}

        if language:
            binary_config["language"] = language
            preset = get_language_preset(language)
            if preset:
                binary_config["source_patterns"] = preset.source_patterns
                binary_config["build_cmd"] = preset.get_build_cmd(name)
                binary_config["install_path"] = preset.get_install_path(name)

        config["binaries"][name] = binary_config

    # Handle --binary arguments (explicit)
    binaries = getattr(args, "binaries", None) or []
    languages = getattr(args, "languages", None) or []
    project_dirs = getattr(args, "project_dirs", None) or []

    for i, name in enumerate(binaries):
        binary_config: BinaryConfigDict = {}

        language = languages[i] if i < len(languages) else None
        project_dir = project_dirs[i] if i < len(project_dirs) else None

        if project_dir:
            binary_config["working_dir"] = project_dir

        if language:
            binary_config["language"] = language
            preset = get_language_preset(language)
            if preset:
                binary_config["source_patterns"] = preset.source_patterns
                binary_config["build_cmd"] = preset.get_build_cmd(name)
                binary_config["install_path"] = preset.get_install_path(name)

        config["binaries"][name] = binary_config

    # Global settings
    if args.policy:
        config["pre_commit_policy"] = args.policy

    return config


# =============================================================================
# Main Entry Point
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)

    # Handle --list-languages
    if args.list_languages:
        print(f"\n{Colors.BOLD}Available Language Presets{Colors.RESET}\n")
        for name, description in list_language_presets():
            preset = get_language_preset(name)
            if preset:
                print(f"  {Colors.CYAN}{name:12}{Colors.RESET} {description}")
                print(f"              Patterns: {', '.join(preset.source_patterns[:3])}")
                print()
        return 0

    # Setup
    root_dir = Path.cwd()
    logger = Logger(verbose=args.verbose, quiet=args.quiet)

    if not sys.stdout.isatty():
        Colors.disable()

    # Load configuration
    file_config = load_config_file(args.config, root_dir)
    cli_config = build_config_from_args(args, root_dir)
    merged_config = merge_configs(file_config, cli_config)
    config = TrackConfig.from_dict(merged_config, root_dir)

    # Check we have binaries configured
    if not config.binaries:
        if args.check_staged:
            # No binaries configured, nothing to check
            logger.success("No binaries configured to track")
            return 0
        logger.error("No binaries configured. Use --project or --binary to specify binaries.")
        return 2

    # Execute action
    if args.check_staged:
        files = args.files or []
        result = check_staged_files(config, logger, files)
        display_staged_check_result(result, config, logger, args.json_output)

        if result.has_affected:
            if config.pre_commit_policy == PreCommitPolicy.BLOCK:
                return 1
            return 0
        return 0

    elif args.rebuild:
        results = rebuild_all(config, logger, args.dry_run)
        if args.json_output:
            output = [{"name": r.name, "success": r.success, "message": r.message} for r in results]
            print(json.dumps(output, indent=2))
        failures = [r for r in results if not r.success]
        return 1 if failures else 0

    elif args.health:
        results = [check_binary_health(bc, logger) for bc in config.binaries.values()]
        display_health_results(results, logger, args.json_output)
        unhealthy = [r for r in results if not r.is_healthy()]
        return 1 if unhealthy else 0

    elif args.codesign:
        if sys.platform != "darwin":
            logger.error("Codesigning is only available on macOS")
            return 1
        results = codesign_all(config, logger, args.dry_run)
        if args.json_output:
            output = [{"name": r.name, "success": r.success, "status": r.status.value, "message": r.message} for r in results]
            print(json.dumps(output, indent=2))
        failures = [r for r in results if not r.success and r.status != CodesignStatus.NOT_APPLICABLE]
        return 1 if failures else 0

    elif args.verify_signature:
        results = verify_all_signatures(config, logger)
        if args.json_output:
            output = [{"name": r.name, "status": r.status.value, "message": r.message} for r in results]
            print(json.dumps(output, indent=2))
        else:
            print(f"\n{Colors.BOLD}=== Signature Verification ==={Colors.RESET}")
            for r in results:
                if r.status == CodesignStatus.SIGNED:
                    print(f"  {Colors.GREEN}✓{Colors.RESET} {r.name}: Valid signature")
                elif r.status == CodesignStatus.NOT_SIGNED:
                    print(f"  {Colors.YELLOW}⚠{Colors.RESET} {r.name}: Not signed")
                elif r.status == CodesignStatus.NOT_APPLICABLE:
                    print(f"  {Colors.BLUE}-{Colors.RESET} {r.name}: N/A")
                else:
                    print(f"  {Colors.RED}✗{Colors.RESET} {r.name}: {r.message}")
        invalid = [r for r in results if r.status in (CodesignStatus.INVALID, CodesignStatus.ERROR)]
        return 1 if invalid else 0

    else:
        # Default: show status/help
        logger.info("Use --check-staged, --rebuild, --health, or --codesign")
        logger.info("Run with --help for usage information")
        return 0


if __name__ == "__main__":
    sys.exit(main())
