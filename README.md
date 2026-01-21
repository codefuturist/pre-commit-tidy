[![build status](https://github.com/pre-commit/pre-commit/actions/workflows/main.yml/badge.svg)](https://github.com/pre-commit/pre-commit/actions/workflows/main.yml)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/pre-commit/pre-commit/main.svg)](https://results.pre-commit.ci/latest/github/pre-commit/pre-commit/main)

## pre-commit

A framework library for managing and maintaining multi-language pre-commit hooks.

---

## Tidy - File Organization Tool

Automated file organization for repositories. Move files from source directories to target directories based on configurable rules.

### Installation

```bash
pip install pre-commit
# or install in development mode
pip install -e .
```

### Quick Start

```bash
# Preview changes (dry run)
tidy --dry-run

# Move all .md files to 00-inbox/
tidy

# Recursively scan directories
tidy --recursive

# Limit recursion depth
tidy --recursive --max-depth 3

# Use content-based duplicate detection
tidy --dedup-by-content

# Undo the last operation
tidy --undo
```

### Configuration

Create a `.tidyrc.json` file in your project root:

```json
{
    "source_dir": ".",
    "target_dir": "00-inbox",
    "extensions": [".md", ".txt"],
    "exclude_files": ["readme.md", "changelog.md"],
    "exclude_patterns": ["*.config.*"],
    "exclude_dirs": ["node_modules", ".git", "__pycache__"],
    "duplicate_strategy": "rename",
    "dedup_by_content": false,
    "recursive": false,
    "max_depth": null,
    "rules": [
        {"pattern": "*.test.md", "target": "tests/"},
        {"pattern": "*.draft.*", "target": "drafts/"},
        {"extensions": [".png", ".jpg"], "target": "assets/images/"},
        {"glob": "docs/**/*.md", "target": "documentation/"}
    ]
}
```

### Rule-based Routing

Tidy supports three rule formats for routing files to different targets:

| Format | Example | Description |
|--------|---------|-------------|
| **Pattern** | `{"pattern": "*.test.md", "target": "tests/"}` | Glob pattern matching on filename |
| **Extensions** | `{"extensions": [".png", ".jpg"], "target": "images/"}` | Match by file extension |
| **Glob** | `{"glob": "docs/**/*.md", "target": "docs-archive/"}` | Full path glob matching |

Rules are evaluated in order — first match wins.

### Duplicate Handling

| Strategy | Description |
|----------|-------------|
| `rename` | Add timestamp suffix to avoid conflicts (default) |
| `skip` | Skip files that already exist in target |
| `overwrite` | Overwrite existing files |

With `--dedup-by-content`, files are compared by SHA-256 hash to detect true duplicates regardless of filename.

### Pre-commit Hook Integration

Add tidy to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: tidy
        name: Tidy files
        entry: tidy
        language: python
        pass_filenames: false
        always_run: true
        args: ['--source', '.', '--target', '00-inbox', '--extensions', '.md']
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `TIDY_SOURCE_DIR` | Source directory |
| `TIDY_TARGET_DIR` | Target directory |
| `TIDY_EXTENSIONS` | Comma-separated extensions |
| `TIDY_EXCLUDE_FILES` | Comma-separated files to exclude |
| `TIDY_EXCLUDE_DIRS` | Comma-separated directories to exclude |
| `TIDY_DRY_RUN` | Set to `true` for dry run |
| `TIDY_VERBOSE` | Set to `true` for verbose output |
| `TIDY_RECURSIVE` | Set to `true` for recursive scanning |
| `TIDY_DEDUP_BY_CONTENT` | Set to `true` for content-based dedup |

### CLI Options

```
Usage: tidy [options]

Options:
  --config PATH           Path to configuration file
  --source DIR            Source directory (default: .)
  --target DIR            Target directory (default: 00-inbox)
  --extensions EXT        Comma-separated extensions (default: .md)
  --exclude-dirs DIRS     Comma-separated directories to exclude
  --recursive, -r         Recursively scan source directory
  --max-depth N           Maximum recursion depth (default: unlimited)
  --dedup-by-content      Detect duplicates by file content hash
  --undo                  Undo the last tidy operation
  --dry-run               Preview changes without moving files
  --verbose, -v           Show detailed output
  --quiet, -q             Suppress all output except errors
  --version               Show version number
  --help                  Show help message
```

### Undo Support

Tidy automatically creates a `.tidy-undo.json` manifest after each operation (overwrites previous). Use `tidy --undo` to restore files to their original locations.

---

## Remote Sync - Multi-Remote Synchronization

Keep multiple git remotes in sync automatically with parallel pushing, health checks, divergence detection, and offline queuing.

### Quick Start

```bash
# Show sync status dashboard
remote-sync --status

# Push current branch to all remotes
remote-sync --push

# Check connectivity to all remotes
remote-sync --health-check

# Push with dry-run preview
remote-sync --push --dry-run

# Process offline queue of failed pushes
remote-sync --process-queue
```

### Configuration

Create a `.remotesyncrc.json` file in your project root:

```json
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
        "internal-server": {
            "priority": 3,
            "branches": ["main"],
            "vpn": "corporate"
        }
    },
    "vpn": {
        "corporate": {
            "connect_cmd": "networksetup -connectpppoeservice 'Corporate VPN'",
            "disconnect_cmd": "networksetup -disconnectpppoeservice 'Corporate VPN'",
            "check_cmd": "scutil --nc status 'Corporate VPN' | grep Connected",
            "timeout": 30,
            "auto_connect": true
        }
    },
    "parallel": true,
    "max_workers": 4,
    "offline_queue": true,
    "health_check_timeout": 5,
    "retry_base_delay": 1.0,
    "retry_max_delay": 30.0,
    "auto_fetch": true
}
```

### Features

| Feature | Description |
|---------|-------------|
| **Parallel Push** | Push to multiple remotes simultaneously for faster sync |
| **Branch Filtering** | Configure which branches sync to which remotes using glob patterns |
| **Force Push Protection** | Block, warn, or allow force pushes per remote |
| **Retry with Backoff** | Auto-retry failed pushes with exponential backoff |
| **Offline Queue** | Queue failed pushes for later when connectivity returns |
| **Health Checks** | Validate remote connectivity before operations |
| **Divergence Detection** | Detect when remotes have diverged from each other |
| **Sync Dashboard** | Visual status of all remotes (ahead/behind/diverged) |
| **Auto-Discovery** | Automatically detect configured git remotes |
| **VPN Support** | Auto-connect VPN for remotes behind firewalls |
| **Filesystem Sync** | Sync to local paths (external drives, NAS) via rsync |
| **Rsync Targets** | Sync to remote servers via SSH + rsync |

### Remote Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `priority` | int | 1 | Push order (lower = first) |
| `branches` | list | `["*"]` | Branch patterns to sync (`*`, `main`, `feature/*`) |
| `force_push` | string | `"block"` | Policy: `"allow"`, `"warn"`, or `"block"` |
| `retry` | int | 3 | Number of retry attempts |
| `timeout` | int | 30 | Push timeout in seconds |
| `group` | string | `"default"` | Group name for organization |
| `vpn` | string | `null` | VPN name to use for this remote |

### VPN Configuration

For remotes behind firewalls or on private networks, configure VPN auto-connection:

```json
{
    "vpn": {
        "corporate": {
            "connect_cmd": "your-vpn-connect-command",
            "disconnect_cmd": "your-vpn-disconnect-command",
            "check_cmd": "command-to-check-if-connected",
            "timeout": 30,
            "auto_connect": true
        }
    }
}
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `connect_cmd` | string | required | Shell command to connect VPN |
| `disconnect_cmd` | string | required | Shell command to disconnect VPN |
| `check_cmd` | string | `""` | Command to check if VPN is connected (exit 0 = connected) |
| `timeout` | int | 30 | Timeout for VPN operations in seconds |
| `auto_connect` | bool | `true` | Auto-connect if remote is unreachable |

#### VPN Examples

**macOS (built-in VPN):**
```json
{
    "vpn": {
        "corporate": {
            "connect_cmd": "networksetup -connectpppoeservice 'My VPN'",
            "disconnect_cmd": "networksetup -disconnectpppoeservice 'My VPN'",
            "check_cmd": "scutil --nc status 'My VPN' | grep -q Connected"
        }
    }
}
```

**OpenVPN:**
```json
{
    "vpn": {
        "office": {
            "connect_cmd": "sudo openvpn --config /etc/openvpn/office.conf --daemon",
            "disconnect_cmd": "sudo killall openvpn",
            "check_cmd": "pgrep openvpn"
        }
    }
}
```

**WireGuard:**
```json
{
    "vpn": {
        "wg-tunnel": {
            "connect_cmd": "wg-quick up wg0",
            "disconnect_cmd": "wg-quick down wg0",
            "check_cmd": "wg show wg0"
        }
    }
}
```

**SSH Tunnel (SOCKS proxy):**
```json
{
    "vpn": {
        "ssh-tunnel": {
            "connect_cmd": "ssh -f -N -D 1080 jumphost",
            "disconnect_cmd": "pkill -f 'ssh.*jumphost'",
            "check_cmd": "pgrep -f 'ssh.*jumphost'"
        }
    }
}
```

**Inline VPN Config** (per-remote):
```json
{
    "remotes": {
        "private-server": {
            "vpn": {
                "connect_cmd": "ssh -f -N -L 2222:internal:22 bastion",
                "disconnect_cmd": "pkill -f 'ssh.*bastion'"
            }
        }
    }
}
```

### Filesystem & Rsync Sync Targets

In addition to git remotes, you can sync your repository to local filesystem paths or remote servers via rsync. This is useful for:
- **Local backups**: External drives, NAS, other directories
- **Remote backups**: Servers accessible via SSH
- **Non-git destinations**: Deployment targets, shared folders

#### Configuration

Add `sync_targets` to your `.remotesyncrc.json`:

```json
{
    "sync_targets": {
        "external-drive": {
            "path": "/Volumes/Backup/projects/my-repo",
            "exclude": [".git", "__pycache__", "*.pyc", "node_modules"],
            "delete": false
        },
        "nas": {
            "path": "/mnt/nas/backups/my-repo",
            "delete": true
        },
        "deploy-server": {
            "host": "deploy.example.com",
            "path": "/var/www/my-app",
            "user": "deploy",
            "port": 22,
            "ssh_key": "~/.ssh/deploy_key",
            "exclude": [".git", ".env", "node_modules"],
            "delete": true
        }
    }
}
```

#### Filesystem Target Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `path` | string | required | Local destination path |
| `exclude` | list | `[".git", "__pycache__", "*.pyc", ".DS_Store"]` | Patterns to exclude |
| `delete` | bool | `false` | Delete files not in source |

#### Rsync Target Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `host` | string | required | Remote hostname |
| `path` | string | required | Remote destination path |
| `user` | string | `""` | SSH username |
| `port` | int | `22` | SSH port |
| `ssh_key` | string | `""` | Path to SSH private key |
| `exclude` | list | `[".git", "__pycache__", "*.pyc", ".DS_Store"]` | Patterns to exclude |
| `delete` | bool | `false` | Delete files not in source |
| `options` | list | `[]` | Additional rsync options |

#### Usage

```bash
# Sync to all configured sync targets
remote-sync --sync-targets

# Sync to specific targets
remote-sync --sync-targets --target backup,nas

# Sync everything (remotes + targets)
remote-sync --sync-all

# Preview sync with dry-run
remote-sync --sync-targets --dry-run
```

### CLI Options

```
Usage: remote-sync [options]

Actions (mutually exclusive):
  --push              Push current branch to all configured remotes
  --push-all          Push all branches to their configured remotes
  --status            Show sync status dashboard
  --health-check      Check connectivity to all remotes
  --process-queue     Process offline queue of failed pushes
  --clear-queue       Clear the offline queue
  --show-queue        Show offline queue contents
  --sync-targets      Sync to filesystem/rsync targets
  --sync-all          Sync to all remotes AND sync targets

Options:
  --config PATH       Path to configuration file
  --remote NAME       Target specific remote(s), comma-separated
  --target NAME       Target specific sync target(s), comma-separated
  --branch NAME       Target specific branch (default: current)
  --force             Allow force push (requires explicit flag)
  --no-parallel       Disable parallel pushing
  --dry-run           Preview changes without executing
  --verbose, -v       Show detailed output
  --quiet, -q         Suppress all output except errors
  --version           Show version number
  --help              Show help message
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `REMOTE_SYNC_PARALLEL` | Set to `false` to disable parallel push |
| `REMOTE_SYNC_DRY_RUN` | Set to `true` for dry run mode |
| `REMOTE_SYNC_VERBOSE` | Set to `true` for verbose output |
| `REMOTE_SYNC_OFFLINE_QUEUE` | Set to `true`/`false` to enable/disable queue |
| `REMOTE_SYNC_MAX_WORKERS` | Maximum parallel workers (default: 4) |

### Pre-commit Hook Integration

Add remote-sync as a post-commit hook in `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: remote-sync
        name: Sync to all remotes
        entry: remote-sync --push --quiet
        language: python
        pass_filenames: false
        always_run: true
        stages: [post-commit]
```

### Sync Status Dashboard

The `--status` command shows a visual dashboard:

```
Sync Status Dashboard
  Branch: main

  ✓ origin (priority: 1)
    State: in sync
    Local:  abc1234
    Remote: abc1234

  ↑ github-mirror (priority: 2)
    State: ahead by 3 commit(s)
    Local:  abc1234
    Remote: def5678

  ⚠ backup (priority: 3)
    State: diverged (+2/-1)
    Local:  abc1234
    Remote: ghi9012
```

### Offline Queue

When pushes fail (network issues, authentication), they're automatically queued:

```bash
# View queued pushes
remote-sync --show-queue

# Retry all queued pushes
remote-sync --process-queue

# Clear the queue
remote-sync --clear-queue
```

The queue persists in `.remote-sync-queue.json` and survives restarts.

---
