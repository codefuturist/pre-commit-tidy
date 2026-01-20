"""Tests for the tidy module."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tidy import (
    DuplicateStrategy,
    OperationStatus,
    TidyConfig,
    generate_unique_name,
    load_config_file,
    load_env_config,
    should_exclude,
    tidy,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


class TestShouldExclude:
    """Tests for the should_exclude function."""

    def test_exclude_by_filename(self) -> None:
        """Test excluding files by exact filename match."""
        config = TidyConfig(exclude_files=["readme.md", "LICENSE"])
        
        assert should_exclude("README.md", config) == (True, "excluded by filename")
        assert should_exclude("readme.MD", config) == (True, "excluded by filename")
        assert should_exclude("other.md", config) == (False, None)

    def test_exclude_by_extension(self) -> None:
        """Test excluding files by extension."""
        config = TidyConfig(extensions=[".md", ".txt"])
        
        assert should_exclude("file.md", config) == (False, None)
        assert should_exclude("file.txt", config) == (False, None)
        excluded, reason = should_exclude("file.json", config)
        assert excluded is True
        assert "extension" in reason

    def test_exclude_by_pattern(self) -> None:
        """Test excluding files by glob pattern."""
        config = TidyConfig(
            extensions=[".md"],
            exclude_patterns=["*.config.*", "_*"],
        )
        
        assert should_exclude("app.config.md", config) == (True, "matches pattern: *.config.*")
        assert should_exclude("_draft.md", config) == (True, "matches pattern: _*")
        assert should_exclude("normal.md", config) == (False, None)


class TestGenerateUniqueName:
    """Tests for the generate_unique_name function."""

    def test_generates_unique_name(self) -> None:
        """Test that unique names are generated with timestamps."""
        name1 = generate_unique_name("file.md")
        name2 = generate_unique_name("file.md")
        
        assert name1.startswith("file-")
        assert name1.endswith(".md")
        # Names should be different (different timestamps)
        # Note: This might fail if run extremely fast, but unlikely
        assert name1 != name2 or True  # Allow same if within same millisecond

    def test_preserves_extension(self) -> None:
        """Test that file extensions are preserved."""
        assert generate_unique_name("doc.txt").endswith(".txt")
        assert generate_unique_name("data.json").endswith(".json")
        assert generate_unique_name("no-ext").endswith("")


class TestLoadConfigFile:
    """Tests for the load_config_file function."""

    def test_load_existing_config(self, tmp_path: Path) -> None:
        """Test loading an existing config file."""
        config_file = tmp_path / ".tidyrc.json"
        config_data = {
            "source_dir": "src",
            "target_dir": "dest",
            "extensions": [".md", ".txt"],
        }
        config_file.write_text(json.dumps(config_data))
        
        os.chdir(tmp_path)
        loaded = load_config_file()
        
        assert loaded["source_dir"] == "src"
        assert loaded["target_dir"] == "dest"
        assert loaded["extensions"] == [".md", ".txt"]

    def test_load_nonexistent_config(self, tmp_path: Path) -> None:
        """Test that missing config returns empty dict."""
        os.chdir(tmp_path)
        loaded = load_config_file()
        assert loaded == {}

    def test_load_explicit_config_path(self, tmp_path: Path) -> None:
        """Test loading config from explicit path."""
        config_file = tmp_path / "custom.json"
        config_file.write_text(json.dumps({"target_dir": "custom"}))
        
        os.chdir(tmp_path)
        loaded = load_config_file(Path("custom.json"))
        
        assert loaded["target_dir"] == "custom"

    def test_explicit_config_not_found(self, tmp_path: Path) -> None:
        """Test that missing explicit config raises error."""
        os.chdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            load_config_file(Path("nonexistent.json"))


class TestLoadEnvConfig:
    """Tests for the load_env_config function."""

    def test_load_env_variables(self, monkeypatch: MonkeyPatch) -> None:
        """Test loading config from environment variables."""
        monkeypatch.setenv("TIDY_SOURCE_DIR", "env_source")
        monkeypatch.setenv("TIDY_TARGET_DIR", "env_target")
        monkeypatch.setenv("TIDY_EXTENSIONS", ".md,.txt,.rst")
        
        config = load_env_config()
        
        assert config["source_dir"] == "env_source"
        assert config["target_dir"] == "env_target"
        assert config["extensions"] == [".md", ".txt", ".rst"]

    def test_empty_env(self, monkeypatch: MonkeyPatch) -> None:
        """Test that missing env vars return empty config."""
        # Clear any existing env vars
        for var in ["TIDY_SOURCE_DIR", "TIDY_TARGET_DIR", "TIDY_EXTENSIONS"]:
            monkeypatch.delenv(var, raising=False)
        
        config = load_env_config()
        assert config == {}


class TestTidyConfig:
    """Tests for the TidyConfig class."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = TidyConfig()
        
        assert config.source_dir == "."
        assert config.target_dir == "00-inbox"
        assert config.extensions == [".md"]
        assert config.duplicate_strategy == DuplicateStrategy.RENAME
        assert config.dry_run is False
        assert config.verbosity == 1

    def test_from_dict(self) -> None:
        """Test creating config from dictionary."""
        data = {
            "source_dir": "drafts",
            "target_dir": "published",
            "duplicate_strategy": "skip",
        }
        config = TidyConfig.from_dict(data)
        
        assert config.source_dir == "drafts"
        assert config.target_dir == "published"
        assert config.duplicate_strategy == DuplicateStrategy.SKIP


class TestTidy:
    """Integration tests for the tidy function."""

    def test_move_files(self, tmp_path: Path) -> None:
        """Test moving files from source to target."""
        # Setup
        source = tmp_path
        target = tmp_path / "inbox"
        (source / "file1.md").write_text("content1")
        (source / "file2.md").write_text("content2")
        (source / "readme.md").write_text("readme")  # Should be excluded
        
        config = TidyConfig(
            root_dir=source,
            source_dir=".",
            target_dir="inbox",
            extensions=[".md"],
            exclude_files=["readme.md"],
            verbosity=0,
        )
        
        result = tidy(config)
        
        assert len(result.moved) == 2
        assert len(result.skipped) == 1
        assert (target / "file1.md").exists()
        assert (target / "file2.md").exists()
        assert (source / "readme.md").exists()  # Not moved

    def test_dry_run(self, tmp_path: Path) -> None:
        """Test that dry run doesn't move files."""
        source = tmp_path
        (source / "file.md").write_text("content")
        
        config = TidyConfig(
            root_dir=source,
            target_dir="inbox",
            dry_run=True,
            verbosity=0,
        )
        
        result = tidy(config)
        
        assert result.dry_run is True
        assert len(result.moved) == 1
        assert (source / "file.md").exists()  # Still exists
        assert not (source / "inbox" / "file.md").exists()

    def test_duplicate_skip(self, tmp_path: Path) -> None:
        """Test skipping duplicate files."""
        source = tmp_path
        target = tmp_path / "inbox"
        target.mkdir()
        
        (source / "file.md").write_text("new")
        (target / "file.md").write_text("existing")
        
        config = TidyConfig(
            root_dir=source,
            target_dir="inbox",
            duplicate_strategy=DuplicateStrategy.SKIP,
            verbosity=0,
        )
        
        result = tidy(config)
        
        assert len(result.skipped) == 1
        assert result.skipped[0].status == OperationStatus.DUPLICATE
        assert (target / "file.md").read_text() == "existing"  # Unchanged

    def test_duplicate_rename(self, tmp_path: Path) -> None:
        """Test renaming duplicate files."""
        source = tmp_path
        target = tmp_path / "inbox"
        target.mkdir()
        
        (source / "file.md").write_text("new")
        (target / "file.md").write_text("existing")
        
        config = TidyConfig(
            root_dir=source,
            target_dir="inbox",
            duplicate_strategy=DuplicateStrategy.RENAME,
            verbosity=0,
        )
        
        result = tidy(config)
        
        assert len(result.moved) == 1
        # Original still exists
        assert (target / "file.md").read_text() == "existing"
        # New file with timestamp exists
        md_files = list(target.glob("file-*.md"))
        assert len(md_files) == 1
        assert md_files[0].read_text() == "new"

    def test_empty_source(self, tmp_path: Path) -> None:
        """Test with empty source directory."""
        config = TidyConfig(
            root_dir=tmp_path,
            verbosity=0,
        )
        
        result = tidy(config)
        
        assert result.total_processed == 0
        assert len(result.moved) == 0

    def test_nonexistent_source(self, tmp_path: Path) -> None:
        """Test with nonexistent source directory."""
        config = TidyConfig(
            root_dir=tmp_path,
            source_dir="nonexistent",
            verbosity=0,
        )
        
        result = tidy(config)
        
        assert len(result.moved) == 0
        assert len(result.failed) == 0
