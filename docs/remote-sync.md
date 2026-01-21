# Remote Sync - Multi-Remote Synchronization

Keep multiple git remotes in sync automatically with parallel pushing, health checks, divergence detection, and offline queuing.

## CLI Options

```
Usage: remote-sync [options]

Options:
  --config PATH           Path to configuration file
  --push                  Push current branch to all remotes
  --status                Show sync status dashboard
  --health-check          Check connectivity to all remotes
  --dry-run               Preview without making changes
  --force                 Force push (use with caution)
  --remotes REMOTES       Comma-separated remotes to sync
  --branch BRANCH         Branch to push (default: current)
  --show-queue            Show offline queue
  --process-queue         Retry queued pushes
  --clear-queue           Clear the offline queue
  --verbose, -v           Show detailed output
  --quiet, -q             Suppress all output except errors
  --version               Show version number
```

## Configuration

Create a `.remotesyncrc.yaml` file in your project root:

```yaml
remotes:
  origin:
    priority: 1
    branches:
      - "*"
    force_push: "block"
    retry: 3
    timeout: 30
  github-mirror:
    priority: 2
    branches:
      - "main"
      - "develop"
    force_push: "warn"
  internal-server:
    priority: 3
    branches:
      - "main"
    vpn: "corporate"

vpn:
  corporate:
    connect_cmd: "networksetup -connectpppoeservice 'Corporate VPN'"
    disconnect_cmd: "networksetup -disconnectpppoeservice 'Corporate VPN'"
    check_cmd: "scutil --nc status 'Corporate VPN' | grep Connected"
    timeout: 30
    auto_connect: true

parallel: true
max_workers: 4
offline_queue: true
health_check_timeout: 5
retry_base_delay: 1.0
retry_max_delay: 30.0
auto_fetch: true
```

## Features

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

## Remote Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `priority` | int | 1 | Push order (lower = first) |
| `branches` | list | `["*"]` | Branch patterns to sync (`*`, `main`, `feature/*`) |
| `force_push` | string | `"block"` | Policy: `"allow"`, `"warn"`, or `"block"` |
| `retry` | int | 3 | Number of retry attempts |
| `timeout` | int | 30 | Push timeout in seconds |
| `vpn` | string | null | VPN profile name to auto-connect |
| `enabled` | bool | true | Enable/disable this remote |

## Sync Targets (Filesystem & Rsync)

Sync your repository to local filesystem paths or remote servers:

```yaml
sync_targets:
  backup:
    path: "/Volumes/Backup/projects/my-repo"
  nas:
    path: "/mnt/nas/backups/my-repo"
    delete: true
  deploy-server:
    host: "deploy.example.com"
    path: "/var/www/my-app"
    user: "deploy"
    ssh_key: "~/.ssh/deploy_key"
    exclude:
      - ".git"
      - ".env"
      - "node_modules"
    branch_mode: "specific"
    branch: "main"
    delete: true
```

### Smart Defaults

| Setting | Default | Rationale |
|---------|---------|-----------|
| `branch_mode` | `"match"` | Destination follows source branch automatically |
| `exclude` | Common build artifacts | `.git` is **NOT** excluded to preserve git history |
| `delete` | `false` | Safe default - won't delete extra files at destination |

## VPN Support

Configure VPN auto-connect for remotes behind firewalls:

```yaml
vpn:
  corporate:
    connect_cmd: "networksetup -connectpppoeservice 'Corporate VPN'"
    disconnect_cmd: "networksetup -disconnectpppoeservice 'Corporate VPN'"
    check_cmd: "scutil --nc status 'Corporate VPN' | grep Connected"
    timeout: 30
    auto_connect: true
```

## Offline Queue

When pushes fail (network issues, authentication), they're automatically queued:

```bash
remote-sync --show-queue      # View queued pushes
remote-sync --process-queue   # Retry all queued pushes
remote-sync --clear-queue     # Clear the queue
```

The queue persists in `.remote-sync-queue.json` and survives restarts.

## Pre-commit Hooks

```yaml
# Push to all remotes after commit
- id: remote-sync
  name: Remote Sync
  entry: remote-sync --push
  language: system
  pass_filenames: false
  stages: [post-commit]

# Push with health check first
- id: remote-sync
  name: Remote Sync (Safe)
  entry: remote-sync --push --health-check
  language: system
  pass_filenames: false
  stages: [post-commit]

# Status check only (no push)
- id: remote-sync-status
  name: Remote Status
  entry: remote-sync --status
  language: system
  pass_filenames: false
  stages: [manual]
```

## Status Dashboard

```
Remote Sync Status
==================

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
