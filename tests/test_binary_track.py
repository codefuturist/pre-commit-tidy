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
    BuildManifest,
    BuildRecord,
    ConfigDict,
    PreCommitPolicy,
    RebuildStatus,
    TrackConfig,
    TrackingMethod,
    check_binary_health,
    compute_file_hash,
    expand_patterns,
    get_current_commit,
    get_source_fingerprint,
    is_binary_stale,
    load_config_file,
    load_env_config,
    load_manifest,
    save_manifest,
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
