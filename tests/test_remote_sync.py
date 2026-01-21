"""Tests for the remote_sync module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from pre_commit.remote_sync import (
    BranchMode,
    ConfigDict,
    FilesystemTarget,
    ForcePushPolicy,
    HealthCheckResult,
    OfflineQueue,
    PushResult,
    PushStatus,
    QueuedPush,
    RemoteConfig,
    RemoteStatus,
    RsyncTarget,
    SyncConfig,
    SyncState,
    SyncStatusResult,
    SyncTargetResult,
    SyncTargetType,
    VpnConfig,
    VpnResult,
    add_to_queue,
    branch_matches_pattern,
    check_filesystem_target_health,
    check_remote_health,
    check_rsync_target_health,
    clear_queue,
    connect_vpn,
    disconnect_vpn,
    discover_remotes,
    get_sync_state,
    get_target_branch,
    get_vpn_for_remote,
    is_force_push_required,
    is_git_repo,
    is_vpn_connected,
    load_config_file,
    load_env_config,
    load_queue,
    main,
    merge_configs,
    remove_from_queue,
    save_queue,
    switch_branch_at_path,
    sync_to_filesystem,
    sync_to_rsync,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


class TestRemoteConfig:
    """Tests for RemoteConfig dataclass."""

    def test_from_dict_defaults(self) -> None:
        """Test creating RemoteConfig with defaults."""
        config = RemoteConfig.from_dict("origin", {})
        assert config.name == "origin"
        assert config.priority == 1
        assert config.branches == ["*"]
        assert config.force_push == ForcePushPolicy.BLOCK
        assert config.retry == 3
        assert config.timeout == 60  # Increased default for large pushes
        assert config.group == "default"

    def test_from_dict_full_config(self) -> None:
        """Test creating RemoteConfig with all options."""
        data = {
            "priority": 2,
            "branches": ["main", "develop"],
            "force_push": "warn",
            "retry": 5,
            "timeout": 60,
            "group": "mirrors",
            "url": "https://github.com/user/repo.git",
        }
        config = RemoteConfig.from_dict("mirror", data)
        assert config.name == "mirror"
        assert config.priority == 2
        assert config.branches == ["main", "develop"]
        assert config.force_push == ForcePushPolicy.WARN
        assert config.retry == 5
        assert config.timeout == 60
        assert config.group == "mirrors"
        assert config.url == "https://github.com/user/repo.git"

    def test_from_dict_invalid_force_push(self) -> None:
        """Test that invalid force_push values default to BLOCK."""
        config = RemoteConfig.from_dict("origin", {"force_push": "invalid"})
        assert config.force_push == ForcePushPolicy.BLOCK


class TestSyncConfig:
    """Tests for SyncConfig dataclass."""

    def test_from_dict_empty(self) -> None:
        """Test creating SyncConfig from empty dict."""
        config = SyncConfig.from_dict({})
        assert config.remotes == {}
        assert config.parallel is True
        assert config.max_workers == 4
        assert config.offline_queue is True

    def test_from_dict_with_remotes(self) -> None:
        """Test creating SyncConfig with remotes."""
        data: ConfigDict = {
            "remotes": {
                "origin": {"priority": 1},
                "mirror": {"priority": 2, "branches": ["main"]},
            },
            "parallel": False,
            "max_workers": 2,
        }
        config = SyncConfig.from_dict(data)
        assert len(config.remotes) == 2
        assert "origin" in config.remotes
        assert "mirror" in config.remotes
        assert config.remotes["mirror"].branches == ["main"]
        assert config.parallel is False
        assert config.max_workers == 2


class TestBranchMatchesPattern:
    """Tests for branch_matches_pattern function."""

    def test_wildcard_matches_all(self) -> None:
        """Test that * matches all branches."""
        assert branch_matches_pattern("main", ["*"]) is True
        assert branch_matches_pattern("feature/foo", ["*"]) is True
        assert branch_matches_pattern("develop", ["*"]) is True

    def test_exact_match(self) -> None:
        """Test exact branch name matching."""
        assert branch_matches_pattern("main", ["main"]) is True
        assert branch_matches_pattern("main", ["develop"]) is False

    def test_glob_pattern(self) -> None:
        """Test glob pattern matching."""
        assert branch_matches_pattern("feature/foo", ["feature/*"]) is True
        assert branch_matches_pattern("feature/bar", ["feature/*"]) is True
        assert branch_matches_pattern("bugfix/foo", ["feature/*"]) is False

    def test_multiple_patterns(self) -> None:
        """Test matching against multiple patterns."""
        patterns = ["main", "develop", "release/*"]
        assert branch_matches_pattern("main", patterns) is True
        assert branch_matches_pattern("develop", patterns) is True
        assert branch_matches_pattern("release/1.0", patterns) is True
        assert branch_matches_pattern("feature/foo", patterns) is False


class TestQueuedPush:
    """Tests for QueuedPush dataclass."""

    def test_to_dict(self) -> None:
        """Test serializing QueuedPush to dict."""
        push = QueuedPush(
            remote="origin",
            branch="main",
            commit_sha="abc123",
            queued_at="2024-01-01T00:00:00Z",
            retries=2,
            last_error="Connection timeout",
        )
        data = push.to_dict()
        assert data["remote"] == "origin"
        assert data["branch"] == "main"
        assert data["commit_sha"] == "abc123"
        assert data["retries"] == 2

    def test_from_dict(self) -> None:
        """Test deserializing QueuedPush from dict."""
        data = {
            "remote": "mirror",
            "branch": "develop",
            "commit_sha": "def456",
            "queued_at": "2024-01-02T00:00:00Z",
        }
        push = QueuedPush.from_dict(data)
        assert push.remote == "mirror"
        assert push.branch == "develop"
        assert push.commit_sha == "def456"
        assert push.retries == 0


class TestOfflineQueue:
    """Tests for OfflineQueue dataclass."""

    def test_round_trip(self) -> None:
        """Test serializing and deserializing OfflineQueue."""
        queue = OfflineQueue(
            items=[
                QueuedPush(
                    remote="origin",
                    branch="main",
                    commit_sha="abc123",
                    queued_at="2024-01-01T00:00:00Z",
                ),
            ],
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        data = queue.to_dict()
        restored = OfflineQueue.from_dict(data)
        assert len(restored.items) == 1
        assert restored.items[0].remote == "origin"
        assert restored.created_at == "2024-01-01T00:00:00Z"


class TestLoadConfigFile:
    """Tests for load_config_file function."""

    def test_load_existing_config(self, tmp_path: Path) -> None:
        """Test loading an existing config file."""
        config_file = tmp_path / ".remotesyncrc.json"
        config_data = {
            "remotes": {
                "origin": {"priority": 1},
            },
            "parallel": False,
        }
        config_file.write_text(json.dumps(config_data))

        config = load_config_file(config_file)
        assert "remotes" in config
        assert config["parallel"] is False

    def test_load_nonexistent_config(self, tmp_path: Path) -> None:
        """Test loading a nonexistent config file returns empty dict."""
        config_file = tmp_path / "nonexistent.json"
        config = load_config_file(config_file)
        assert config == {}

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        """Test loading invalid JSON returns empty dict."""
        config_file = tmp_path / ".remotesyncrc.json"
        config_file.write_text("invalid json {")

        config = load_config_file(config_file)
        assert config == {}


class TestLoadEnvConfig:
    """Tests for load_env_config function."""

    def test_parallel_false(self, monkeypatch: MonkeyPatch) -> None:
        """Test REMOTE_SYNC_PARALLEL=false."""
        monkeypatch.setenv("REMOTE_SYNC_PARALLEL", "false")
        config = load_env_config()
        assert config.get("parallel") is False

    def test_parallel_true(self, monkeypatch: MonkeyPatch) -> None:
        """Test REMOTE_SYNC_PARALLEL=true."""
        monkeypatch.setenv("REMOTE_SYNC_PARALLEL", "true")
        config = load_env_config()
        assert config.get("parallel") is True

    def test_offline_queue(self, monkeypatch: MonkeyPatch) -> None:
        """Test REMOTE_SYNC_OFFLINE_QUEUE setting."""
        monkeypatch.setenv("REMOTE_SYNC_OFFLINE_QUEUE", "true")
        config = load_env_config()
        assert config.get("offline_queue") is True

    def test_max_workers(self, monkeypatch: MonkeyPatch) -> None:
        """Test REMOTE_SYNC_MAX_WORKERS setting."""
        monkeypatch.setenv("REMOTE_SYNC_MAX_WORKERS", "8")
        config = load_env_config()
        assert config.get("max_workers") == 8

    def test_invalid_max_workers(self, monkeypatch: MonkeyPatch) -> None:
        """Test invalid REMOTE_SYNC_MAX_WORKERS is ignored."""
        monkeypatch.setenv("REMOTE_SYNC_MAX_WORKERS", "not_a_number")
        config = load_env_config()
        assert "max_workers" not in config


class TestMergeConfigs:
    """Tests for merge_configs function."""

    def test_simple_merge(self) -> None:
        """Test merging simple configs."""
        config1: ConfigDict = {"parallel": True}
        config2: ConfigDict = {"max_workers": 8}
        merged = merge_configs(config1, config2)
        assert merged["parallel"] is True
        assert merged["max_workers"] == 8

    def test_override(self) -> None:
        """Test later configs override earlier ones."""
        config1: ConfigDict = {"parallel": True}
        config2: ConfigDict = {"parallel": False}
        merged = merge_configs(config1, config2)
        assert merged["parallel"] is False

    def test_deep_merge_remotes(self) -> None:
        """Test remotes are deep merged."""
        config1: ConfigDict = {
            "remotes": {"origin": {"priority": 1}},
        }
        config2: ConfigDict = {
            "remotes": {"mirror": {"priority": 2}},
        }
        merged = merge_configs(config1, config2)
        assert "origin" in merged["remotes"]
        assert "mirror" in merged["remotes"]


class TestQueueOperations:
    """Tests for queue save/load/modify operations."""

    def test_save_and_load_queue(self, tmp_path: Path) -> None:
        """Test saving and loading queue."""
        queue_path = tmp_path / ".remote-sync-queue.json"
        queue = OfflineQueue(
            items=[
                QueuedPush(
                    remote="origin",
                    branch="main",
                    commit_sha="abc123",
                    queued_at="2024-01-01T00:00:00Z",
                ),
            ],
        )

        save_queue(queue, queue_path)
        loaded = load_queue(queue_path)

        assert len(loaded.items) == 1
        assert loaded.items[0].remote == "origin"

    def test_add_to_queue(self, tmp_path: Path) -> None:
        """Test adding items to queue."""
        queue_path = tmp_path / ".remote-sync-queue.json"

        add_to_queue("origin", "main", "abc123", "Error", queue_path)
        add_to_queue("mirror", "develop", "def456", "Timeout", queue_path)

        queue = load_queue(queue_path)
        assert len(queue.items) == 2

    def test_add_to_queue_updates_existing(self, tmp_path: Path) -> None:
        """Test adding same remote/branch updates existing entry."""
        queue_path = tmp_path / ".remote-sync-queue.json"

        add_to_queue("origin", "main", "abc123", "Error 1", queue_path)
        add_to_queue("origin", "main", "def456", "Error 2", queue_path)

        queue = load_queue(queue_path)
        assert len(queue.items) == 1
        assert queue.items[0].commit_sha == "def456"
        assert queue.items[0].retries == 1

    def test_remove_from_queue(self, tmp_path: Path) -> None:
        """Test removing items from queue."""
        queue_path = tmp_path / ".remote-sync-queue.json"

        add_to_queue("origin", "main", "abc123", "", queue_path)
        add_to_queue("mirror", "develop", "def456", "", queue_path)
        remove_from_queue("origin", "main", queue_path)

        queue = load_queue(queue_path)
        assert len(queue.items) == 1
        assert queue.items[0].remote == "mirror"

    def test_clear_queue(self, tmp_path: Path) -> None:
        """Test clearing the queue."""
        queue_path = tmp_path / ".remote-sync-queue.json"

        add_to_queue("origin", "main", "abc123", "", queue_path)
        assert queue_path.exists()

        result = clear_queue(queue_path)
        assert result is True
        assert not queue_path.exists()


class TestGitOperations:
    """Tests for git-related operations using mocks."""

    @patch("pre_commit.remote_sync.run_git_command")
    def test_check_remote_health_reachable(self, mock_run: MagicMock) -> None:
        """Test health check for reachable remote."""
        mock_run.return_value = subprocess.CompletedProcess(
            ["git", "ls-remote"], 0, stdout="refs/heads/main\n", stderr=""
        )

        result = check_remote_health("origin", timeout=5)

        assert result.status == RemoteStatus.REACHABLE
        assert result.remote == "origin"

    @patch("pre_commit.remote_sync.run_git_command")
    def test_check_remote_health_unreachable(self, mock_run: MagicMock) -> None:
        """Test health check for unreachable remote."""
        mock_run.return_value = subprocess.CompletedProcess(
            ["git", "ls-remote"], 128, stdout="", stderr="Connection refused"
        )

        result = check_remote_health("origin", timeout=5)

        assert result.status == RemoteStatus.UNREACHABLE
        assert "Connection refused" in result.error

    @patch("pre_commit.remote_sync.run_git_command")
    def test_get_sync_state_in_sync(self, mock_run: MagicMock) -> None:
        """Test sync state when in sync."""
        mock_run.return_value = subprocess.CompletedProcess(
            ["git", "rev-list"], 0, stdout="0\t0", stderr=""
        )

        state, ahead, behind = get_sync_state("origin", "main")

        assert state == SyncState.IN_SYNC
        assert ahead == 0
        assert behind == 0

    @patch("pre_commit.remote_sync.run_git_command")
    def test_get_sync_state_ahead(self, mock_run: MagicMock) -> None:
        """Test sync state when ahead."""
        mock_run.return_value = subprocess.CompletedProcess(
            ["git", "rev-list"], 0, stdout="3\t0", stderr=""
        )

        state, ahead, behind = get_sync_state("origin", "main")

        assert state == SyncState.AHEAD
        assert ahead == 3
        assert behind == 0

    @patch("pre_commit.remote_sync.run_git_command")
    def test_get_sync_state_behind(self, mock_run: MagicMock) -> None:
        """Test sync state when behind."""
        mock_run.return_value = subprocess.CompletedProcess(
            ["git", "rev-list"], 0, stdout="0\t2", stderr=""
        )

        state, ahead, behind = get_sync_state("origin", "main")

        assert state == SyncState.BEHIND
        assert ahead == 0
        assert behind == 2

    @patch("pre_commit.remote_sync.run_git_command")
    def test_get_sync_state_diverged(self, mock_run: MagicMock) -> None:
        """Test sync state when diverged."""
        mock_run.return_value = subprocess.CompletedProcess(
            ["git", "rev-list"], 0, stdout="2\t3", stderr=""
        )

        state, ahead, behind = get_sync_state("origin", "main")

        assert state == SyncState.DIVERGED
        assert ahead == 2
        assert behind == 3

    @patch("pre_commit.remote_sync.run_git_command")
    def test_is_force_push_required_fast_forward(self, mock_run: MagicMock) -> None:
        """Test force push detection for fast-forward."""
        # Mock responses for rev-parse and merge-base
        def side_effect(args, **kwargs):
            if args[0] == "rev-parse":
                if "origin" in args[1]:
                    return subprocess.CompletedProcess(args, 0, "abc123", "")
                return subprocess.CompletedProcess(args, 0, "def456", "")
            if args[0] == "merge-base":
                return subprocess.CompletedProcess(args, 0, "abc123", "")
            return subprocess.CompletedProcess(args, 1, "", "error")

        mock_run.side_effect = side_effect

        # When merge-base equals remote commit, it's fast-forward
        result = is_force_push_required("origin", "main")
        assert result is False


class TestDiscoverRemotes:
    """Tests for discover_remotes function."""

    @patch("pre_commit.remote_sync.get_configured_remotes")
    @patch("pre_commit.remote_sync.get_remote_url")
    def test_discover_remotes(
        self, mock_url: MagicMock, mock_remotes: MagicMock
    ) -> None:
        """Test auto-discovery of remotes."""
        mock_remotes.return_value = ["origin", "upstream"]
        mock_url.side_effect = lambda r: f"https://github.com/{r}/repo.git"

        config = SyncConfig()
        config = discover_remotes(config)

        assert "origin" in config.remotes
        assert "upstream" in config.remotes
        # Origin should have priority 1
        assert config.remotes["origin"].priority == 1

    @patch("pre_commit.remote_sync.get_configured_remotes")
    def test_discover_remotes_skips_if_configured(
        self, mock_remotes: MagicMock
    ) -> None:
        """Test that discovery skips if remotes already configured."""
        mock_remotes.return_value = ["origin"]

        config = SyncConfig(remotes={"custom": RemoteConfig(name="custom")})
        config = discover_remotes(config)

        # Should not add origin since remotes are already configured
        assert "origin" not in config.remotes
        assert "custom" in config.remotes


class TestPushResult:
    """Tests for PushResult and SyncResult."""

    def test_sync_result_counts(self) -> None:
        """Test SyncResult success/failure counting."""
        from pre_commit.remote_sync import SyncResult

        result = SyncResult(
            push_results=[
                PushResult("origin", "main", PushStatus.SUCCESS),
                PushResult("mirror", "main", PushStatus.SUCCESS),
                PushResult("backup", "main", PushStatus.FAILED, "timeout"),
            ]
        )

        assert result.success_count == 2
        assert result.failed_count == 1
        assert result.all_succeeded is False

    def test_sync_result_all_succeeded(self) -> None:
        """Test SyncResult when all succeed."""
        from pre_commit.remote_sync import SyncResult

        result = SyncResult(
            push_results=[
                PushResult("origin", "main", PushStatus.SUCCESS),
                PushResult("mirror", "main", PushStatus.SUCCESS),
            ]
        )

        assert result.all_succeeded is True


class TestCLI:
    """Tests for CLI argument parsing and main function."""

    def test_version_flag(self, capsys) -> None:
        """Test --version flag."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "1.0.0" in captured.out

    @patch("pre_commit.remote_sync.get_configured_remotes")
    @patch("pre_commit.remote_sync.get_remote_url")
    @patch("pre_commit.remote_sync.check_remote_health")
    def test_health_check_command(
        self,
        mock_health: MagicMock,
        mock_url: MagicMock,
        mock_remotes: MagicMock,
    ) -> None:
        """Test --health-check command."""
        mock_remotes.return_value = ["origin"]
        mock_url.return_value = "https://github.com/user/repo.git"
        mock_health.return_value = HealthCheckResult(
            remote="origin",
            status=RemoteStatus.REACHABLE,
            url="https://github.com/user/repo.git",
            latency_ms=50.0,
        )

        exit_code = main(["--health-check", "--quiet"])
        assert exit_code == 0

    @patch("pre_commit.remote_sync.get_configured_remotes")
    def test_no_remotes_error(self, mock_remotes: MagicMock) -> None:
        """Test error when no remotes configured."""
        mock_remotes.return_value = []

        exit_code = main(["--push"])
        assert exit_code == 1

    @patch("pre_commit.remote_sync.get_configured_remotes")
    @patch("pre_commit.remote_sync.get_remote_url")
    @patch("pre_commit.remote_sync.clear_queue")
    def test_clear_queue_command(
        self,
        mock_clear: MagicMock,
        mock_url: MagicMock,
        mock_remotes: MagicMock,
    ) -> None:
        """Test --clear-queue command."""
        mock_remotes.return_value = ["origin"]
        mock_url.return_value = "https://github.com/user/repo.git"
        mock_clear.return_value = True

        exit_code = main(["--clear-queue", "--quiet"])
        assert exit_code == 0

    def test_dry_run_flag(self) -> None:
        """Test --dry-run flag is parsed correctly."""
        from pre_commit.remote_sync import create_argument_parser

        parser = create_argument_parser()
        args = parser.parse_args(["--push", "--dry-run"])

        assert args.push is True
        assert args.dry_run is True

    def test_remote_and_branch_flags(self) -> None:
        """Test --remote and --branch flags."""
        from pre_commit.remote_sync import create_argument_parser

        parser = create_argument_parser()
        args = parser.parse_args(["--push", "--remote", "origin,mirror", "--branch", "main"])

        assert args.remote == "origin,mirror"
        assert args.branch == "main"


class TestSyncStatusResult:
    """Tests for SyncStatusResult dataclass."""

    def test_sync_status_result_creation(self) -> None:
        """Test creating SyncStatusResult."""
        result = SyncStatusResult(
            remote="origin",
            branch="main",
            state=SyncState.AHEAD,
            local_commit="abc123",
            remote_commit="def456",
            ahead_count=3,
            behind_count=0,
        )

        assert result.remote == "origin"
        assert result.branch == "main"
        assert result.state == SyncState.AHEAD
        assert result.ahead_count == 3


class TestHealthCheckResult:
    """Tests for HealthCheckResult dataclass."""

    def test_health_check_result_reachable(self) -> None:
        """Test HealthCheckResult for reachable remote."""
        result = HealthCheckResult(
            remote="origin",
            status=RemoteStatus.REACHABLE,
            url="https://github.com/user/repo.git",
            latency_ms=42.5,
        )

        assert result.status == RemoteStatus.REACHABLE
        assert result.latency_ms == 42.5
        assert result.error == ""

    def test_health_check_result_unreachable(self) -> None:
        """Test HealthCheckResult for unreachable remote."""
        result = HealthCheckResult(
            remote="backup",
            status=RemoteStatus.UNREACHABLE,
            url="ssh://backup.server/repo.git",
            error="Connection refused",
        )

        assert result.status == RemoteStatus.UNREACHABLE
        assert "Connection refused" in result.error


class TestVpnConfig:
    """Tests for VpnConfig dataclass."""

    def test_from_dict_defaults(self) -> None:
        """Test creating VpnConfig with defaults."""
        config = VpnConfig.from_dict("corporate", {})
        assert config.name == "corporate"
        assert config.connect_cmd == ""
        assert config.disconnect_cmd == ""
        assert config.check_cmd == ""
        assert config.timeout == 60  # Increased default for VPN connections
        assert config.auto_connect is True

    def test_from_dict_full_config(self) -> None:
        """Test creating VpnConfig with all options."""
        data = {
            "connect_cmd": "networksetup -connectpppoeservice 'VPN'",
            "disconnect_cmd": "networksetup -disconnectpppoeservice 'VPN'",
            "check_cmd": "scutil --nc status 'VPN' | grep Connected",
            "timeout": 60,
            "auto_connect": False,
        }
        config = VpnConfig.from_dict("work-vpn", data)
        assert config.name == "work-vpn"
        assert "connectpppoeservice" in config.connect_cmd
        assert config.timeout == 60
        assert config.auto_connect is False


class TestVpnOperations:
    """Tests for VPN connect/disconnect operations."""

    @patch("pre_commit.remote_sync.run_shell_command")
    def test_is_vpn_connected_true(self, mock_run: MagicMock) -> None:
        """Test VPN connection check when connected."""
        mock_run.return_value = subprocess.CompletedProcess(
            "check_cmd", 0, stdout="Connected", stderr=""
        )

        vpn_config = VpnConfig(
            name="test-vpn",
            connect_cmd="connect",
            disconnect_cmd="disconnect",
            check_cmd="check",
        )
        assert is_vpn_connected(vpn_config) is True

    @patch("pre_commit.remote_sync.run_shell_command")
    def test_is_vpn_connected_false(self, mock_run: MagicMock) -> None:
        """Test VPN connection check when not connected."""
        mock_run.return_value = subprocess.CompletedProcess(
            "check_cmd", 1, stdout="", stderr="Not connected"
        )

        vpn_config = VpnConfig(
            name="test-vpn",
            connect_cmd="connect",
            disconnect_cmd="disconnect",
            check_cmd="check",
        )
        assert is_vpn_connected(vpn_config) is False

    def test_is_vpn_connected_no_check_cmd(self) -> None:
        """Test VPN connection check without check command."""
        vpn_config = VpnConfig(
            name="test-vpn",
            connect_cmd="connect",
            disconnect_cmd="disconnect",
            check_cmd="",  # No check command
        )
        assert is_vpn_connected(vpn_config) is False

    @patch("pre_commit.remote_sync.run_shell_command")
    @patch("pre_commit.remote_sync.is_vpn_connected")
    def test_connect_vpn_success(
        self, mock_is_connected: MagicMock, mock_run: MagicMock
    ) -> None:
        """Test successful VPN connection."""
        mock_is_connected.side_effect = [False, True]  # First not connected, then connected
        mock_run.return_value = subprocess.CompletedProcess(
            "connect", 0, stdout="Connected", stderr=""
        )

        vpn_config = VpnConfig(
            name="test-vpn",
            connect_cmd="vpn connect",
            disconnect_cmd="vpn disconnect",
            check_cmd="vpn status",
        )
        result = connect_vpn(vpn_config, dry_run=False)

        assert result.connected is True
        assert result.vpn_name == "test-vpn"

    def test_connect_vpn_dry_run(self) -> None:
        """Test VPN connection in dry run mode."""
        vpn_config = VpnConfig(
            name="test-vpn",
            connect_cmd="vpn connect",
            disconnect_cmd="vpn disconnect",
        )
        result = connect_vpn(vpn_config, dry_run=True)

        assert result.connected is True
        assert "DRY RUN" in result.message

    def test_connect_vpn_no_command(self) -> None:
        """Test VPN connection without connect command."""
        vpn_config = VpnConfig(
            name="test-vpn",
            connect_cmd="",  # No connect command
            disconnect_cmd="disconnect",
        )
        result = connect_vpn(vpn_config, dry_run=False)

        assert result.connected is False
        assert "No connect command" in result.message

    @patch("pre_commit.remote_sync.run_shell_command")
    def test_disconnect_vpn_success(self, mock_run: MagicMock) -> None:
        """Test successful VPN disconnection."""
        mock_run.return_value = subprocess.CompletedProcess(
            "disconnect", 0, stdout="Disconnected", stderr=""
        )

        vpn_config = VpnConfig(
            name="test-vpn",
            connect_cmd="vpn connect",
            disconnect_cmd="vpn disconnect",
        )
        result = disconnect_vpn(vpn_config, dry_run=False)

        assert result.connected is False
        assert "Disconnected" in result.message

    def test_disconnect_vpn_dry_run(self) -> None:
        """Test VPN disconnection in dry run mode."""
        vpn_config = VpnConfig(
            name="test-vpn",
            connect_cmd="connect",
            disconnect_cmd="vpn disconnect",
        )
        result = disconnect_vpn(vpn_config, dry_run=True)

        assert result.connected is False
        assert "DRY RUN" in result.message


class TestVpnConfigInRemote:
    """Tests for VPN configuration in remote settings."""

    def test_remote_config_with_vpn_name(self) -> None:
        """Test RemoteConfig with VPN name reference."""
        data = {
            "priority": 1,
            "branches": ["main"],
            "vpn": "corporate",
        }
        config = RemoteConfig.from_dict("internal", data)
        assert config.vpn == "corporate"

    def test_sync_config_with_vpn_configs(self) -> None:
        """Test SyncConfig parsing VPN configurations."""
        data: ConfigDict = {
            "remotes": {
                "internal": {"priority": 1, "vpn": "corporate"},
                "public": {"priority": 2},
            },
            "vpn": {
                "corporate": {
                    "connect_cmd": "vpn connect corp",
                    "disconnect_cmd": "vpn disconnect",
                    "check_cmd": "vpn status | grep Connected",
                },
            },
        }
        config = SyncConfig.from_dict(data)

        assert "corporate" in config.vpn_configs
        assert config.vpn_configs["corporate"].connect_cmd == "vpn connect corp"
        assert config.remotes["internal"].vpn == "corporate"
        assert config.remotes["public"].vpn is None

    def test_get_vpn_for_remote(self) -> None:
        """Test getting VPN config for a remote."""
        vpn_config = VpnConfig(
            name="corporate",
            connect_cmd="connect",
            disconnect_cmd="disconnect",
        )
        remote_config = RemoteConfig(name="internal", vpn="corporate")
        sync_config = SyncConfig(
            remotes={"internal": remote_config},
            vpn_configs={"corporate": vpn_config},
        )

        result = get_vpn_for_remote(remote_config, sync_config)
        assert result is not None
        assert result.name == "corporate"

    def test_get_vpn_for_remote_no_vpn(self) -> None:
        """Test getting VPN config when remote has no VPN."""
        remote_config = RemoteConfig(name="public")
        sync_config = SyncConfig(remotes={"public": remote_config})

        result = get_vpn_for_remote(remote_config, sync_config)
        assert result is None

    def test_inline_vpn_config(self) -> None:
        """Test inline VPN configuration in remote."""
        data: ConfigDict = {
            "remotes": {
                "private-server": {
                    "priority": 1,
                    "vpn": {
                        "connect_cmd": "ssh -f -N -D 1080 jumphost",
                        "disconnect_cmd": "pkill -f 'ssh.*jumphost'",
                    },
                },
            },
        }
        config = SyncConfig.from_dict(data)

        # Inline VPN should be stored with special name
        assert config.remotes["private-server"].vpn == "_inline_private-server"
        assert "_inline_private-server" in config.vpn_configs


class TestVpnResult:
    """Tests for VpnResult dataclass."""

    def test_vpn_result_connected(self) -> None:
        """Test VpnResult when connected."""
        result = VpnResult(
            vpn_name="corporate",
            connected=True,
            message="Connected successfully",
            duration=2.5,
        )
        assert result.connected is True
        assert result.duration == 2.5

    def test_vpn_result_failed(self) -> None:
        """Test VpnResult when connection failed."""
        result = VpnResult(
            vpn_name="corporate",
            connected=False,
            message="Connection refused",
        )
        assert result.connected is False
        assert "refused" in result.message


class TestPushResultWithVpn:
    """Tests for PushResult with VPN information."""

    def test_push_result_with_vpn(self) -> None:
        """Test PushResult includes VPN information."""
        result = PushResult(
            remote="internal",
            branch="main",
            status=PushStatus.SUCCESS,
            vpn_used="corporate",
        )
        assert result.vpn_used == "corporate"

    def test_push_result_without_vpn(self) -> None:
        """Test PushResult without VPN."""
        result = PushResult(
            remote="origin",
            branch="main",
            status=PushStatus.SUCCESS,
        )
        assert result.vpn_used is None


# =============================================================================
# Sync Target Tests
# =============================================================================


class TestFilesystemTarget:
    """Tests for FilesystemTarget configuration."""

    def test_from_dict_minimal(self) -> None:
        """Test FilesystemTarget with minimal config."""
        data = {"path": "/backup/repo"}
        target = FilesystemTarget.from_dict("backup", data)

        assert target.name == "backup"
        assert target.path == "/backup/repo"
        assert "__pycache__" in target.exclude  # Default excludes
        assert ".git" not in target.exclude  # .git NOT excluded by default (preserve repo)
        assert target.delete is False
        assert target.branch_mode == BranchMode.MATCH  # Match source branch by default

    def test_from_dict_full_config(self) -> None:
        """Test FilesystemTarget with full config."""
        data = {
            "path": "/backup/repo",
            "exclude": ["*.pyc", "__pycache__"],
            "delete": True,
        }
        target = FilesystemTarget.from_dict("backup", data)

        assert target.name == "backup"
        assert target.path == "/backup/repo"
        assert target.exclude == ["*.pyc", "__pycache__"]
        assert target.delete is True


class TestRsyncTarget:
    """Tests for RsyncTarget configuration."""

    def test_from_dict_minimal(self) -> None:
        """Test RsyncTarget with minimal config."""
        data = {"host": "backup.server.com", "path": "/var/backup/repo"}
        target = RsyncTarget.from_dict("server", data)

        assert target.name == "server"
        assert target.host == "backup.server.com"
        assert target.path == "/var/backup/repo"
        assert target.user == ""
        assert target.port == 22
        assert target.ssh_key == ""
        assert "__pycache__" in target.exclude  # Default excludes
        assert ".git" not in target.exclude  # .git NOT excluded by default (preserve repo)
        assert target.delete is False
        assert target.branch_mode == BranchMode.MATCH  # Match source branch by default

    def test_from_dict_full_config(self) -> None:
        """Test RsyncTarget with full config."""
        data = {
            "host": "backup.server.com",
            "path": "/var/backup/repo",
            "user": "deploy",
            "port": 2222,
            "ssh_key": "~/.ssh/backup_key",
            "exclude": [".git", "node_modules"],
            "delete": True,
        }
        target = RsyncTarget.from_dict("server", data)

        assert target.name == "server"
        assert target.host == "backup.server.com"
        assert target.path == "/var/backup/repo"
        assert target.user == "deploy"
        assert target.port == 2222
        assert target.ssh_key == "~/.ssh/backup_key"
        assert target.exclude == [".git", "node_modules"]
        assert target.delete is True


class TestSyncTargetResult:
    """Tests for SyncTargetResult dataclass."""

    def test_sync_target_result_success(self) -> None:
        """Test SyncTargetResult for successful sync."""
        result = SyncTargetResult(
            name="backup",
            target_type=SyncTargetType.FILESYSTEM,
            success=True,
            message="Sync completed",
            duration=5.5,
        )

        assert result.name == "backup"
        assert result.target_type == SyncTargetType.FILESYSTEM
        assert result.success is True
        assert result.message == "Sync completed"

    def test_sync_target_result_failure(self) -> None:
        """Test SyncTargetResult for failed sync."""
        result = SyncTargetResult(
            name="server",
            target_type=SyncTargetType.RSYNC,
            success=False,
            message="Connection refused",
            duration=2.0,
        )

        assert result.name == "server"
        assert result.target_type == SyncTargetType.RSYNC
        assert result.success is False
        assert result.message == "Connection refused"


class TestSyncConfigWithTargets:
    """Tests for SyncConfig with sync_targets."""

    def test_sync_config_with_filesystem_targets(self) -> None:
        """Test SyncConfig parses filesystem targets."""
        data = {
            "sync_targets": {
                "backup": {"path": "/backup/repo"},
                "nas": {"path": "/mnt/nas/projects/repo"},
            }
        }
        config = SyncConfig.from_dict(data)

        assert len(config.sync_targets) == 2
        assert "backup" in config.sync_targets
        assert config.sync_targets["backup"].target_type == SyncTargetType.FILESYSTEM

    def test_sync_config_with_rsync_targets(self) -> None:
        """Test SyncConfig parses rsync targets."""
        data = {
            "sync_targets": {
                "server": {
                    "host": "backup.server.com",
                    "path": "/var/backup",
                }
            }
        }
        config = SyncConfig.from_dict(data)

        assert len(config.sync_targets) == 1
        assert "server" in config.sync_targets
        assert config.sync_targets["server"].target_type == SyncTargetType.RSYNC
        assert config.sync_targets["server"].host == "backup.server.com"

    def test_sync_config_mixed_targets(self) -> None:
        """Test SyncConfig with mixed filesystem and rsync targets."""
        data = {
            "sync_targets": {
                "local": {"path": "/backup/repo"},
                "remote": {
                    "host": "server.com",
                    "path": "/var/backup",
                },
            }
        }
        config = SyncConfig.from_dict(data)

        assert len(config.sync_targets) == 2
        types = [t.target_type for t in config.sync_targets.values()]
        assert SyncTargetType.FILESYSTEM in types
        assert SyncTargetType.RSYNC in types


class TestSyncTargetHealthChecks:
    """Tests for sync target health checks."""

    def test_check_filesystem_target_health_exists(self, tmp_path: Path) -> None:
        """Test filesystem target health check for existing directory."""
        target = FilesystemTarget(
            name="backup",
            path=str(tmp_path),
            exclude=[],
            delete=False,
        )

        result = check_filesystem_target_health(target)
        assert result.status == RemoteStatus.REACHABLE

    def test_check_filesystem_target_health_not_exists(self) -> None:
        """Test filesystem target health check for non-existing directory."""
        target = FilesystemTarget(
            name="backup",
            path="/nonexistent/path/12345",
            exclude=[],
            delete=False,
        )

        result = check_filesystem_target_health(target)
        assert result.status == RemoteStatus.UNREACHABLE

    @patch("subprocess.run")
    def test_check_rsync_target_health_success(self, mock_run: MagicMock) -> None:
        """Test rsync target health check success."""
        mock_run.return_value = subprocess.CompletedProcess(
            "ssh", 0, stdout="ok", stderr=""
        )

        target = RsyncTarget(
            name="server",
            host="backup.server.com",
            path="/var/backup",
            user="deploy",
            port=22,
            ssh_key="",
            exclude=[],
            delete=False,
        )

        result = check_rsync_target_health(target)
        assert result.status == RemoteStatus.REACHABLE

    @patch("subprocess.run")
    def test_check_rsync_target_health_failure(self, mock_run: MagicMock) -> None:
        """Test rsync target health check failure."""
        mock_run.return_value = subprocess.CompletedProcess(
            "ssh", 255, stdout="", stderr="Connection refused"
        )

        target = RsyncTarget(
            name="server",
            host="unreachable.server.com",
            path="/var/backup",
            user="",
            port=22,
            ssh_key="",
            exclude=[],
            delete=False,
        )

        result = check_rsync_target_health(target)
        assert result.status == RemoteStatus.UNREACHABLE


class TestSyncToFilesystem:
    """Tests for sync_to_filesystem function."""

    @patch("subprocess.run")
    def test_sync_to_filesystem_success(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Test successful filesystem sync."""
        mock_run.return_value = subprocess.CompletedProcess(
            "rsync", 0, stdout="", stderr=""
        )

        # Create a source directory with test file
        source = tmp_path / "source"
        source.mkdir()
        (source / "test.txt").write_text("test")

        target = FilesystemTarget(
            name="backup",
            path=str(tmp_path / "dest"),
            exclude=["*.pyc"],
            delete=False,
        )

        result = sync_to_filesystem(target, source, dry_run=False)

        assert result.success is True
        assert result.name == "backup"
        # At least 2 calls: get_current_branch + rsync
        assert mock_run.call_count >= 1

    def test_sync_to_filesystem_dry_run(self, tmp_path: Path) -> None:
        """Test filesystem sync in dry run mode."""
        source = tmp_path / "source"
        source.mkdir()

        target = FilesystemTarget(
            name="backup",
            path=str(tmp_path / "dest"),
            exclude=[],
            delete=False,
        )

        result = sync_to_filesystem(target, source, dry_run=True)

        assert result.success is True
        assert "DRY RUN" in result.message


class TestSyncToRsync:
    """Tests for sync_to_rsync function."""

    @patch("subprocess.run")
    def test_sync_to_rsync_success(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Test successful rsync sync."""
        mock_run.return_value = subprocess.CompletedProcess(
            "rsync", 0, stdout="", stderr=""
        )

        source = tmp_path / "source"
        source.mkdir()

        target = RsyncTarget(
            name="server",
            host="backup.server.com",
            path="/var/backup",
            user="deploy",
            port=22,
            ssh_key="",
            exclude=[],
            delete=False,
        )

        result = sync_to_rsync(target, source, dry_run=False)

        assert result.success is True
        assert result.name == "server"
        # At least 2 calls: get_current_branch + rsync
        assert mock_run.call_count >= 1

    def test_sync_to_rsync_dry_run(self, tmp_path: Path) -> None:
        """Test rsync sync in dry run mode."""
        source = tmp_path / "source"
        source.mkdir()

        target = RsyncTarget(
            name="server",
            host="backup.server.com",
            path="/var/backup",
            user="",
            port=22,
            ssh_key="",
            exclude=[],
            delete=False,
        )

        result = sync_to_rsync(target, source, dry_run=True)

        assert result.success is True
        assert "DRY RUN" in result.message

    @patch("subprocess.run")
    def test_sync_to_rsync_with_ssh_key(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Test rsync sync with SSH key."""
        mock_run.return_value = subprocess.CompletedProcess(
            "rsync", 0, stdout="", stderr=""
        )

        source = tmp_path / "source"
        source.mkdir()

        target = RsyncTarget(
            name="server",
            host="backup.server.com",
            path="/var/backup",
            user="deploy",
            port=2222,
            ssh_key="~/.ssh/backup_key",
            exclude=[],
            delete=True,
        )

        result = sync_to_rsync(target, source, dry_run=False)

        assert result.success is True
        # Verify SSH options are included
        call_args = mock_run.call_args[0][0]
        assert "-e" in call_args


class TestSyncTargetsCLI:
    """Tests for CLI options related to sync targets."""

    def test_sync_targets_flag(self) -> None:
        """Test --sync-targets CLI flag."""
        from pre_commit.remote_sync import create_argument_parser

        parser = create_argument_parser()
        args = parser.parse_args(["--sync-targets"])

        assert args.sync_targets is True

    def test_sync_all_flag(self) -> None:
        """Test --sync-all CLI flag."""
        from pre_commit.remote_sync import create_argument_parser

        parser = create_argument_parser()
        args = parser.parse_args(["--sync-all"])

        assert args.sync_all is True

    def test_target_filter_flag(self) -> None:
        """Test --target CLI flag for filtering."""
        from pre_commit.remote_sync import create_argument_parser

        parser = create_argument_parser()
        args = parser.parse_args(["--sync-targets", "--target", "backup,nas"])

        assert args.target == "backup,nas"


class TestBranchMode:
    """Tests for BranchMode enum and branch switching."""

    def test_branch_mode_enum_values(self) -> None:
        """Test BranchMode enum values."""
        from pre_commit.remote_sync import BranchMode

        assert BranchMode.KEEP.value == "keep"
        assert BranchMode.MATCH.value == "match"
        assert BranchMode.SPECIFIC.value == "specific"

    def test_filesystem_target_with_branch_mode(self) -> None:
        """Test FilesystemTarget with branch_mode configuration."""
        from pre_commit.remote_sync import BranchMode

        data = {
            "path": "/backup/repo",
            "branch_mode": "match",
        }
        target = FilesystemTarget.from_dict("backup", data)

        assert target.branch_mode == BranchMode.MATCH

    def test_filesystem_target_with_specific_branch(self) -> None:
        """Test FilesystemTarget with specific branch."""
        from pre_commit.remote_sync import BranchMode

        data = {
            "path": "/backup/repo",
            "branch_mode": "specific",
            "branch": "main",
        }
        target = FilesystemTarget.from_dict("backup", data)

        assert target.branch_mode == BranchMode.SPECIFIC
        assert target.branch == "main"

    def test_rsync_target_with_branch_mode(self) -> None:
        """Test RsyncTarget with branch_mode configuration."""
        from pre_commit.remote_sync import BranchMode

        data = {
            "host": "server.com",
            "path": "/var/backup",
            "branch_mode": "match",
        }
        target = RsyncTarget.from_dict("server", data)

        assert target.branch_mode == BranchMode.MATCH

    def test_get_target_branch_keep(self) -> None:
        """Test get_target_branch with KEEP mode."""
        from pre_commit.remote_sync import BranchMode, get_target_branch

        target = FilesystemTarget(
            name="backup",
            path="/backup",
            branch_mode=BranchMode.KEEP,
        )

        result = get_target_branch(target, "develop")
        assert result is None  # Should not switch

    def test_get_target_branch_match(self) -> None:
        """Test get_target_branch with MATCH mode."""
        from pre_commit.remote_sync import BranchMode, get_target_branch

        target = FilesystemTarget(
            name="backup",
            path="/backup",
            branch_mode=BranchMode.MATCH,
        )

        result = get_target_branch(target, "develop")
        assert result == "develop"

    def test_get_target_branch_specific(self) -> None:
        """Test get_target_branch with SPECIFIC mode."""
        from pre_commit.remote_sync import BranchMode, get_target_branch

        target = FilesystemTarget(
            name="backup",
            path="/backup",
            branch_mode=BranchMode.SPECIFIC,
            branch="main",
        )

        result = get_target_branch(target, "develop")
        assert result == "main"  # Should use specific branch, not source

    def test_is_git_repo_true(self, tmp_path: Path) -> None:
        """Test is_git_repo returns True for git repo."""
        from pre_commit.remote_sync import is_git_repo

        # Create a fake .git directory
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        assert is_git_repo(tmp_path) is True

    def test_is_git_repo_false(self, tmp_path: Path) -> None:
        """Test is_git_repo returns False for non-git directory."""
        from pre_commit.remote_sync import is_git_repo

        assert is_git_repo(tmp_path) is False

    @patch("subprocess.run")
    def test_switch_branch_at_path_success(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Test successful branch switch at local path."""
        from pre_commit.remote_sync import switch_branch_at_path

        # Create fake .git directory
        (tmp_path / ".git").mkdir()

        mock_run.return_value = subprocess.CompletedProcess(
            "git checkout", 0, stdout="Switched to branch 'main'", stderr=""
        )

        success, message = switch_branch_at_path(tmp_path, "main")

        assert success is True
        assert "main" in message

    def test_switch_branch_at_path_not_git_repo(self, tmp_path: Path) -> None:
        """Test branch switch fails for non-git directory."""
        from pre_commit.remote_sync import switch_branch_at_path

        success, message = switch_branch_at_path(tmp_path, "main")

        assert success is False
        assert "Not a git repository" in message

    def test_switch_branch_at_path_dry_run(self, tmp_path: Path) -> None:
        """Test branch switch in dry run mode."""
        from pre_commit.remote_sync import switch_branch_at_path

        # Create fake .git directory
        (tmp_path / ".git").mkdir()

        success, message = switch_branch_at_path(tmp_path, "main", dry_run=True)

        assert success is True
        assert "DRY RUN" in message
