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

from pre_commit.binary_track import (
    BinaryConfig,
    BinaryStatus,
    BinaryStatusResult,
    BinaryType,
    BuildManifest,
    BuildRecord,
    CodesignConfig,
    CodesignResult,
    CodesignStatus,
    ConfigDict,
    InstallLocation,
    InstallScope,
    Platform,
    PreCommitPolicy,
    RebuildStatus,
    TrackConfig,
    TrackingMethod,
    check_binary_health,
    codesign_binary,
    compute_file_hash,
    ensure_install_path_exists,
    expand_patterns,
    get_current_commit,
    get_current_platform,
    get_default_install_locations,
    get_path_setup_instructions,
    get_recommended_install_path,
    get_source_fingerprint,
    is_binary_stale,
    is_codesign_available,
    is_path_in_system_path,
    load_config_file,
    load_env_config,
    load_manifest,
    save_manifest,
    verify_signature,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


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
        assert config.language == ""
        assert config.rebuild_on_commit is True
        assert config.check_in_path is True
        assert config.timeout == 300

    def test_from_dict_full(self) -> None:
        """Test creating config with all options."""
        data = {
            "source_patterns": ["cmd/**/*.go", "internal/**/*.go"],
            "build_cmd": "make build",
            "install_path": "/usr/local/bin/mytool",
            "language": "go",
            "rebuild_on_commit": False,
            "check_in_path": False,
            "working_dir": "./cmd",
            "env": {"CGO_ENABLED": "0"},
            "timeout": 600,
        }
        config = BinaryConfig.from_dict("mytool", data)

        assert config.name == "mytool"
        assert len(config.source_patterns) == 2
        assert config.language == "go"
        assert config.rebuild_on_commit is False
        assert config.check_in_path is False
        assert config.working_dir == "./cmd"
        assert config.env == {"CGO_ENABLED": "0"}
        assert config.timeout == 600

    def test_get_expanded_install_path(self) -> None:
        """Test expanding ~ in install path."""
        config = BinaryConfig(
            name="test",
            install_path="~/.local/bin/test",
        )
        expanded = config.get_expanded_install_path()
        assert "~" not in str(expanded)
        assert str(expanded).endswith(".local/bin/test")


class TestTrackConfig:
    """Tests for the TrackConfig dataclass."""

    def test_from_dict_defaults(self) -> None:
        """Test creating config with defaults."""
        config = TrackConfig.from_dict({})

        assert config.binaries == {}
        assert config.auto_rebuild is False
        assert config.stale_threshold_hours == 24
        assert config.watch_debounce_ms == 500
        assert config.pre_commit_policy == PreCommitPolicy.WARN
        assert config.track_by == TrackingMethod.GIT_COMMIT
        assert config.parallel_builds is True
        assert config.max_workers == 4

    def test_from_dict_with_binaries(self) -> None:
        """Test creating config with binaries."""
        data: ConfigDict = {
            "binaries": {
                "tool1": {
                    "source_patterns": ["src/**/*.rs"],
                    "build_cmd": "cargo build",
                    "install_path": "~/.cargo/bin/tool1",
                    "language": "rust",
                },
                "tool2": {
                    "source_patterns": ["*.py"],
                    "build_cmd": "pip install -e .",
                    "install_path": "~/.local/bin/tool2",
                    "language": "python",
                },
            },
            "auto_rebuild": True,
            "pre_commit_policy": "block",
            "track_by": "hash",
        }
        config = TrackConfig.from_dict(data)

        assert len(config.binaries) == 2
        assert "tool1" in config.binaries
        assert "tool2" in config.binaries
        assert config.auto_rebuild is True
        assert config.pre_commit_policy == PreCommitPolicy.BLOCK
        assert config.track_by == TrackingMethod.HASH

    def test_from_dict_invalid_policy(self) -> None:
        """Test that invalid policy defaults to WARN."""
        config = TrackConfig.from_dict({"pre_commit_policy": "invalid"})
        assert config.pre_commit_policy == PreCommitPolicy.WARN

    def test_from_dict_invalid_track_by(self) -> None:
        """Test that invalid track_by defaults to GIT_COMMIT."""
        config = TrackConfig.from_dict({"track_by": "invalid"})
        assert config.track_by == TrackingMethod.GIT_COMMIT


class TestLoadConfigFile:
    """Tests for the load_config_file function."""

    def test_load_existing_config(self, tmp_path: Path) -> None:
        """Test loading an existing config file."""
        config_file = tmp_path / ".binariesrc.json"
        config_data = {
            "binaries": {
                "mytool": {
                    "source_patterns": ["src/**/*.go"],
                    "build_cmd": "go build",
                    "install_path": "~/.local/bin/mytool",
                }
            },
            "track_by": "mtime",
        }
        config_file.write_text(json.dumps(config_data))

        os.chdir(tmp_path)
        loaded = load_config_file(root_dir=tmp_path)

        assert "binaries" in loaded
        assert "mytool" in loaded["binaries"]
        assert loaded["track_by"] == "mtime"

    def test_load_explicit_config_path(self, tmp_path: Path) -> None:
        """Test loading config from explicit path."""
        config_file = tmp_path / "custom-config.json"
        config_data = {"binaries": {"tool": {"build_cmd": "make"}}}
        config_file.write_text(json.dumps(config_data))

        loaded = load_config_file(Path("custom-config.json"), root_dir=tmp_path)
        assert "tool" in loaded["binaries"]

    def test_load_missing_config(self, tmp_path: Path) -> None:
        """Test loading when no config exists returns empty dict."""
        loaded = load_config_file(root_dir=tmp_path)
        assert loaded == {}

    def test_load_explicit_missing_config_raises(self, tmp_path: Path) -> None:
        """Test loading explicit missing config raises error."""
        with pytest.raises(FileNotFoundError):
            load_config_file(Path("nonexistent.json"), root_dir=tmp_path)


class TestLoadEnvConfig:
    """Tests for the load_env_config function."""

    def test_load_auto_rebuild(self, monkeypatch: MonkeyPatch) -> None:
        """Test loading auto_rebuild from env."""
        monkeypatch.setenv("BINARY_TRACK_AUTO_REBUILD", "true")
        config = load_env_config()
        assert config.get("auto_rebuild") is True

    def test_load_policy(self, monkeypatch: MonkeyPatch) -> None:
        """Test loading policy from env."""
        monkeypatch.setenv("BINARY_TRACK_POLICY", "block")
        config = load_env_config()
        assert config.get("pre_commit_policy") == "block"

    def test_empty_env(self, monkeypatch: MonkeyPatch) -> None:
        """Test empty config when no env vars set."""
        monkeypatch.delenv("BINARY_TRACK_AUTO_REBUILD", raising=False)
        monkeypatch.delenv("BINARY_TRACK_POLICY", raising=False)
        config = load_env_config()
        assert config == {}


class TestBuildManifest:
    """Tests for the BuildManifest dataclass."""

    def test_to_dict(self) -> None:
        """Test serializing manifest to dict."""
        record = BuildRecord(
            binary_name="mytool",
            built_at="2024-01-15T10:00:00Z",
            source_commit="abc123",
            build_duration=5.5,
            success=True,
        )
        manifest = BuildManifest(
            records={"mytool": record},
            created_at="2024-01-15T09:00:00Z",
            updated_at="2024-01-15T10:00:00Z",
        )

        data = manifest.to_dict()
        assert data["created_at"] == "2024-01-15T09:00:00Z"
        assert "mytool" in data["records"]
        assert data["records"]["mytool"]["source_commit"] == "abc123"

    def test_from_dict(self) -> None:
        """Test deserializing manifest from dict."""
        data = {
            "created_at": "2024-01-15T09:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
            "records": {
                "mytool": {
                    "binary_name": "mytool",
                    "built_at": "2024-01-15T10:00:00Z",
                    "source_commit": "abc123",
                    "source_hashes": {},
                    "source_mtimes": {},
                    "build_duration": 5.5,
                    "success": True,
                    "error": "",
                }
            },
        }
        manifest = BuildManifest.from_dict(data)

        assert manifest.created_at == "2024-01-15T09:00:00Z"
        assert "mytool" in manifest.records
        assert manifest.records["mytool"].source_commit == "abc123"

    def test_round_trip(self) -> None:
        """Test that manifest survives serialization round-trip."""
        record = BuildRecord(
            binary_name="tool",
            built_at="2024-01-15T10:00:00Z",
            source_hashes={"src/main.go": "abc123"},
        )
        original = BuildManifest(records={"tool": record}, created_at="2024-01-15")

        restored = BuildManifest.from_dict(original.to_dict())
        assert restored.records["tool"].source_hashes == {"src/main.go": "abc123"}


class TestSaveLoadManifest:
    """Tests for saving and loading manifests."""

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Test saving and loading a manifest."""
        record = BuildRecord(
            binary_name="mytool",
            built_at="2024-01-15T10:00:00Z",
            source_commit="abc123",
        )
        manifest = BuildManifest(records={"mytool": record})

        save_manifest(manifest, tmp_path)
        loaded = load_manifest(tmp_path)

        assert "mytool" in loaded.records
        assert loaded.records["mytool"].source_commit == "abc123"

    def test_load_missing_manifest(self, tmp_path: Path) -> None:
        """Test loading when no manifest exists."""
        manifest = load_manifest(tmp_path)
        assert manifest.records == {}

    def test_load_invalid_manifest(self, tmp_path: Path) -> None:
        """Test loading invalid JSON manifest."""
        manifest_path = tmp_path / ".binary-track-manifest.json"
        manifest_path.write_text("invalid json{")

        manifest = load_manifest(tmp_path)
        assert manifest.records == {}


class TestExpandPatterns:
    """Tests for the expand_patterns function."""

    def test_simple_glob(self, tmp_path: Path) -> None:
        """Test expanding simple glob pattern."""
        (tmp_path / "file1.go").touch()
        (tmp_path / "file2.go").touch()
        (tmp_path / "file.py").touch()

        files = expand_patterns(tmp_path, ["*.go"])
        assert len(files) == 2
        assert all(f.suffix == ".go" for f in files)

    def test_recursive_glob(self, tmp_path: Path) -> None:
        """Test expanding recursive glob pattern."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.go").touch()
        pkg = src / "pkg"
        pkg.mkdir()
        (pkg / "util.go").touch()

        files = expand_patterns(tmp_path, ["src/**/*.go"])
        assert len(files) == 2

    def test_multiple_patterns(self, tmp_path: Path) -> None:
        """Test expanding multiple patterns."""
        (tmp_path / "main.go").touch()
        (tmp_path / "go.mod").touch()
        (tmp_path / "readme.md").touch()

        files = expand_patterns(tmp_path, ["*.go", "go.mod"])
        names = [f.name for f in files]
        assert "main.go" in names
        assert "go.mod" in names
        assert "readme.md" not in names

    def test_no_matches(self, tmp_path: Path) -> None:
        """Test pattern with no matches."""
        files = expand_patterns(tmp_path, ["*.rs"])
        assert files == []


class TestComputeFileHash:
    """Tests for the compute_file_hash function."""

    def test_compute_hash(self, tmp_path: Path) -> None:
        """Test computing file hash."""
        file = tmp_path / "test.txt"
        file.write_text("hello world")

        hash1 = compute_file_hash(file)
        assert len(hash1) == 64  # SHA-256 hex length

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        """Test that same content produces same hash."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        content = "identical content"
        file1.write_text(content)
        file2.write_text(content)

        assert compute_file_hash(file1) == compute_file_hash(file2)

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        """Test that different content produces different hash."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content a")
        file2.write_text("content b")

        assert compute_file_hash(file1) != compute_file_hash(file2)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Test that missing file returns empty string."""
        result = compute_file_hash(tmp_path / "nonexistent.txt")
        assert result == ""


class TestCheckBinaryHealth:
    """Tests for the check_binary_health function."""

    def test_missing_binary(self, tmp_path: Path) -> None:
        """Test checking a missing binary."""
        config = BinaryConfig(
            name="mytool",
            install_path=str(tmp_path / "nonexistent"),
        )
        result = check_binary_health(config)

        assert result.status == BinaryStatus.MISSING
        assert result.exists is False
        assert "not found" in result.message.lower()

    def test_existing_executable(self, tmp_path: Path) -> None:
        """Test checking an existing executable binary."""
        binary = tmp_path / "mytool"
        binary.write_text("#!/bin/sh\necho hello")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

        config = BinaryConfig(
            name="mytool",
            install_path=str(binary),
            check_in_path=False,
        )
        result = check_binary_health(config)

        assert result.exists is True
        assert result.executable is True
        assert result.status == BinaryStatus.CURRENT

    def test_not_executable(self, tmp_path: Path) -> None:
        """Test checking a file that's not executable."""
        binary = tmp_path / "mytool"
        binary.write_text("not executable")
        # Explicitly remove execute permission
        binary.chmod(stat.S_IRUSR | stat.S_IWUSR)

        config = BinaryConfig(
            name="mytool",
            install_path=str(binary),
        )
        result = check_binary_health(config)

        assert result.exists is True
        assert result.executable is False
        assert result.status == BinaryStatus.NOT_EXECUTABLE


class TestIsBinaryStale:
    """Tests for the is_binary_stale function."""

    def test_no_build_record(self, tmp_path: Path) -> None:
        """Test binary is stale if never built."""
        config = BinaryConfig(
            name="mytool",
            source_patterns=["*.go"],
        )

        is_stale, reason, files = is_binary_stale(
            config, None, tmp_path, TrackingMethod.GIT_COMMIT
        )

        assert is_stale is True
        assert "never built" in reason

    def test_stale_by_hash(self, tmp_path: Path) -> None:
        """Test detecting staleness by hash changes."""
        src = tmp_path / "main.go"
        src.write_text("package main")

        config = BinaryConfig(
            name="mytool",
            source_patterns=["*.go"],
        )

        # Old build record with different hash
        old_record = BuildRecord(
            binary_name="mytool",
            built_at="2024-01-01",
            source_hashes={"main.go": "oldhash123"},
        )

        is_stale, reason, files = is_binary_stale(
            config, old_record, tmp_path, TrackingMethod.HASH
        )

        assert is_stale is True
        assert "changed" in reason
        assert "main.go" in files

    def test_current_by_hash(self, tmp_path: Path) -> None:
        """Test detecting current binary by matching hash."""
        src = tmp_path / "main.go"
        src.write_text("package main")
        current_hash = compute_file_hash(src)

        config = BinaryConfig(
            name="mytool",
            source_patterns=["*.go"],
        )

        record = BuildRecord(
            binary_name="mytool",
            built_at="2024-01-01",
            source_hashes={"main.go": current_hash},
        )

        is_stale, reason, files = is_binary_stale(
            config, record, tmp_path, TrackingMethod.HASH
        )

        assert is_stale is False
        assert "up to date" in reason


class TestGetSourceFingerprint:
    """Tests for the get_source_fingerprint function."""

    def test_hash_method(self, tmp_path: Path) -> None:
        """Test fingerprinting by hash."""
        (tmp_path / "main.go").write_text("package main")
        (tmp_path / "util.go").write_text("package util")

        commit, hashes, mtimes = get_source_fingerprint(
            tmp_path, ["*.go"], TrackingMethod.HASH
        )

        assert commit == ""
        assert len(hashes) == 2
        assert "main.go" in hashes
        assert mtimes == {}

    def test_mtime_method(self, tmp_path: Path) -> None:
        """Test fingerprinting by mtime."""
        (tmp_path / "main.go").write_text("package main")

        commit, hashes, mtimes = get_source_fingerprint(
            tmp_path, ["*.go"], TrackingMethod.MTIME
        )

        assert commit == ""
        assert hashes == {}
        assert "main.go" in mtimes
        assert mtimes["main.go"] > 0


class TestTrackResult:
    """Tests for the TrackResult dataclass."""

    def test_to_dict(self) -> None:
        """Test serializing TrackResult to dict."""
        status = BinaryStatusResult(
            name="mytool",
            status=BinaryStatus.STALE,
            install_path="/usr/local/bin/mytool",
            exists=True,
            executable=True,
            commits_behind=3,
        )
        from pre_commit.binary_track import RebuildResult, TrackResult

        result = TrackResult(
            statuses=[status],
            stale_count=1,
            all_current=False,
        )

        data = result.to_dict()
        assert data["stale_count"] == 1
        assert data["all_current"] is False
        assert len(data["statuses"]) == 1
        assert data["statuses"][0]["name"] == "mytool"
        assert data["statuses"][0]["status"] == "stale"


class TestIntegration:
    """Integration tests for binary tracking."""

    def test_full_workflow(self, tmp_path: Path) -> None:
        """Test a complete workflow: configure, check, and verify status."""
        # Create source files
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.go").write_text("package main")

        # Create config
        config_data = {
            "binaries": {
                "mytool": {
                    "source_patterns": ["src/**/*.go"],
                    "build_cmd": "echo 'building'",
                    "install_path": str(tmp_path / "bin" / "mytool"),
                    "language": "go",
                }
            },
            "track_by": "hash",
        }
        config_file = tmp_path / ".binariesrc.json"
        config_file.write_text(json.dumps(config_data))

        # Load config
        loaded = load_config_file(root_dir=tmp_path)
        config = TrackConfig.from_dict(loaded, tmp_path)

        assert "mytool" in config.binaries
        assert config.track_by == TrackingMethod.HASH

        # Check status (should be missing since binary doesn't exist)
        binary_config = config.binaries["mytool"]
        status = check_binary_health(binary_config)
        assert status.status == BinaryStatus.MISSING

    def test_manifest_persistence(self, tmp_path: Path) -> None:
        """Test that manifest persists correctly across operations."""
        # Create initial manifest
        record = BuildRecord(
            binary_name="mytool",
            built_at="2024-01-15T10:00:00Z",
            source_commit="abc123def456",
            build_duration=2.5,
        )
        manifest = BuildManifest(records={"mytool": record})
        save_manifest(manifest, tmp_path)

        # Verify file exists
        manifest_file = tmp_path / ".binary-track-manifest.json"
        assert manifest_file.exists()

        # Load and verify content
        loaded = load_manifest(tmp_path)
        assert "mytool" in loaded.records
        assert loaded.records["mytool"].source_commit == "abc123def456"
        assert loaded.records["mytool"].build_duration == 2.5


class TestPreCommitPolicy:
    """Tests for pre-commit policy handling."""

    def test_warn_policy_allows_stale(self) -> None:
        """Test that warn policy doesn't block on stale."""
        # This tests the policy enum behavior
        assert PreCommitPolicy.WARN.value == "warn"
        assert PreCommitPolicy.BLOCK.value == "block"
        assert PreCommitPolicy.IGNORE.value == "ignore"

    def test_policy_from_string(self) -> None:
        """Test creating policy from string."""
        assert PreCommitPolicy("warn") == PreCommitPolicy.WARN
        assert PreCommitPolicy("block") == PreCommitPolicy.BLOCK
        assert PreCommitPolicy("ignore") == PreCommitPolicy.IGNORE

        with pytest.raises(ValueError):
            PreCommitPolicy("invalid")


class TestCodesignConfig:
    """Tests for the CodesignConfig dataclass."""

    def test_from_dict_empty(self) -> None:
        """Test creating config with empty/None data."""
        config = CodesignConfig.from_dict(None)
        assert config.enabled is False
        assert config.identity == "-"
        assert config.entitlements is None
        assert config.options == []
        assert config.force is True

    def test_from_dict_full(self) -> None:
        """Test creating config with all fields."""
        data = {
            "enabled": True,
            "identity": "Developer ID Application: Test",
            "entitlements": "/path/to/entitlements.plist",
            "options": ["runtime", "library"],
            "force": False,
        }
        config = CodesignConfig.from_dict(data)
        assert config.enabled is True
        assert config.identity == "Developer ID Application: Test"
        assert config.entitlements == "/path/to/entitlements.plist"
        assert config.options == ["runtime", "library"]
        assert config.force is False

    def test_merge_with(self) -> None:
        """Test merging two codesign configs."""
        global_config = CodesignConfig(
            enabled=True,
            identity="Global Identity",
            options=["runtime"],
        )
        binary_config = CodesignConfig(
            enabled=False,
            identity="-",  # Default, should be overridden
        )

        merged = binary_config.merge_with(global_config)
        # Global enabled should be used since binary is False
        assert merged.enabled is True
        # Global identity should be used since binary is default
        assert merged.identity == "Global Identity"
        # Global options should be used since binary has none
        assert merged.options == ["runtime"]

    def test_merge_with_binary_override(self) -> None:
        """Test that explicit binary config takes precedence."""
        global_config = CodesignConfig(
            enabled=True,
            identity="Global Identity",
        )
        binary_config = CodesignConfig(
            enabled=True,
            identity="Binary Specific Identity",
        )

        merged = binary_config.merge_with(global_config)
        assert merged.identity == "Binary Specific Identity"


class TestBinaryConfigWithCodesign:
    """Tests for BinaryConfig with codesigning configuration."""

    def test_from_dict_with_codesign(self) -> None:
        """Test creating BinaryConfig with codesign settings."""
        data = {
            "source_patterns": ["src/**/*.go"],
            "build_cmd": "go build",
            "install_path": "~/.local/bin/mytool",
            "codesign": {
                "enabled": True,
                "identity": "Developer ID Application: Test",
            },
        }
        config = BinaryConfig.from_dict("mytool", data)
        assert config.codesign.enabled is True
        assert config.codesign.identity == "Developer ID Application: Test"

    def test_from_dict_without_codesign(self) -> None:
        """Test creating BinaryConfig without codesign settings."""
        data = {
            "source_patterns": ["src/**/*.go"],
            "build_cmd": "go build",
            "install_path": "~/.local/bin/mytool",
        }
        config = BinaryConfig.from_dict("mytool", data)
        assert config.codesign.enabled is False
        assert config.codesign.identity == "-"


class TestTrackConfigWithCodesign:
    """Tests for TrackConfig with global codesigning configuration."""

    def test_global_codesign_merged_with_binary(self) -> None:
        """Test that global codesign config is merged with binary config."""
        data: ConfigDict = {
            "binaries": {
                "tool1": {
                    "source_patterns": ["src/**/*.go"],
                    "build_cmd": "go build",
                    "install_path": "~/.local/bin/tool1",
                    # No codesign specified - should inherit global
                },
                "tool2": {
                    "source_patterns": ["src/**/*.rs"],
                    "build_cmd": "cargo build",
                    "install_path": "~/.local/bin/tool2",
                    "codesign": {
                        "enabled": True,
                        "identity": "Tool2 Specific",
                    },
                },
            },
            "codesign": {
                "enabled": True,
                "identity": "Global Identity",
                "options": ["runtime"],
            },
        }
        config = TrackConfig.from_dict(data)

        # tool1 should have global codesign config merged in
        assert config.binaries["tool1"].codesign.enabled is True
        assert config.binaries["tool1"].codesign.identity == "Global Identity"
        assert config.binaries["tool1"].codesign.options == ["runtime"]

        # tool2 should use its own identity but inherit other global settings
        assert config.binaries["tool2"].codesign.enabled is True
        assert config.binaries["tool2"].codesign.identity == "Tool2 Specific"


class TestLoadEnvConfigWithCodesign:
    """Tests for environment variable loading with codesigning."""

    def test_load_codesign_enabled(self, monkeypatch: MonkeyPatch) -> None:
        """Test loading codesign enabled from env."""
        monkeypatch.setenv("BINARY_TRACK_CODESIGN", "true")
        config = load_env_config()
        assert "codesign" in config
        assert config["codesign"]["enabled"] is True

    def test_load_codesign_identity(self, monkeypatch: MonkeyPatch) -> None:
        """Test loading codesign identity from env."""
        monkeypatch.setenv("BINARY_TRACK_CODESIGN_ID", "Test Identity")
        config = load_env_config()
        assert "codesign" in config
        assert config["codesign"]["identity"] == "Test Identity"

    def test_load_codesign_both(self, monkeypatch: MonkeyPatch) -> None:
        """Test loading both codesign settings from env."""
        monkeypatch.setenv("BINARY_TRACK_CODESIGN", "true")
        monkeypatch.setenv("BINARY_TRACK_CODESIGN_ID", "My Identity")
        config = load_env_config()
        assert config["codesign"]["enabled"] is True
        assert config["codesign"]["identity"] == "My Identity"


class TestCodesignStatus:
    """Tests for CodesignStatus enum."""

    def test_status_values(self) -> None:
        """Test all status enum values exist."""
        assert CodesignStatus.SIGNED.value == "signed"
        assert CodesignStatus.VALID.value == "valid"
        assert CodesignStatus.INVALID.value == "invalid"
        assert CodesignStatus.UNSIGNED.value == "unsigned"
        assert CodesignStatus.FAILED.value == "failed"
        assert CodesignStatus.SKIPPED.value == "skipped"
        assert CodesignStatus.NOT_SUPPORTED.value == "not_supported"


class TestCodesignResult:
    """Tests for CodesignResult dataclass."""

    def test_basic_result(self) -> None:
        """Test creating a basic codesign result."""
        result = CodesignResult(
            name="mytool",
            status=CodesignStatus.SIGNED,
            identity="Developer ID",
            message="Successfully signed",
        )
        assert result.name == "mytool"
        assert result.status == CodesignStatus.SIGNED
        assert result.identity == "Developer ID"
        assert result.message == "Successfully signed"


class TestCodesignBinary:
    """Tests for the codesign_binary function."""

    def test_codesign_not_enabled(self, tmp_path: Path) -> None:
        """Test that codesigning is skipped when not enabled."""
        from pre_commit.binary_track import Logger

        binary_config = BinaryConfig(
            name="mytool",
            install_path=str(tmp_path / "mytool"),
            codesign=CodesignConfig(enabled=False),
        )
        track_config = TrackConfig(root_dir=tmp_path)
        logger = Logger(quiet=True)

        result = codesign_binary(binary_config, track_config, logger)
        assert result.status == CodesignStatus.SKIPPED
        assert "not enabled" in result.message

    def test_codesign_binary_not_found(self, tmp_path: Path) -> None:
        """Test codesigning when binary doesn't exist."""
        from pre_commit.binary_track import Logger

        binary_config = BinaryConfig(
            name="mytool",
            install_path=str(tmp_path / "nonexistent"),
            codesign=CodesignConfig(enabled=True),
        )
        track_config = TrackConfig(root_dir=tmp_path)
        logger = Logger(quiet=True)

        # Only test if codesign is available
        if is_codesign_available():
            result = codesign_binary(binary_config, track_config, logger)
            assert result.status == CodesignStatus.FAILED
            assert "not found" in result.message

    def test_codesign_dry_run(self, tmp_path: Path) -> None:
        """Test codesigning in dry-run mode."""
        from pre_commit.binary_track import Logger

        # Create a fake binary
        binary_path = tmp_path / "mytool"
        binary_path.write_bytes(b"#!/bin/bash\necho test")
        binary_path.chmod(0o755)

        binary_config = BinaryConfig(
            name="mytool",
            install_path=str(binary_path),
            codesign=CodesignConfig(enabled=True, identity="-"),
        )
        track_config = TrackConfig(root_dir=tmp_path, dry_run=True)
        logger = Logger(quiet=True)

        # Only test if codesign is available
        if is_codesign_available():
            result = codesign_binary(binary_config, track_config, logger)
            assert result.status == CodesignStatus.SKIPPED
            assert "Would run" in result.message


class TestVerifySignature:
    """Tests for the verify_signature function."""

    def test_verify_missing_binary(self, tmp_path: Path) -> None:
        """Test verifying signature of missing binary."""
        from pre_commit.binary_track import Logger

        binary_path = tmp_path / "nonexistent"

        # Only test if codesign is available
        if is_codesign_available():
            logger = Logger(quiet=True)
            result = verify_signature(binary_path, logger)
            assert result.status == CodesignStatus.FAILED
            assert "not found" in result.message

    def test_verify_unsigned_binary(self, tmp_path: Path) -> None:
        """Test verifying an unsigned binary."""
        from pre_commit.binary_track import Logger

        # Create an unsigned binary
        binary_path = tmp_path / "unsigned_tool"
        binary_path.write_bytes(b"#!/bin/bash\necho test")
        binary_path.chmod(0o755)

        # Only test if codesign is available
        if is_codesign_available():
            logger = Logger(quiet=True)
            result = verify_signature(binary_path, logger)
            # An unsigned script should be reported as unsigned or invalid
            assert result.status in (CodesignStatus.UNSIGNED, CodesignStatus.INVALID)


class TestPlatform:
    """Tests for platform detection."""

    def test_platform_values(self) -> None:
        """Test platform enum values exist."""
        assert Platform.MACOS.value == "macos"
        assert Platform.LINUX.value == "linux"
        assert Platform.WINDOWS.value == "windows"
        assert Platform.UNKNOWN.value == "unknown"

    def test_get_current_platform(self) -> None:
        """Test getting current platform."""
        platform = get_current_platform()
        assert platform in [Platform.MACOS, Platform.LINUX, Platform.WINDOWS, Platform.UNKNOWN]

    def test_binary_type_values(self) -> None:
        """Test binary type enum values."""
        assert BinaryType.CLI.value == "cli"
        assert BinaryType.GUI.value == "gui"

    def test_install_scope_values(self) -> None:
        """Test install scope enum values."""
        assert InstallScope.USER.value == "user"
        assert InstallScope.SYSTEM.value == "system"


class TestInstallLocation:
    """Tests for InstallLocation dataclass."""

    def test_basic_location(self, tmp_path: Path) -> None:
        """Test creating a basic install location."""
        loc = InstallLocation(
            path=tmp_path,
            scope=InstallScope.USER,
            binary_type=BinaryType.CLI,
            platform=Platform.MACOS,
            description="Test location",
        )
        assert loc.path == tmp_path
        assert loc.scope == InstallScope.USER
        assert loc.binary_type == BinaryType.CLI
        assert loc.exists() is True  # tmp_path exists

    def test_exists_nonexistent(self, tmp_path: Path) -> None:
        """Test exists() returns False for nonexistent path."""
        loc = InstallLocation(
            path=tmp_path / "nonexistent",
            scope=InstallScope.USER,
            binary_type=BinaryType.CLI,
            platform=Platform.MACOS,
        )
        assert loc.exists() is False

    def test_is_writable(self, tmp_path: Path) -> None:
        """Test is_writable() for writable path."""
        loc = InstallLocation(
            path=tmp_path,
            scope=InstallScope.USER,
            binary_type=BinaryType.CLI,
            platform=Platform.MACOS,
        )
        assert loc.is_writable() is True


class TestDefaultInstallLocations:
    """Tests for get_default_install_locations function."""

    def test_get_all_locations(self) -> None:
        """Test getting all locations without filters."""
        locations = get_default_install_locations()
        assert len(locations) > 0
        assert all(isinstance(loc, InstallLocation) for loc in locations)

    def test_filter_by_binary_type(self) -> None:
        """Test filtering by binary type."""
        cli_locations = get_default_install_locations(binary_type=BinaryType.CLI)
        gui_locations = get_default_install_locations(binary_type=BinaryType.GUI)

        assert all(loc.binary_type == BinaryType.CLI for loc in cli_locations)
        assert all(loc.binary_type == BinaryType.GUI for loc in gui_locations)

    def test_filter_by_scope(self) -> None:
        """Test filtering by install scope."""
        user_locations = get_default_install_locations(scope=InstallScope.USER)
        system_locations = get_default_install_locations(scope=InstallScope.SYSTEM)

        assert all(loc.scope == InstallScope.USER for loc in user_locations)
        assert all(loc.scope == InstallScope.SYSTEM for loc in system_locations)

    def test_filter_combined(self) -> None:
        """Test combining filters."""
        locations = get_default_install_locations(
            binary_type=BinaryType.CLI,
            scope=InstallScope.USER,
        )
        assert all(
            loc.binary_type == BinaryType.CLI and loc.scope == InstallScope.USER
            for loc in locations
        )

    def test_macos_locations(self) -> None:
        """Test macOS-specific locations."""
        locations = get_default_install_locations(target_platform=Platform.MACOS)
        paths = [str(loc.path) for loc in locations]

        # Check for expected macOS paths
        assert any(".local/bin" in p for p in paths)
        assert any("Applications" in p for p in paths)

    def test_linux_locations(self) -> None:
        """Test Linux-specific locations."""
        locations = get_default_install_locations(target_platform=Platform.LINUX)
        paths = [str(loc.path) for loc in locations]

        # Check for expected Linux paths
        assert any(".local/bin" in p for p in paths)
        assert any("/usr/local/bin" in p for p in paths)

    def test_windows_locations(self) -> None:
        """Test Windows-specific locations."""
        locations = get_default_install_locations(target_platform=Platform.WINDOWS)
        paths = [str(loc.path) for loc in locations]

        # Windows locations should exist
        assert len(locations) > 0


class TestRecommendedInstallPath:
    """Tests for get_recommended_install_path function."""

    def test_default_cli_user(self) -> None:
        """Test default recommendation for CLI user tools."""
        path = get_recommended_install_path(BinaryType.CLI, InstallScope.USER)
        assert ".local/bin" in str(path) or "Programs" in str(path)

    def test_gui_user(self) -> None:
        """Test recommendation for GUI user apps."""
        path = get_recommended_install_path(BinaryType.GUI, InstallScope.USER)
        assert "Applications" in str(path) or "Programs" in str(path) or "opt" in str(path)


class TestEnsureInstallPath:
    """Tests for ensure_install_path_exists function."""

    def test_create_new_directory(self, tmp_path: Path) -> None:
        """Test creating a new directory."""
        new_path = tmp_path / "new" / "nested" / "dir"
        assert not new_path.exists()

        result = ensure_install_path_exists(new_path)
        assert result is True
        assert new_path.exists()

    def test_existing_directory(self, tmp_path: Path) -> None:
        """Test with existing directory."""
        result = ensure_install_path_exists(tmp_path)
        assert result is True


class TestPathInSystemPath:
    """Tests for is_path_in_system_path function."""

    def test_path_check(self, tmp_path: Path) -> None:
        """Test checking if path is in system PATH."""
        # A random temp path should not be in PATH
        result = is_path_in_system_path(tmp_path)
        assert result is False


class TestPathSetupInstructions:
    """Tests for get_path_setup_instructions function."""

    def test_instructions_returned(self, tmp_path: Path) -> None:
        """Test that instructions are returned."""
        instructions = get_path_setup_instructions(tmp_path)
        assert isinstance(instructions, str)
        assert len(instructions) > 0
        assert str(tmp_path) in instructions


class TestBinaryConfigWithInstallLocation:
    """Tests for BinaryConfig with install location features."""

    def test_from_dict_with_binary_type(self) -> None:
        """Test creating BinaryConfig with binary_type."""
        data = {
            "source_patterns": ["src/**/*.go"],
            "build_cmd": "go build",
            "install_path": "~/.local/bin/mytool",
            "binary_type": "cli",
            "install_scope": "user",
        }
        config = BinaryConfig.from_dict("mytool", data)
        assert config.binary_type == BinaryType.CLI
        assert config.install_scope == InstallScope.USER

    def test_from_dict_with_gui_type(self) -> None:
        """Test creating BinaryConfig with GUI type."""
        data = {
            "source_patterns": ["src/**/*.swift"],
            "build_cmd": "xcodebuild",
            "install_path": "~/Applications/MyApp.app",
            "binary_type": "gui",
            "install_scope": "user",
        }
        config = BinaryConfig.from_dict("myapp", data)
        assert config.binary_type == BinaryType.GUI
        assert config.install_scope == InstallScope.USER

    def test_default_install_path_auto_generated(self) -> None:
        """Test that install_path is auto-generated if not specified."""
        data = {
            "source_patterns": ["src/**/*.go"],
            "build_cmd": "go build",
            "binary_type": "cli",
            "install_scope": "user",
        }
        config = BinaryConfig.from_dict("mytool", data)
        # Should have an auto-generated path
        assert config.install_path != ""
        assert "mytool" in config.install_path

    def test_get_install_directory(self, tmp_path: Path) -> None:
        """Test get_install_directory method."""
        config = BinaryConfig(
            name="mytool",
            install_path=str(tmp_path / "bin" / "mytool"),
        )
        assert config.get_install_directory() == tmp_path / "bin"

    def test_ensure_install_directory(self, tmp_path: Path) -> None:
        """Test ensure_install_directory method."""
        new_dir = tmp_path / "new_bin"
        config = BinaryConfig(
            name="mytool",
            install_path=str(new_dir / "mytool"),
        )
        assert not new_dir.exists()
        result = config.ensure_install_directory()
        assert result is True
        assert new_dir.exists()


class TestShadowConflict:
    """Tests for ShadowConflict dataclass."""

    def test_basic_shadow_conflict(self) -> None:
        """Test creating a ShadowConflict."""
        from pre_commit.binary_track import ShadowConflict
        conflict = ShadowConflict(
            name="mytool",
            path=Path("/usr/local/bin/mytool"),
            scope=InstallScope.SYSTEM,
            binary_type=BinaryType.CLI,
            is_executable=True,
            description="System-wide CLI tools",
        )
        assert conflict.name == "mytool"
        assert conflict.scope == InstallScope.SYSTEM
        assert conflict.is_executable is True

    def test_shadow_conflict_str(self) -> None:
        """Test string representation of ShadowConflict."""
        from pre_commit.binary_track import ShadowConflict
        conflict = ShadowConflict(
            name="mytool",
            path=Path("/usr/local/bin/mytool"),
            scope=InstallScope.SYSTEM,
            binary_type=BinaryType.CLI,
        )
        result = str(conflict)
        assert "/usr/local/bin/mytool" in result
        assert "system" in result


class TestFindShadowConflicts:
    """Tests for find_shadow_conflicts function."""

    def test_no_conflicts_when_no_duplicates(self, tmp_path: Path) -> None:
        """Test that no conflicts are found when binary doesn't exist elsewhere."""
        from pre_commit.binary_track import find_shadow_conflicts
        # Use a unique name that won't exist anywhere
        conflicts = find_shadow_conflicts(
            "unique_nonexistent_binary_12345",
            tmp_path / "unique_nonexistent_binary_12345",
            BinaryType.CLI,
        )
        assert conflicts == []

    def test_finds_conflict_in_standard_location(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that conflicts are found in standard locations."""
        from pre_commit.binary_track import find_shadow_conflicts, get_default_install_locations

        # Create a fake "other location" with a binary
        other_bin = tmp_path / "system_bin"
        other_bin.mkdir()
        other_tool = other_bin / "mytool"
        other_tool.touch()
        other_tool.chmod(0o755)

        # Mock get_default_install_locations to return our test location
        original_func = get_default_install_locations

        def mock_locations(binary_type=None, scope=None, target_platform=None):
            from pre_commit.binary_track import InstallLocation, Platform
            return [
                InstallLocation(
                    path=other_bin,
                    scope=InstallScope.SYSTEM,
                    binary_type=BinaryType.CLI,
                    platform=Platform.MACOS,
                    description="Test system location",
                ),
            ]

        monkeypatch.setattr("pre_commit.binary_track.get_default_install_locations", mock_locations)

        # Check for conflicts from a user location
        user_install = tmp_path / "user_bin" / "mytool"
        conflicts = find_shadow_conflicts("mytool", user_install, BinaryType.CLI)

        assert len(conflicts) == 1
        assert conflicts[0][0] == other_tool
        assert conflicts[0][1] == InstallScope.SYSTEM

    def test_ignores_same_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that the installed binary's own path is not reported as a conflict."""
        from pre_commit.binary_track import find_shadow_conflicts, get_default_install_locations

        # Create a binary
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        tool = bin_dir / "mytool"
        tool.touch()

        # Mock locations to return the same location
        def mock_locations(binary_type=None, scope=None, target_platform=None):
            from pre_commit.binary_track import InstallLocation, Platform
            return [
                InstallLocation(
                    path=bin_dir,
                    scope=InstallScope.USER,
                    binary_type=BinaryType.CLI,
                    platform=Platform.MACOS,
                    description="Test location",
                ),
            ]

        monkeypatch.setattr("pre_commit.binary_track.get_default_install_locations", mock_locations)

        # Should not report itself as a conflict
        conflicts = find_shadow_conflicts("mytool", tool, BinaryType.CLI)
        assert conflicts == []


class TestPathPriority:
    """Tests for get_path_priority function."""

    def test_path_not_in_path(self, tmp_path: Path) -> None:
        """Test priority for path not in PATH."""
        from pre_commit.binary_track import get_path_priority
        result = get_path_priority(tmp_path)
        assert result == -1

    def test_path_in_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test priority for path in PATH."""
        from pre_commit.binary_track import get_path_priority
        test_path = Path("/test/path")
        monkeypatch.setenv("PATH", f"/first/path:/test/path:/third/path")
        result = get_path_priority(test_path)
        assert result == 1  # Second position (0-indexed)


class TestCheckShadowPriority:
    """Tests for check_shadow_priority function."""

    def test_neither_in_path(self, tmp_path: Path) -> None:
        """Test when neither path is in PATH."""
        from pre_commit.binary_track import check_shadow_priority
        result = check_shadow_priority(
            tmp_path / "install" / "tool",
            tmp_path / "conflict" / "tool",
        )
        assert "neither in PATH" in result

    def test_install_takes_priority(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Test when install path takes priority."""
        from pre_commit.binary_track import check_shadow_priority
        install_dir = tmp_path / "first"
        conflict_dir = tmp_path / "second"
        monkeypatch.setenv("PATH", f"{install_dir}:{conflict_dir}")

        result = check_shadow_priority(
            install_dir / "tool",
            conflict_dir / "tool",
        )
        assert "takes priority" in result

    def test_shadowed_by_conflict(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Test when install is shadowed by conflict."""
        from pre_commit.binary_track import check_shadow_priority
        install_dir = tmp_path / "second"
        conflict_dir = tmp_path / "first"
        monkeypatch.setenv("PATH", f"{conflict_dir}:{install_dir}")

        result = check_shadow_priority(
            install_dir / "tool",
            conflict_dir / "tool",
        )
        assert "shadowed by" in result


class TestBinaryStatusResultWithShadows:
    """Tests for BinaryStatusResult with shadow_conflicts field."""

    def test_status_result_has_shadow_conflicts(self) -> None:
        """Test that BinaryStatusResult includes shadow_conflicts."""
        from pre_commit.binary_track import BinaryStatusResult, ShadowConflict
        status = BinaryStatusResult(
            name="mytool",
            status=BinaryStatus.CURRENT,
        )
        assert hasattr(status, "shadow_conflicts")
        assert status.shadow_conflicts == []

    def test_status_result_with_conflicts(self) -> None:
        """Test BinaryStatusResult with shadow conflicts."""
        from pre_commit.binary_track import BinaryStatusResult, ShadowConflict
        conflict = ShadowConflict(
            name="mytool",
            path=Path("/usr/local/bin/mytool"),
            scope=InstallScope.SYSTEM,
            binary_type=BinaryType.CLI,
            is_executable=True,
        )
        status = BinaryStatusResult(
            name="mytool",
            status=BinaryStatus.CURRENT,
            shadow_conflicts=[conflict],
        )
        assert len(status.shadow_conflicts) == 1
        assert status.shadow_conflicts[0].path == Path("/usr/local/bin/mytool")


class TestTrackResultWithShadows:
    """Tests for TrackResult JSON output with shadow_conflicts."""

    def test_to_dict_includes_shadow_conflicts(self) -> None:
        """Test that to_dict includes shadow_conflicts in output."""
        from pre_commit.binary_track import TrackResult, BinaryStatusResult, ShadowConflict
        conflict = ShadowConflict(
            name="mytool",
            path=Path("/usr/local/bin/mytool"),
            scope=InstallScope.SYSTEM,
            binary_type=BinaryType.CLI,
            is_executable=True,
            description="System CLI tools",
        )
        status = BinaryStatusResult(
            name="mytool",
            status=BinaryStatus.CURRENT,
            install_path="~/.local/bin/mytool",
            shadow_conflicts=[conflict],
        )
        result = TrackResult(statuses=[status])
        output = result.to_dict()

        assert "statuses" in output
        assert len(output["statuses"]) == 1
        assert "shadow_conflicts" in output["statuses"][0]
        assert len(output["statuses"][0]["shadow_conflicts"]) == 1
        assert output["statuses"][0]["shadow_conflicts"][0]["path"] == "/usr/local/bin/mytool"
        assert output["statuses"][0]["shadow_conflicts"][0]["scope"] == "system"
        assert output["statuses"][0]["shadow_conflicts"][0]["is_executable"] is True
