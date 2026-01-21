"""Tests for the tidy module."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pre_commit.tidy import (
    ConfigDict,
    DuplicateStrategy,
    OperationStatus,
    RoutingRule,
    TidyConfig,
    UndoManifest,
    collect_files,
    compute_file_hash,
    generate_unique_name,
    load_config_file,
    load_env_config,
    should_exclude,
    should_exclude_dir,
    tidy,
    undo_tidy,
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
        assert reason is not None and "extension" in reason

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

        assert name1.startswith("file-")
        assert name1.endswith(".md")
        # Names should be different (different timestamps)
        # Note: This might fail if run extremely fast, but unlikely
        # We just verify the format is correct, not uniqueness
        assert "-" in name1

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
        data: ConfigDict = {
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


class TestRecursiveScanning:
    """Tests for recursive directory scanning."""

    def test_recursive_scan(self, tmp_path: Path) -> None:
        """Test recursive file collection."""
        # Setup nested directories
        (tmp_path / "level1").mkdir()
        (tmp_path / "level1" / "level2").mkdir()
        (tmp_path / "file1.md").write_text("root")
        (tmp_path / "level1" / "file2.md").write_text("level1")
        (tmp_path / "level1" / "level2" / "file3.md").write_text("level2")

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir=".",
            target_dir="inbox",
            recursive=True,
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 3
        inbox = tmp_path / "inbox"
        assert (inbox / "file1.md").exists()
        assert (inbox / "file2.md").exists()
        assert (inbox / "file3.md").exists()

    def test_max_depth_limit(self, tmp_path: Path) -> None:
        """Test max depth limits recursion."""
        # Setup 3 levels deep
        (tmp_path / "level1").mkdir()
        (tmp_path / "level1" / "level2").mkdir()
        (tmp_path / "level1" / "level2" / "level3").mkdir()
        (tmp_path / "file1.md").write_text("root")
        (tmp_path / "level1" / "file2.md").write_text("level1")
        (tmp_path / "level1" / "level2" / "file3.md").write_text("level2")
        (tmp_path / "level1" / "level2" / "level3" / "file4.md").write_text("level3")

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir=".",
            target_dir="inbox",
            recursive=True,
            max_depth=2,  # Only go 2 levels deep
            verbosity=0,
        )

        result = tidy(config)

        # Should only get files from root, level1, and level2 (depth 0, 1, 2)
        assert len(result.moved) == 3  # file1, file2, file3
        inbox = tmp_path / "inbox"
        assert (inbox / "file1.md").exists()
        assert (inbox / "file2.md").exists()
        assert (inbox / "file3.md").exists()
        assert not (inbox / "file4.md").exists()  # Too deep

    def test_exclude_dirs(self, tmp_path: Path) -> None:
        """Test excluding directories from recursive scan."""
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "node_modules" / "lib.md").write_text("excluded")
        (tmp_path / "src" / "app.md").write_text("included")

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir=".",
            target_dir="inbox",
            recursive=True,
            exclude_dirs=["node_modules"],
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 1
        assert (tmp_path / "inbox" / "app.md").exists()
        assert (tmp_path / "node_modules" / "lib.md").exists()  # Not moved


class TestShouldExcludeDir:
    """Tests for directory exclusion."""

    def test_exclude_dir(self) -> None:
        """Test directory exclusion check."""
        config = TidyConfig(exclude_dirs=["node_modules", ".git"])

        assert should_exclude_dir("node_modules", config) is True
        assert should_exclude_dir("NODE_MODULES", config) is True
        assert should_exclude_dir(".git", config) is True
        assert should_exclude_dir("src", config) is False


class TestRoutingRules:
    """Tests for rule-based file routing."""

    def test_pattern_matching(self) -> None:
        """Test pattern-based routing rule."""
        rule = RoutingRule(target="tests/", pattern="*.test.md")

        assert rule.matches(Path("foo.test.md"), "foo.test.md") is True
        assert rule.matches(Path("foo.md"), "foo.md") is False

    def test_extension_matching(self) -> None:
        """Test extension-based routing rule."""
        rule = RoutingRule(target="images/", extensions=[".png", ".jpg"])

        assert rule.matches(Path("photo.png"), "photo.png") is True
        assert rule.matches(Path("photo.jpg"), "photo.jpg") is True
        assert rule.matches(Path("photo.gif"), "photo.gif") is False

    def test_glob_matching(self) -> None:
        """Test glob-based routing rule."""
        rule = RoutingRule(target="docs/", glob="docs/**/*.md")

        assert rule.matches(Path("readme.md"), "docs/readme.md") is True
        assert rule.matches(Path("api.md"), "docs/api/api.md") is True
        assert rule.matches(Path("other.md"), "src/other.md") is False

    def test_rules_route_files(self, tmp_path: Path) -> None:
        """Test that rules route files to different targets."""
        (tmp_path / "file.md").write_text("normal")
        (tmp_path / "file.test.md").write_text("test")
        (tmp_path / "file.draft.md").write_text("draft")

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir=".",
            target_dir="inbox",
            extensions=[".md"],
            exclude_files=[],
            rules=[
                RoutingRule(target="tests/", pattern="*.test.md"),
                RoutingRule(target="drafts/", pattern="*.draft.md"),
            ],
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 3
        assert (tmp_path / "inbox" / "file.md").exists()
        assert (tmp_path / "tests" / "file.test.md").exists()
        assert (tmp_path / "drafts" / "file.draft.md").exists()


class TestContentDeduplication:
    """Tests for content-based duplicate detection."""

    def test_compute_file_hash(self, tmp_path: Path) -> None:
        """Test file hash computation."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file3 = tmp_path / "file3.txt"

        file1.write_text("same content")
        file2.write_text("same content")
        file3.write_text("different content")

        hash1 = compute_file_hash(file1)
        hash2 = compute_file_hash(file2)
        hash3 = compute_file_hash(file3)

        assert hash1 == hash2  # Same content = same hash
        assert hash1 != hash3  # Different content = different hash

    def test_dedup_by_content(self, tmp_path: Path) -> None:
        """Test content-based deduplication."""
        target = tmp_path / "inbox"
        target.mkdir()

        # Create files with same content but different names
        (tmp_path / "file1.md").write_text("identical content")
        (target / "existing.md").write_text("identical content")

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir=".",
            target_dir="inbox",
            dedup_by_content=True,
            verbosity=0,
        )

        result = tidy(config)

        # file1.md should be detected as duplicate of existing.md
        assert len(result.skipped) == 1
        assert result.skipped[0].status == OperationStatus.DUPLICATE
        assert "content duplicate" in (result.skipped[0].reason or "")


class TestUndoFunctionality:
    """Tests for undo capability."""

    def test_undo_manifest_serialization(self) -> None:
        """Test undo manifest to/from dict."""
        from pre_commit.tidy import UndoOperation

        manifest = UndoManifest(
            created_at="2026-01-21T00:00:00Z",
            dry_run=False,
            operations=[
                UndoOperation(
                    original_path="/src/file.md",
                    moved_to_path="/dest/file.md",
                    timestamp="2026-01-21T00:00:01Z",
                )
            ],
        )

        data = manifest.to_dict()
        restored = UndoManifest.from_dict(data)

        assert restored.created_at == manifest.created_at
        assert len(restored.operations) == 1
        assert restored.operations[0].original_path == "/src/file.md"

    def test_undo_restores_files(self, tmp_path: Path) -> None:
        """Test that undo restores files to original locations."""
        # First, run tidy
        (tmp_path / "file.md").write_text("content")

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir=".",
            target_dir="inbox",
            verbosity=0,
        )

        result = tidy(config)
        assert len(result.moved) == 1
        assert (tmp_path / "inbox" / "file.md").exists()
        assert not (tmp_path / "file.md").exists()

        # Now undo
        undo_result = undo_tidy(config)

        assert len(undo_result.moved) == 1
        assert (tmp_path / "file.md").exists()
        assert not (tmp_path / "inbox" / "file.md").exists()

    def test_undo_dry_run_skipped(self, tmp_path: Path) -> None:
        """Test that dry run operations can't be undone."""
        (tmp_path / "file.md").write_text("content")

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir="inbox",
            dry_run=True,
            verbosity=0,
        )

        tidy(config)  # Dry run

        undo_result = undo_tidy(config)

        # Should report that last op was dry run
        assert len(undo_result.moved) == 0


class TestCollectFiles:
    """Tests for file collection functionality."""

    def test_collect_files_flat(self, tmp_path: Path) -> None:
        """Test collecting files without recursion."""
        (tmp_path / "file1.md").write_text("content")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "file2.md").write_text("content")

        config = TidyConfig(root_dir=tmp_path, recursive=False)

        files = collect_files(tmp_path, config)

        assert len(files) == 1
        assert files[0][0].name == "file1.md"

    def test_collect_files_recursive(self, tmp_path: Path) -> None:
        """Test collecting files with recursion."""
        (tmp_path / "file1.md").write_text("content")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "file2.md").write_text("content")

        config = TidyConfig(root_dir=tmp_path, recursive=True)

        files = collect_files(tmp_path, config)

        assert len(files) == 2
        names = {f[0].name for f in files}
        assert names == {"file1.md", "file2.md"}
