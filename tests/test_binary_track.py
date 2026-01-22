"""Tests for the binary_track module."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from pre_commit.binary_track import (
    BinaryConfig,
    BinaryStatus,
    CodesignConfig,
    CodesignResult,
    CodesignStatus,
    ConfigDict,
    HealthResult,
    LanguagePreset,
    Logger,
    PreCommitPolicy,
    RebuildResult,
    StagedCheckResult,
    TrackConfig,
    build_config_from_args,
    check_binary_health,
    check_staged_files,
    codesign_binary,
    derive_binary_name,
    detect_project_language,
    file_matches_pattern,
    get_language_preset,
    is_codesign_available,
    list_language_presets,
    load_config_file,
    main,
    merge_configs,
    parse_args,
    rebuild_all,
    rebuild_binary,
    verify_signature,
    LANGUAGE_PRESETS,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


# =============================================================================
# Language Presets Tests
# =============================================================================


class TestLanguagePresets:
    """Tests for language presets."""

    def test_get_preset_exists(self) -> None:
        """Test getting an existing preset."""
        preset = get_language_preset("rust")
        assert preset is not None
        assert preset.name == "rust"
        assert "**/*.rs" in preset.source_patterns
        assert "Cargo.toml" in preset.source_patterns

    def test_get_preset_not_exists(self) -> None:
        """Test getting a non-existent preset."""
        preset = get_language_preset("not-a-language")
        assert preset is None

    def test_get_preset_case_insensitive(self) -> None:
        """Test preset lookup is case insensitive."""
        preset1 = get_language_preset("RUST")
        preset2 = get_language_preset("Rust")
        preset3 = get_language_preset("rust")
        assert preset1 == preset2 == preset3

    def test_list_presets(self) -> None:
        """Test listing available presets."""
        presets = list_language_presets()
        assert len(presets) >= 10
        names = [name for name, _ in presets]
        assert "go" in names
        assert "rust" in names
        assert "python" in names
        assert "swift" in names

    def test_all_presets_have_required_fields(self) -> None:
        """Test all presets have required fields."""
        for name, preset in LANGUAGE_PRESETS.items():
            assert preset.name == name
            assert len(preset.source_patterns) > 0
            assert preset.default_build_cmd
            assert preset.default_install_path_template
            assert len(preset.file_extensions) > 0
            assert preset.description

    def test_preset_get_build_cmd(self) -> None:
        """Test getting build command from preset."""
        preset = get_language_preset("rust")
        assert preset is not None
        cmd = preset.get_build_cmd("mytool")
        assert "mytool" in cmd
        assert "cargo" in cmd.lower()

    def test_preset_get_install_path(self) -> None:
        """Test getting install path from preset."""
        preset = get_language_preset("go")
        assert preset is not None
        path = preset.get_install_path("mytool")
        assert "mytool" in path
        assert "~" in path or "/" in path


# =============================================================================
# Project Detection Tests
# =============================================================================


class TestProjectDetection:
    """Tests for smart project detection."""

    def test_detect_rust_project(self, tmp_path: Path) -> None:
        """Test detecting Rust project."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'")
        language = detect_project_language(tmp_path)
        assert language == "rust"

    def test_detect_go_project(self, tmp_path: Path) -> None:
        """Test detecting Go project."""
        (tmp_path / "go.mod").write_text("module example.com/test")
        language = detect_project_language(tmp_path)
        assert language == "go"

    def test_detect_swift_project(self, tmp_path: Path) -> None:
        """Test detecting Swift package project."""
        (tmp_path / "Package.swift").write_text("// swift-tools-version:5.5")
        language = detect_project_language(tmp_path)
        assert language == "swift"

    def test_detect_swift_app_project(self, tmp_path: Path) -> None:
        """Test detecting Swift Xcode project."""
        (tmp_path / "Package.swift").write_text("// swift-tools-version:5.5")
        (tmp_path / "MyApp.xcodeproj").mkdir()
        language = detect_project_language(tmp_path)
        assert language == "swift-app"

    def test_detect_uv_project(self, tmp_path: Path) -> None:
        """Test detecting uv project."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
        (tmp_path / "uv.lock").write_text("")
        language = detect_project_language(tmp_path)
        assert language == "uv"

    def test_detect_python_project(self, tmp_path: Path) -> None:
        """Test detecting standard Python project."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
        language = detect_project_language(tmp_path)
        assert language == "python"

    def test_detect_pnpm_project(self, tmp_path: Path) -> None:
        """Test detecting pnpm project."""
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / "pnpm-lock.yaml").write_text("")
        language = detect_project_language(tmp_path)
        assert language == "pnpm"

    def test_detect_node_project(self, tmp_path: Path) -> None:
        """Test detecting npm project."""
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / "package-lock.json").write_text("")
        language = detect_project_language(tmp_path)
        assert language == "node"

    def test_detect_unknown_project(self, tmp_path: Path) -> None:
        """Test detection returns None for unknown project."""
        language = detect_project_language(tmp_path)
        assert language is None

    def test_derive_binary_name(self) -> None:
        """Test deriving binary name from path."""
        assert derive_binary_name(Path("apps/cli/mytool")) == "mytool"
        assert derive_binary_name(Path("services/api")) == "api"
        assert derive_binary_name(Path("my-project")) == "my-project"


# =============================================================================
# BinaryConfig Tests
# =============================================================================


class TestBinaryConfig:
    """Tests for the BinaryConfig dataclass."""

    def test_from_dict_minimal(self) -> None:
        """Test creating config with minimal data."""
        data = {
            "source_patterns": ["src/**/*.go"],
            "build_cmd": "go build",
            "install_path": "~/.local/bin/mytool",
        }
        config = BinaryConfig.from_dict("mytool", data)

        assert config.name == "mytool"
        assert config.source_patterns == ["src/**/*.go"]
        assert config.build_cmd == "go build"
        assert config.install_path == "~/.local/bin/mytool"

    def test_from_dict_with_language(self) -> None:
        """Test creating config with language."""
        data = {
            "source_patterns": ["**/*.rs"],
            "build_cmd": "cargo build",
            "install_path": "~/.local/bin/mytool",
            "language": "rust",
        }
        config = BinaryConfig.from_dict("mytool", data)
        assert config.language == "rust"

    def test_from_dict_with_working_dir(self) -> None:
        """Test creating config with working directory."""
        data = {
            "source_patterns": ["**/*.rs"],
            "build_cmd": "cargo build",
            "install_path": "~/.local/bin/mytool",
            "working_dir": "apps/cli/mytool",
        }
        config = BinaryConfig.from_dict("mytool", data)
        assert config.working_dir == "apps/cli/mytool"

    def test_get_expanded_install_path(self) -> None:
        """Test expanding ~ in install path."""
        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            install_path="~/.local/bin/mytool",
        )
        expanded = config.get_expanded_install_path()
        assert str(expanded).startswith(str(Path.home()))
        assert "mytool" in str(expanded)


# =============================================================================
# TrackConfig Tests
# =============================================================================


class TestTrackConfig:
    """Tests for the TrackConfig dataclass."""

    def test_from_dict_minimal(self, tmp_path: Path) -> None:
        """Test creating config with minimal data."""
        data: ConfigDict = {"binaries": {}}
        config = TrackConfig.from_dict(data, tmp_path)
        assert config.root_dir == tmp_path
        assert config.binaries == {}
        assert config.pre_commit_policy == PreCommitPolicy.WARN

    def test_from_dict_with_binaries(self, tmp_path: Path) -> None:
        """Test creating config with binaries."""
        data: ConfigDict = {
            "binaries": {
                "mytool": {
                    "source_patterns": ["**/*.go"],
                    "build_cmd": "go build",
                    "install_path": "~/.local/bin/mytool",
                },
            },
        }
        config = TrackConfig.from_dict(data, tmp_path)
        assert "mytool" in config.binaries
        assert config.binaries["mytool"].name == "mytool"

    def test_from_dict_with_policy(self, tmp_path: Path) -> None:
        """Test creating config with pre-commit policy."""
        data: ConfigDict = {
            "binaries": {},
            "pre_commit_policy": "block",
        }
        config = TrackConfig.from_dict(data, tmp_path)
        assert config.pre_commit_policy == PreCommitPolicy.BLOCK


# =============================================================================
# File Pattern Matching Tests
# =============================================================================


class TestFilePatternMatching:
    """Tests for file pattern matching."""

    def test_simple_pattern(self, tmp_path: Path) -> None:
        """Test simple file pattern."""
        assert file_matches_pattern("main.go", "*.go", tmp_path)
        assert not file_matches_pattern("main.rs", "*.go", tmp_path)

    def test_recursive_pattern(self, tmp_path: Path) -> None:
        """Test recursive glob pattern."""
        assert file_matches_pattern("src/main.go", "**/*.go", tmp_path)
        assert file_matches_pattern("deep/nested/file.go", "**/*.go", tmp_path)
        assert not file_matches_pattern("src/main.rs", "**/*.go", tmp_path)

    def test_specific_file(self, tmp_path: Path) -> None:
        """Test matching specific files."""
        assert file_matches_pattern("Cargo.toml", "Cargo.toml", tmp_path)
        assert file_matches_pattern("go.mod", "go.mod", tmp_path)

    def test_nested_file(self, tmp_path: Path) -> None:
        """Test matching nested files with pattern."""
        assert file_matches_pattern("src/lib.rs", "**/*.rs", tmp_path)
        assert file_matches_pattern("pkg/util/helper.go", "**/*.go", tmp_path)


# =============================================================================
# Check Staged Files Tests
# =============================================================================


class TestCheckStagedFiles:
    """Tests for check_staged_files function."""

    def test_no_files(self, tmp_path: Path) -> None:
        """Test with no staged files."""
        config = TrackConfig(
            root_dir=tmp_path,
            binaries={
                "mytool": BinaryConfig(
                    name="mytool",
                    source_patterns=["**/*.go"],
                ),
            },
        )
        logger = Logger()
        result = check_staged_files(config, logger, [])
        assert not result.has_affected
        assert result.total_files_checked == 0

    def test_no_matching_files(self, tmp_path: Path) -> None:
        """Test with files that don't match patterns."""
        config = TrackConfig(
            root_dir=tmp_path,
            binaries={
                "mytool": BinaryConfig(
                    name="mytool",
                    source_patterns=["**/*.go"],
                ),
            },
        )
        logger = Logger()
        result = check_staged_files(config, logger, ["README.md", "main.rs"])
        assert not result.has_affected

    def test_matching_files(self, tmp_path: Path) -> None:
        """Test with files that match patterns."""
        config = TrackConfig(
            root_dir=tmp_path,
            binaries={
                "mytool": BinaryConfig(
                    name="mytool",
                    source_patterns=["**/*.go", "go.mod"],
                ),
            },
        )
        logger = Logger()
        result = check_staged_files(config, logger, ["main.go", "go.mod"])
        assert result.has_affected
        assert "mytool" in result.affected_binaries
        assert len(result.file_matches["mytool"]) == 2

    def test_multiple_binaries(self, tmp_path: Path) -> None:
        """Test with multiple binaries."""
        config = TrackConfig(
            root_dir=tmp_path,
            binaries={
                "gotool": BinaryConfig(
                    name="gotool",
                    source_patterns=["**/*.go"],
                ),
                "rusttool": BinaryConfig(
                    name="rusttool",
                    source_patterns=["**/*.rs"],
                ),
            },
        )
        logger = Logger()
        result = check_staged_files(config, logger, ["main.go", "lib.rs"])
        assert result.has_affected
        assert "gotool" in result.affected_binaries
        assert "rusttool" in result.affected_binaries

    def test_working_dir_scoping(self, tmp_path: Path) -> None:
        """Test that working_dir scopes pattern matching."""
        config = TrackConfig(
            root_dir=tmp_path,
            binaries={
                "mytool": BinaryConfig(
                    name="mytool",
                    source_patterns=["**/*.rs"],
                    working_dir="apps/cli/mytool",
                ),
            },
        )
        logger = Logger()

        # File in the working dir should match
        result1 = check_staged_files(config, logger, ["apps/cli/mytool/src/main.rs"])
        assert result1.has_affected

        # File outside working dir should not match
        result2 = check_staged_files(config, logger, ["other/main.rs"])
        assert not result2.has_affected


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHealthCheck:
    """Tests for health check functionality."""

    def test_missing_binary(self, tmp_path: Path) -> None:
        """Test health check for missing binary."""
        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            install_path=str(tmp_path / "nonexistent"),
        )
        logger = Logger()
        result = check_binary_health(config, logger)
        assert not result.is_healthy()
        assert result.status == BinaryStatus.MISSING

    def test_existing_but_not_executable(self, tmp_path: Path) -> None:
        """Test health check for non-executable file."""
        binary_path = tmp_path / "mytool"
        binary_path.write_text("#!/bin/sh\necho hello")

        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            install_path=str(binary_path),
        )
        logger = Logger()
        result = check_binary_health(config, logger)
        assert not result.is_healthy()
        assert result.status == BinaryStatus.NOT_EXECUTABLE

    def test_healthy_binary(self, tmp_path: Path) -> None:
        """Test health check for healthy binary."""
        binary_path = tmp_path / "mytool"
        binary_path.write_text("#!/bin/sh\necho hello")
        binary_path.chmod(0o755)

        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            install_path=str(binary_path),
        )
        logger = Logger()
        result = check_binary_health(config, logger)
        assert result.is_healthy()
        assert result.status == BinaryStatus.UP_TO_DATE

    def test_no_install_path(self) -> None:
        """Test health check with no install path configured."""
        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            install_path="",
        )
        logger = Logger()
        result = check_binary_health(config, logger)
        assert result.status == BinaryStatus.UNKNOWN


# =============================================================================
# Rebuild Tests
# =============================================================================


class TestRebuild:
    """Tests for rebuild functionality."""

    def test_rebuild_dry_run(self, tmp_path: Path) -> None:
        """Test rebuild in dry-run mode."""
        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            build_cmd="go build -o ~/.local/bin/mytool",
        )
        logger = Logger()
        result = rebuild_binary(config, tmp_path, logger, dry_run=True)
        assert result.success
        assert "Dry run" in result.message

    def test_rebuild_no_build_cmd(self, tmp_path: Path) -> None:
        """Test rebuild with no build command."""
        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            build_cmd="",
        )
        logger = Logger()
        result = rebuild_binary(config, tmp_path, logger)
        assert not result.success
        assert "No build command" in result.message

    @patch("subprocess.run")
    def test_rebuild_success(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Test successful rebuild."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            build_cmd="go build -o ~/.local/bin/mytool",
        )
        logger = Logger()
        result = rebuild_binary(config, tmp_path, logger)
        assert result.success

    @patch("subprocess.run")
    def test_rebuild_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Test failed rebuild."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="build error")

        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            build_cmd="go build",
        )
        logger = Logger()
        result = rebuild_binary(config, tmp_path, logger)
        assert not result.success


# =============================================================================
# Codesigning Tests
# =============================================================================


class TestCodesigning:
    """Tests for codesigning functionality."""

    def test_codesign_not_darwin(self, tmp_path: Path) -> None:
        """Test codesigning on non-macOS platform."""
        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            install_path=str(tmp_path / "mytool"),
        )
        global_codesign = CodesignConfig(enabled=True)
        logger = Logger()

        with patch("sys.platform", "linux"):
            result = codesign_binary(config, global_codesign, logger)
            assert result.status == CodesignStatus.NOT_APPLICABLE

    def test_codesign_not_enabled(self, tmp_path: Path) -> None:
        """Test codesigning when not enabled."""
        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            install_path=str(tmp_path / "mytool"),
            codesign=CodesignConfig(enabled=False),
        )
        global_codesign = CodesignConfig(enabled=False)
        logger = Logger()

        result = codesign_binary(config, global_codesign, logger)
        assert result.status == CodesignStatus.NOT_APPLICABLE

    def test_verify_signature_missing_binary(self, tmp_path: Path) -> None:
        """Test signature verification for missing binary."""
        config = BinaryConfig(
            name="mytool",
            source_patterns=["**/*.go"],
            install_path=str(tmp_path / "nonexistent"),
        )
        logger = Logger()

        result = verify_signature(config, logger)
        assert result.status == CodesignStatus.ERROR


# =============================================================================
# Config Loading Tests
# =============================================================================


class TestConfigLoading:
    """Tests for configuration loading."""

    def test_load_missing_config(self, tmp_path: Path) -> None:
        """Test loading non-existent config file."""
        config = load_config_file(tmp_path / "nonexistent.yaml", tmp_path)
        assert config == {"binaries": {}}

    def test_load_valid_config(self, tmp_path: Path) -> None:
        """Test loading valid config file."""
        config_path = tmp_path / ".binariesrc.yaml"
        config_path.write_text(
            """
binaries:
  mytool:
    source_patterns:
      - "**/*.go"
    build_cmd: "go build"
    install_path: "~/.local/bin/mytool"
"""
        )
        config = load_config_file(config_path, tmp_path)
        assert "mytool" in config.get("binaries", {})

    def test_merge_configs(self) -> None:
        """Test merging multiple configs."""
        config1: ConfigDict = {
            "binaries": {
                "tool1": {"source_patterns": ["**/*.go"]},
            },
        }
        config2: ConfigDict = {
            "binaries": {
                "tool2": {"source_patterns": ["**/*.rs"]},
            },
            "pre_commit_policy": "block",
        }

        merged = merge_configs(config1, config2)
        assert "tool1" in merged["binaries"]
        assert "tool2" in merged["binaries"]
        assert merged["pre_commit_policy"] == "block"


# =============================================================================
# CLI Argument Parsing Tests
# =============================================================================


class TestArgParsing:
    """Tests for CLI argument parsing."""

    def test_parse_check_staged(self) -> None:
        """Test parsing --check-staged argument."""
        args = parse_args(["--check-staged", "file1.go", "file2.go"])
        assert args.check_staged
        assert args.files == ["file1.go", "file2.go"]

    def test_parse_project(self) -> None:
        """Test parsing --project argument."""
        args = parse_args(["--check-staged", "--project=apps/cli/mytool"])
        assert args.projects == ["apps/cli/mytool"]

    def test_parse_multiple_projects(self) -> None:
        """Test parsing multiple --project arguments."""
        args = parse_args([
            "--check-staged",
            "--project=apps/cli/tool1",
            "--project=apps/cli/tool2",
        ])
        assert len(args.projects) == 2

    def test_parse_binary_language(self) -> None:
        """Test parsing --binary and --language arguments."""
        args = parse_args([
            "--check-staged",
            "--binary=mytool",
            "--language=rust",
        ])
        assert args.binaries == ["mytool"]
        assert args.languages == ["rust"]

    def test_parse_rebuild(self) -> None:
        """Test parsing --rebuild argument."""
        args = parse_args(["--rebuild", "--project=apps/cli/mytool"])
        assert args.rebuild

    def test_parse_health(self) -> None:
        """Test parsing --health argument."""
        args = parse_args(["--health", "--project=apps/cli/mytool"])
        assert args.health

    def test_parse_codesign(self) -> None:
        """Test parsing --codesign argument."""
        args = parse_args(["--codesign", "--project=apps/cli/mytool"])
        assert args.codesign

    def test_parse_dry_run(self) -> None:
        """Test parsing --dry-run argument."""
        args = parse_args(["--rebuild", "--dry-run"])
        assert args.dry_run

    def test_parse_verbose(self) -> None:
        """Test parsing --verbose argument."""
        args = parse_args(["-v", "--check-staged"])
        assert args.verbose

    def test_parse_json_output(self) -> None:
        """Test parsing --json argument."""
        args = parse_args(["--json", "--check-staged"])
        assert args.json_output


# =============================================================================
# Build Config From Args Tests
# =============================================================================


class TestBuildConfigFromArgs:
    """Tests for building config from CLI arguments."""

    def test_project_with_detection(self, tmp_path: Path) -> None:
        """Test --project triggers auto-detection."""
        project_path = tmp_path / "apps" / "cli" / "mytool"
        project_path.mkdir(parents=True)
        (project_path / "Cargo.toml").write_text("[package]\nname = 'mytool'")

        args = parse_args(["--check-staged", f"--project={project_path}"])
        config = build_config_from_args(args, tmp_path)

        assert "mytool" in config["binaries"]
        assert config["binaries"]["mytool"]["language"] == "rust"
        assert "**/*.rs" in config["binaries"]["mytool"]["source_patterns"]

    def test_explicit_binary_language(self, tmp_path: Path) -> None:
        """Test --binary with --language."""
        args = parse_args([
            "--check-staged",
            "--binary=mytool",
            "--language=go",
        ])
        config = build_config_from_args(args, tmp_path)

        assert "mytool" in config["binaries"]
        assert config["binaries"]["mytool"]["language"] == "go"

    def test_policy_setting(self, tmp_path: Path) -> None:
        """Test --policy argument."""
        args = parse_args(["--check-staged", "--policy=block"])
        config = build_config_from_args(args, tmp_path)
        assert config["pre_commit_policy"] == "block"


# =============================================================================
# Main Function Tests
# =============================================================================


class TestMain:
    """Tests for main entry point."""

    def test_list_languages(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test --list-languages output."""
        result = main(["--list-languages"])
        assert result == 0
        captured = capsys.readouterr()
        assert "rust" in captured.out.lower()
        assert "go" in captured.out.lower()
        assert "python" in captured.out.lower()

    def test_check_staged_no_binaries(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        """Test --check-staged with no binaries configured."""
        monkeypatch.chdir(tmp_path)
        result = main(["--check-staged"])
        assert result == 0

    def test_check_staged_no_files(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        """Test --check-staged with no files."""
        monkeypatch.chdir(tmp_path)
        project_path = tmp_path / "mytool"
        project_path.mkdir()
        (project_path / "Cargo.toml").write_text("[package]\nname = 'test'")

        result = main(["--check-staged", f"--project={project_path}"])
        assert result == 0

    def test_check_staged_with_matching_files(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        """Test --check-staged with matching files."""
        monkeypatch.chdir(tmp_path)
        project_path = tmp_path / "mytool"
        project_path.mkdir()
        (project_path / "Cargo.toml").write_text("[package]\nname = 'test'")

        result = main([
            "--check-staged",
            f"--project={project_path}",
            "mytool/src/main.rs",
        ])
        # With WARN policy, matching files still returns 0
        assert result == 0

    def test_check_staged_block_policy(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        """Test --check-staged with block policy."""
        monkeypatch.chdir(tmp_path)
        project_path = tmp_path / "mytool"
        project_path.mkdir()
        (project_path / "Cargo.toml").write_text("[package]\nname = 'test'")

        # Use relative path for --project since we're in tmp_path
        result = main([
            "--check-staged",
            "--project=mytool",
            "--policy=block",
            "mytool/src/main.rs",
        ])
        # With BLOCK policy, matching files returns 1
        assert result == 1

    def test_health_missing_binary(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        """Test --health with missing binary."""
        monkeypatch.chdir(tmp_path)
        project_path = tmp_path / "mytool"
        project_path.mkdir()
        (project_path / "Cargo.toml").write_text("[package]\nname = 'test'")

        result = main(["--health", f"--project={project_path}"])
        assert result == 1  # Missing binary is unhealthy

    def test_rebuild_dry_run(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        """Test --rebuild with --dry-run."""
        monkeypatch.chdir(tmp_path)
        project_path = tmp_path / "mytool"
        project_path.mkdir()
        (project_path / "Cargo.toml").write_text("[package]\nname = 'test'")

        result = main(["--rebuild", "--dry-run", f"--project={project_path}"])
        assert result == 0


# =============================================================================
# Logger Tests
# =============================================================================


class TestLogger:
    """Tests for Logger class."""

    def test_quiet_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test quiet mode suppresses output."""
        logger = Logger(quiet=True)
        logger.info("test info")
        logger.success("test success")
        logger.warn("test warn")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_verbose_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test verbose mode shows debug output."""
        logger = Logger(verbose=True)
        logger.debug("test debug")
        captured = capsys.readouterr()
        assert "test debug" in captured.out

    def test_non_verbose_hides_debug(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test non-verbose mode hides debug output."""
        logger = Logger(verbose=False)
        logger.debug("test debug")
        captured = capsys.readouterr()
        assert "test debug" not in captured.out

    def test_error_goes_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test error output goes to stderr."""
        logger = Logger()
        logger.error("test error")
        captured = capsys.readouterr()
        assert "test error" in captured.err


# =============================================================================
# StagedCheckResult Tests
# =============================================================================


class TestStagedCheckResult:
    """Tests for StagedCheckResult dataclass."""

    def test_has_affected_empty(self) -> None:
        """Test has_affected with no affected binaries."""
        result = StagedCheckResult()
        assert not result.has_affected

    def test_has_affected_with_binaries(self) -> None:
        """Test has_affected with affected binaries."""
        result = StagedCheckResult(
            affected_binaries=["mytool"],
            file_matches={"mytool": ["main.rs"]},
        )
        assert result.has_affected

    def test_to_dict(self) -> None:
        """Test to_dict serialization."""
        result = StagedCheckResult(
            affected_binaries=["mytool"],
            file_matches={"mytool": ["main.rs", "lib.rs"]},
            total_files_checked=5,
        )
        data = result.to_dict()
        assert data["affected_binaries"] == ["mytool"]
        assert data["file_matches"]["mytool"] == ["main.rs", "lib.rs"]
        assert data["total_files_checked"] == 5
        assert data["has_affected"] is True


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for full workflows."""

    def test_end_to_end_check_staged(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        """Test complete check-staged workflow."""
        monkeypatch.chdir(tmp_path)

        # Set up a Rust project
        project_path = tmp_path / "apps" / "cli" / "mytool"
        project_path.mkdir(parents=True)
        (project_path / "Cargo.toml").write_text("[package]\nname = 'mytool'")
        (project_path / "src").mkdir()
        (project_path / "src" / "main.rs").write_text("fn main() {}")

        # Run check-staged with matching files
        result = main([
            "--check-staged",
            "--project=apps/cli/mytool",
            "apps/cli/mytool/src/main.rs",
        ])

        # Should detect stale binary but not block (default WARN policy)
        assert result == 0

    def test_end_to_end_with_config_file(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        """Test workflow with config file."""
        monkeypatch.chdir(tmp_path)

        # Create config file
        config_file = tmp_path / ".binariesrc.yaml"
        config_file.write_text(
            """
binaries:
  mytool:
    source_patterns:
      - "**/*.rs"
      - "Cargo.toml"
    build_cmd: "cargo build --release"
    install_path: "~/.local/bin/mytool"
    language: rust
"""
        )

        # Run check-staged
        result = main(["--check-staged", "src/main.rs"])
        assert result == 0

    def test_json_output(self, tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Test JSON output format."""
        monkeypatch.chdir(tmp_path)

        project_path = tmp_path / "mytool"
        project_path.mkdir()
        (project_path / "Cargo.toml").write_text("[package]\nname = 'test'")

        result = main([
            "--check-staged",
            "--json",
            f"--project={project_path}",
            "mytool/src/main.rs",
        ])

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "affected_binaries" in data
        assert "file_matches" in data
