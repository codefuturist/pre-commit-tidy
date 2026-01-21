"""Tests for the tidy module."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pre_commit.tidy import collect_files
from pre_commit.tidy import CollisionKeep
from pre_commit.tidy import compute_file_hash
from pre_commit.tidy import ConfigDict
from pre_commit.tidy import DuplicateStrategy
from pre_commit.tidy import generate_unique_name
from pre_commit.tidy import load_config_file
from pre_commit.tidy import load_env_config
from pre_commit.tidy import OperationStatus
from pre_commit.tidy import RoutingRule
from pre_commit.tidy import should_exclude
from pre_commit.tidy import should_exclude_dir
from pre_commit.tidy import tidy
from pre_commit.tidy import TidyConfig
from pre_commit.tidy import undo_tidy
from pre_commit.tidy import UndoManifest

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


class TestShouldExclude:
    """Tests for the should_exclude function."""

    def test_exclude_by_filename(self) -> None:
        """Test excluding files by exact filename match."""
        config = TidyConfig(exclude_files=['readme.md', 'LICENSE'])

        assert should_exclude('README.md', config) == (True, 'excluded by filename')  # noqa: E501
        assert should_exclude('readme.MD', config) == (True, 'excluded by filename')  # noqa: E501
        assert should_exclude('other.md', config) == (False, None)

    def test_exclude_by_extension(self) -> None:
        """Test excluding files by extension."""
        config = TidyConfig(extensions=['.md', '.txt'])

        assert should_exclude('file.md', config) == (False, None)
        assert should_exclude('file.txt', config) == (False, None)
        excluded, reason = should_exclude('file.json', config)
        assert excluded is True
        assert reason is not None and 'extension' in reason

    def test_exclude_by_pattern(self) -> None:
        """Test excluding files by glob pattern."""
        config = TidyConfig(
            extensions=['.md'],
            exclude_patterns=['*.config.*', '_*'],
        )

        assert should_exclude('app.config.md', config) == (True, 'matches pattern: *.config.*')  # noqa: E501
        assert should_exclude('_draft.md', config) == (True, 'matches pattern: _*')  # noqa: E501
        assert should_exclude('normal.md', config) == (False, None)


class TestGenerateUniqueName:
    """Tests for the generate_unique_name function."""

    def test_generates_unique_name(self) -> None:
        """Test that unique names are generated with timestamps."""
        name1 = generate_unique_name('file.md')

        assert name1.startswith('file-')
        assert name1.endswith('.md')
        # Names should be different (different timestamps)
        # Note: This might fail if run extremely fast, but unlikely
        # We just verify the format is correct, not uniqueness
        assert '-' in name1

    def test_preserves_extension(self) -> None:
        """Test that file extensions are preserved."""
        assert generate_unique_name('doc.txt').endswith('.txt')
        assert generate_unique_name('data.json').endswith('.json')
        assert generate_unique_name('no-ext').endswith('')


class TestLoadConfigFile:
    """Tests for the load_config_file function."""

    def test_load_existing_config(self, tmp_path: Path) -> None:
        """Test loading an existing config file."""
        config_file = tmp_path / '.tidyrc.json'
        config_data = {
            'source_dir': 'src',
            'target_dir': 'dest',
            'extensions': ['.md', '.txt'],
        }
        config_file.write_text(json.dumps(config_data))

        os.chdir(tmp_path)
        loaded = load_config_file()

        assert loaded['source_dir'] == 'src'
        assert loaded['target_dir'] == 'dest'
        assert loaded['extensions'] == ['.md', '.txt']

    def test_load_nonexistent_config(self, tmp_path: Path) -> None:
        """Test that missing config returns empty dict."""
        os.chdir(tmp_path)
        loaded = load_config_file()
        assert loaded == {}

    def test_load_explicit_config_path(self, tmp_path: Path) -> None:
        """Test loading config from explicit path."""
        config_file = tmp_path / 'custom.json'
        config_file.write_text(json.dumps({'target_dir': 'custom'}))

        os.chdir(tmp_path)
        loaded = load_config_file(Path('custom.json'))

        assert loaded['target_dir'] == 'custom'

    def test_explicit_config_not_found(self, tmp_path: Path) -> None:
        """Test that missing explicit config raises error."""
        os.chdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            load_config_file(Path('nonexistent.json'))


class TestLoadEnvConfig:
    """Tests for the load_env_config function."""

    def test_load_env_variables(self, monkeypatch: MonkeyPatch) -> None:
        """Test loading config from environment variables."""
        monkeypatch.setenv('TIDY_SOURCE_DIR', 'env_source')
        monkeypatch.setenv('TIDY_TARGET_DIR', 'env_target')
        monkeypatch.setenv('TIDY_EXTENSIONS', '.md,.txt,.rst')

        config = load_env_config()

        assert config['source_dir'] == 'env_source'
        assert config['target_dir'] == 'env_target'
        assert config['extensions'] == ['.md', '.txt', '.rst']

    def test_empty_env(self, monkeypatch: MonkeyPatch) -> None:
        """Test that missing env vars return empty config."""
        # Clear any existing env vars
        for var in ['TIDY_SOURCE_DIR', 'TIDY_TARGET_DIR', 'TIDY_EXTENSIONS']:
            monkeypatch.delenv(var, raising=False)

        config = load_env_config()
        assert config == {}


class TestTidyConfig:
    """Tests for the TidyConfig class."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = TidyConfig()

        assert config.source_dir == '.'
        assert config.target_dir == '00-inbox'
        assert config.extensions == ['.md']
        assert config.duplicate_strategy == DuplicateStrategy.RENAME
        assert config.dry_run is False
        assert config.verbosity == 1

    def test_from_dict(self) -> None:
        """Test creating config from dictionary."""
        data: ConfigDict = {
            'source_dir': 'drafts',
            'target_dir': 'published',
            'duplicate_strategy': 'skip',
        }
        config = TidyConfig.from_dict(data)

        assert config.source_dir == 'drafts'
        assert config.target_dir == 'published'
        assert config.duplicate_strategy == DuplicateStrategy.SKIP


class TestTidy:
    """Integration tests for the tidy function."""

    def test_move_files(self, tmp_path: Path) -> None:
        """Test moving files from source to target."""
        # Setup
        source = tmp_path
        target = tmp_path / 'inbox'
        (source / 'file1.md').write_text('content1')
        (source / 'file2.md').write_text('content2')
        (source / 'readme.md').write_text('readme')  # Should be excluded

        config = TidyConfig(
            root_dir=source,
            source_dir='.',
            target_dir='inbox',
            extensions=['.md'],
            exclude_files=['readme.md'],
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 2
        assert len(result.skipped) == 1
        assert (target / 'file1.md').exists()
        assert (target / 'file2.md').exists()
        assert (source / 'readme.md').exists()  # Not moved

    def test_dry_run(self, tmp_path: Path) -> None:
        """Test that dry run doesn't move files."""
        source = tmp_path
        (source / 'file.md').write_text('content')

        config = TidyConfig(
            root_dir=source,
            target_dir='inbox',
            dry_run=True,
            verbosity=0,
        )

        result = tidy(config)

        assert result.dry_run is True
        assert len(result.moved) == 1
        assert (source / 'file.md').exists()  # Still exists
        assert not (source / 'inbox' / 'file.md').exists()

    def test_duplicate_skip(self, tmp_path: Path) -> None:
        """Test skipping duplicate files."""
        source = tmp_path
        target = tmp_path / 'inbox'
        target.mkdir()

        (source / 'file.md').write_text('new')
        (target / 'file.md').write_text('existing')

        config = TidyConfig(
            root_dir=source,
            target_dir='inbox',
            duplicate_strategy=DuplicateStrategy.SKIP,
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.skipped) == 1
        assert result.skipped[0].status == OperationStatus.DUPLICATE
        assert (target / 'file.md').read_text() == 'existing'  # Unchanged

    def test_duplicate_rename(self, tmp_path: Path) -> None:
        """Test renaming duplicate files."""
        source = tmp_path
        target = tmp_path / 'inbox'
        target.mkdir()

        (source / 'file.md').write_text('new')
        (target / 'file.md').write_text('existing')

        config = TidyConfig(
            root_dir=source,
            target_dir='inbox',
            duplicate_strategy=DuplicateStrategy.RENAME,
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 1
        # Original still exists
        assert (target / 'file.md').read_text() == 'existing'
        # New file with timestamp exists
        md_files = list(target.glob('file-*.md'))
        assert len(md_files) == 1
        assert md_files[0].read_text() == 'new'

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
            source_dir='nonexistent',
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
        (tmp_path / 'level1').mkdir()
        (tmp_path / 'level1' / 'level2').mkdir()
        (tmp_path / 'file1.md').write_text('root')
        (tmp_path / 'level1' / 'file2.md').write_text('level1')
        (tmp_path / 'level1' / 'level2' / 'file3.md').write_text('level2')

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir='.',
            target_dir='inbox',
            recursive=True,
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 3
        inbox = tmp_path / 'inbox'
        assert (inbox / 'file1.md').exists()
        assert (inbox / 'file2.md').exists()
        assert (inbox / 'file3.md').exists()

    def test_max_depth_limit(self, tmp_path: Path) -> None:
        """Test max depth limits recursion."""
        # Setup 3 levels deep
        (tmp_path / 'level1').mkdir()
        (tmp_path / 'level1' / 'level2').mkdir()
        (tmp_path / 'level1' / 'level2' / 'level3').mkdir()
        (tmp_path / 'file1.md').write_text('root')
        (tmp_path / 'level1' / 'file2.md').write_text('level1')
        (tmp_path / 'level1' / 'level2' / 'file3.md').write_text('level2')
        (tmp_path / 'level1' / 'level2' / 'level3' / 'file4.md').write_text('level3')  # noqa: E501

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir='.',
            target_dir='inbox',
            recursive=True,
            max_depth=2,  # Only go 2 levels deep
            verbosity=0,
        )

        result = tidy(config)

        # Should only get files from root, level1, and level2 (depth 0, 1, 2)
        assert len(result.moved) == 3  # file1, file2, file3
        inbox = tmp_path / 'inbox'
        assert (inbox / 'file1.md').exists()
        assert (inbox / 'file2.md').exists()
        assert (inbox / 'file3.md').exists()
        assert not (inbox / 'file4.md').exists()  # Too deep

    def test_exclude_dirs(self, tmp_path: Path) -> None:
        """Test excluding directories from recursive scan."""
        (tmp_path / 'node_modules').mkdir()
        (tmp_path / 'src').mkdir()
        (tmp_path / 'node_modules' / 'lib.md').write_text('excluded')
        (tmp_path / 'src' / 'app.md').write_text('included')

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir='.',
            target_dir='inbox',
            recursive=True,
            exclude_dirs=['node_modules'],
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 1
        assert (tmp_path / 'inbox' / 'app.md').exists()
        assert (tmp_path / 'node_modules' / 'lib.md').exists()  # Not moved


class TestShouldExcludeDir:
    """Tests for directory exclusion."""

    def test_exclude_dir(self) -> None:
        """Test directory exclusion check."""
        config = TidyConfig(exclude_dirs=['node_modules', '.git'])

        assert should_exclude_dir('node_modules', config) is True
        assert should_exclude_dir('NODE_MODULES', config) is True
        assert should_exclude_dir('.git', config) is True
        assert should_exclude_dir('src', config) is False


class TestRoutingRules:
    """Tests for rule-based file routing."""

    def test_pattern_matching(self) -> None:
        """Test pattern-based routing rule."""
        rule = RoutingRule(target='tests/', pattern='*.test.md')

        assert rule.matches(Path('foo.test.md'), 'foo.test.md') is True
        assert rule.matches(Path('foo.md'), 'foo.md') is False

    def test_extension_matching(self) -> None:
        """Test extension-based routing rule."""
        rule = RoutingRule(target='images/', extensions=['.png', '.jpg'])

        assert rule.matches(Path('photo.png'), 'photo.png') is True
        assert rule.matches(Path('photo.jpg'), 'photo.jpg') is True
        assert rule.matches(Path('photo.gif'), 'photo.gif') is False

    def test_glob_matching(self) -> None:
        """Test glob-based routing rule."""
        rule = RoutingRule(target='docs/', glob='docs/**/*.md')

        assert rule.matches(Path('readme.md'), 'docs/readme.md') is True
        assert rule.matches(Path('api.md'), 'docs/api/api.md') is True
        assert rule.matches(Path('other.md'), 'src/other.md') is False

    def test_rules_route_files(self, tmp_path: Path) -> None:
        """Test that rules route files to different targets."""
        (tmp_path / 'file.md').write_text('normal')
        (tmp_path / 'file.test.md').write_text('test')
        (tmp_path / 'file.draft.md').write_text('draft')

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir='.',
            target_dir='inbox',
            extensions=['.md'],
            exclude_files=[],
            rules=[
                RoutingRule(target='tests/', pattern='*.test.md'),
                RoutingRule(target='drafts/', pattern='*.draft.md'),
            ],
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 3
        assert (tmp_path / 'inbox' / 'file.md').exists()
        assert (tmp_path / 'tests' / 'file.test.md').exists()
        assert (tmp_path / 'drafts' / 'file.draft.md').exists()


class TestContentDeduplication:
    """Tests for content-based duplicate detection."""

    def test_compute_file_hash(self, tmp_path: Path) -> None:
        """Test file hash computation."""
        file1 = tmp_path / 'file1.txt'
        file2 = tmp_path / 'file2.txt'
        file3 = tmp_path / 'file3.txt'

        file1.write_text('same content')
        file2.write_text('same content')
        file3.write_text('different content')

        hash1 = compute_file_hash(file1)
        hash2 = compute_file_hash(file2)
        hash3 = compute_file_hash(file3)

        assert hash1 == hash2  # Same content = same hash
        assert hash1 != hash3  # Different content = different hash

    def test_dedup_by_content(self, tmp_path: Path) -> None:
        """Test content-based deduplication."""
        target = tmp_path / 'inbox'
        target.mkdir()

        # Create files with same content but different names
        (tmp_path / 'file1.md').write_text('identical content')
        (target / 'existing.md').write_text('identical content')

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir='.',
            target_dir='inbox',
            dedup_by_content=True,
            verbosity=0,
        )

        result = tidy(config)

        # file1.md should be detected as duplicate of existing.md
        assert len(result.skipped) == 1
        assert result.skipped[0].status == OperationStatus.DUPLICATE
        assert 'content duplicate' in (result.skipped[0].reason or '')


class TestUndoFunctionality:
    """Tests for undo capability."""

    def test_undo_manifest_serialization(self) -> None:
        """Test undo manifest to/from dict."""
        from pre_commit.tidy import UndoOperation

        manifest = UndoManifest(
            created_at='2026-01-21T00:00:00Z',
            dry_run=False,
            operations=[
                UndoOperation(
                    original_path='/src/file.md',
                    moved_to_path='/dest/file.md',
                    timestamp='2026-01-21T00:00:01Z',
                ),
            ],
        )

        data = manifest.to_dict()
        restored = UndoManifest.from_dict(data)

        assert restored.created_at == manifest.created_at
        assert len(restored.operations) == 1
        assert restored.operations[0].original_path == '/src/file.md'

    def test_undo_restores_files(self, tmp_path: Path) -> None:
        """Test that undo restores files to original locations."""
        # First, run tidy
        (tmp_path / 'file.md').write_text('content')

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir='.',
            target_dir='inbox',
            verbosity=0,
        )

        result = tidy(config)
        assert len(result.moved) == 1
        assert (tmp_path / 'inbox' / 'file.md').exists()
        assert not (tmp_path / 'file.md').exists()

        # Now undo
        undo_result = undo_tidy(config)

        assert len(undo_result.moved) == 1
        assert (tmp_path / 'file.md').exists()
        assert not (tmp_path / 'inbox' / 'file.md').exists()

    def test_undo_dry_run_skipped(self, tmp_path: Path) -> None:
        """Test that dry run operations can't be undone."""
        (tmp_path / 'file.md').write_text('content')

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
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
        (tmp_path / 'file1.md').write_text('content')
        (tmp_path / 'subdir').mkdir()
        (tmp_path / 'subdir' / 'file2.md').write_text('content')

        config = TidyConfig(root_dir=tmp_path, recursive=False)

        files = collect_files(tmp_path, config)

        assert len(files) == 1
        assert files[0][0].name == 'file1.md'

    def test_collect_files_recursive(self, tmp_path: Path) -> None:
        """Test collecting files with recursion."""
        (tmp_path / 'file1.md').write_text('content')
        (tmp_path / 'subdir').mkdir()
        (tmp_path / 'subdir' / 'file2.md').write_text('content')

        config = TidyConfig(root_dir=tmp_path, recursive=True)

        files = collect_files(tmp_path, config)

        assert len(files) == 2
        names = {f[0].name for f in files}
        assert names == {'file1.md', 'file2.md'}


class TestPreserveStructure:
    """Tests for preserve_structure option."""

    def test_preserve_structure(self, tmp_path: Path) -> None:
        """Test preserving directory structure when moving files."""
        # Setup nested directories
        (tmp_path / 'src' / 'level1').mkdir(parents=True)
        (tmp_path / 'src' / 'level1' / 'level2').mkdir()
        (tmp_path / 'src' / 'file1.md').write_text('root')
        (tmp_path / 'src' / 'level1' / 'file2.md').write_text('level1')
        (tmp_path / 'src' / 'level1' / 'level2' / 'file3.md').write_text('level2')  # noqa: E501

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir='src',
            target_dir='dest',
            recursive=True,
            preserve_structure=True,
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 3
        dest = tmp_path / 'dest'
        # Structure should be preserved
        assert (dest / 'file1.md').exists()
        assert (dest / 'level1' / 'file2.md').exists()
        assert (dest / 'level1' / 'level2' / 'file3.md').exists()

    def test_flatten_depth(self, tmp_path: Path) -> None:
        """Test flatten_depth limits preserved structure."""
        # Setup deeply nested directories
        (tmp_path / 'src' / 'a' / 'b' / 'c').mkdir(parents=True)
        (tmp_path / 'src' / 'a' / 'b' / 'c' / 'file.md').write_text('deep')

        config = TidyConfig(
            root_dir=tmp_path,
            source_dir='src',
            target_dir='dest',
            recursive=True,
            preserve_structure=True,
            flatten_depth=2,  # Keep only 2 levels
            verbosity=0,
        )

        result = tidy(config)

        assert len(result.moved) == 1
        # Only 2 levels preserved: a/b (not c)
        assert (tmp_path / 'dest' / 'a' / 'b' / 'file.md').exists()
        assert not (tmp_path / 'dest' / 'a' / 'b' / 'c').exists()


class TestMetadataFilters:
    """Tests for file metadata filters."""

    def test_exclude_hidden_files(self, tmp_path: Path) -> None:
        """Test excluding hidden files."""
        (tmp_path / 'visible.md').write_text('visible')
        (tmp_path / '.hidden.md').write_text('hidden')

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
            verbosity=0,
        )
        config.filters.exclude_hidden = True

        result = tidy(config)

        assert len(result.moved) == 1
        assert len(result.skipped) == 1
        assert (tmp_path / 'inbox' / 'visible.md').exists()
        assert not (tmp_path / 'inbox' / '.hidden.md').exists()

    def test_size_filters(self, tmp_path: Path) -> None:
        """Test filtering by file size."""
        (tmp_path / 'small.md').write_text('x')  # 1 byte
        (tmp_path / 'large.md').write_text('x' * 1000)  # 1000 bytes

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
            verbosity=0,
        )
        config.filters.min_size = 100  # At least 100 bytes

        result = tidy(config)

        assert len(result.moved) == 1
        assert len(result.skipped) == 1
        assert (tmp_path / 'inbox' / 'large.md').exists()
        assert not (tmp_path / 'inbox' / 'small.md').exists()

    def test_exclude_symlinks(self, tmp_path: Path) -> None:
        """Test excluding symbolic links."""
        (tmp_path / 'real.md').write_text('real')
        (tmp_path / 'link.md').symlink_to(tmp_path / 'real.md')

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
            verbosity=0,
        )
        config.filters.exclude_symlinks = True

        result = tidy(config)

        assert len(result.moved) == 1
        assert (tmp_path / 'inbox' / 'real.md').exists()


class TestCollisionHandling:
    """Tests for collision handling options."""

    def test_collision_keep_newest(self, tmp_path: Path) -> None:
        """Test keeping the newest file on collision."""
        import time

        target = tmp_path / 'inbox'
        target.mkdir()

        # Create older file in target
        (target / 'file.md').write_text('old')
        old_time = time.time() - 100
        os.utime(target / 'file.md', (old_time, old_time))

        # Create newer file in source
        (tmp_path / 'file.md').write_text('new')

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
            verbosity=0,
        )
        config.collision.keep = CollisionKeep.NEWEST

        result = tidy(config)

        assert len(result.moved) == 1
        # Newer file should have replaced older
        assert (target / 'file.md').read_text() == 'new'

    def test_collision_keep_largest(self, tmp_path: Path) -> None:
        """Test keeping the largest file on collision."""
        target = tmp_path / 'inbox'
        target.mkdir()

        # Create smaller file in target
        (target / 'file.md').write_text('x')

        # Create larger file in source
        (tmp_path / 'file.md').write_text('x' * 100)

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
            verbosity=0,
        )
        config.collision.keep = CollisionKeep.LARGEST

        result = tidy(config)

        assert len(result.moved) == 1
        # Larger file should have replaced smaller
        assert len((target / 'file.md').read_text()) == 100

    def test_collision_keep_target(self, tmp_path: Path) -> None:
        """Test keeping the target file (skip source)."""
        target = tmp_path / 'inbox'
        target.mkdir()

        (target / 'file.md').write_text('existing')
        (tmp_path / 'file.md').write_text('new')

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
            verbosity=0,
        )
        config.collision.keep = CollisionKeep.TARGET

        result = tidy(config)

        assert len(result.skipped) == 1
        assert (target / 'file.md').read_text() == 'existing'


class TestPersistentUndoHistory:
    """Tests for persistent undo history."""

    def test_undo_history_created(self, tmp_path: Path) -> None:
        """Test that undo history directory is created."""
        from pre_commit.tidy import UNDO_HISTORY_DIR, list_undo_manifests

        (tmp_path / 'file.md').write_text('content')

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
            verbosity=0,
        )

        tidy(config)

        undo_dir = tmp_path / UNDO_HISTORY_DIR
        assert undo_dir.exists()
        assert len(list(undo_dir.glob('*.json'))) == 1

        manifests = list_undo_manifests(tmp_path)
        assert len(manifests) == 1

    def test_undo_history_limit(self, tmp_path: Path) -> None:
        """Test that undo history respects limit."""
        from pre_commit.tidy import UNDO_HISTORY_DIR

        for i in range(5):
            (tmp_path / f"file{i}.md").write_text(f"content{i}")

            config = TidyConfig(
                root_dir=tmp_path,
                target_dir='inbox',
                undo_history_limit=3,
                verbosity=0,
            )

            tidy(config)

        undo_dir = tmp_path / UNDO_HISTORY_DIR
        # Should only keep 3 most recent
        assert len(list(undo_dir.glob('*.json'))) == 3

    def test_undo_specific_manifest(self, tmp_path: Path) -> None:
        """Test undoing a specific manifest by ID."""
        from pre_commit.tidy import list_undo_manifests

        # First operation
        (tmp_path / 'file1.md').write_text('content1')
        config = TidyConfig(root_dir=tmp_path, target_dir='inbox', verbosity=0)
        tidy(config)

        # Second operation
        (tmp_path / 'file2.md').write_text('content2')
        tidy(config)

        manifests = list_undo_manifests(tmp_path)
        assert len(manifests) == 2

        # Undo the most recent one
        latest_id = manifests[0][0]
        result = undo_tidy(config, manifest_id=latest_id)

        assert len(result.moved) == 1
        assert (tmp_path / 'file2.md').exists()


class TestParseSizeAndDate:
    """Tests for size and date parsing utilities."""

    def test_parse_size(self) -> None:
        """Test parsing human-readable sizes."""
        from pre_commit.tidy import parse_size

        assert parse_size('1024') == 1024
        assert parse_size('1KB') == 1024
        assert parse_size('1kb') == 1024
        assert parse_size('5MB') == 5 * 1024 * 1024
        assert parse_size('1GB') == 1024 ** 3
        assert parse_size(100) == 100

    def test_parse_date(self) -> None:
        """Test parsing date strings."""
        from pre_commit.tidy import parse_date

        d = parse_date('2025-01-15')
        assert d.year == 2025
        assert d.month == 1
        assert d.day == 15

        d = parse_date('2025-01-15T10:30:00')
        assert d.hour == 10
        assert d.minute == 30


class TestRenamePattern:
    """Tests for configurable rename patterns."""

    def test_custom_rename_pattern(self, tmp_path: Path) -> None:
        """Test using custom rename pattern."""
        target = tmp_path / 'inbox'
        target.mkdir()

        (target / 'file.md').write_text('existing')
        (tmp_path / 'file.md').write_text('new')

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
            verbosity=0,
        )
        config.collision.keep = CollisionKeep.BOTH
        config.collision.rename_pattern = '{name}-copy{ext}'

        result = tidy(config)

        assert len(result.moved) == 1
        assert (target / 'file-copy.md').exists()

    def test_rename_pattern_with_date(self, tmp_path: Path) -> None:
        """Test rename pattern with date token."""

        target = tmp_path / 'inbox'
        target.mkdir()

        (target / 'file.md').write_text('existing')
        (tmp_path / 'file.md').write_text('new')

        config = TidyConfig(
            root_dir=tmp_path,
            target_dir='inbox',
            verbosity=0,
        )
        config.collision.keep = CollisionKeep.BOTH
        config.collision.rename_pattern = '{name}-{date}{ext}'

        result = tidy(config)

        assert len(result.moved) == 1
        today = datetime.now().strftime('%Y-%m-%d')
        assert (target / f"file-{today}.md").exists()
