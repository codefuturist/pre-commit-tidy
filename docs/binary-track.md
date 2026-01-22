# Binary Track - Local Binary Staleness Detection

Keep locally-built binaries up to date with source code changes. Automatically detect when binaries become stale and trigger rebuilds.

## Quick Start (No Config File Needed)

Track a binary with a single command using language presets:

```bash
# Single binary with Go preset
binary-track --binary=mytool --language=go --status

# Multiple binaries
binary-track --binary=tool1 --language=go --binary=tool2 --language=rust --check

# Generate a config file from inline definition
binary-track --binary=mytool --language=go --init
```

## CLI Options

```
Usage: binary-track [options] [files...]

Actions:
  --status                Show status of all tracked binaries
  --check                 Check for stale binaries (exit 1 if any stale)
  --check-staged          Check if staged files affect any binary (for pre-commit)
  --rebuild               Rebuild all stale binaries
  --rebuild-all           Rebuild all tracked binaries
  --watch                 Watch source files and rebuild on change
  --add                   Interactive add a new binary to track
  --remove NAME           Remove a binary from tracking
  --verify, --health      Check binary health (exists, executable, in PATH)
  --codesign              Sign all binaries (or re-sign after rebuild)
  --verify-signature      Verify codesigning status of all binaries
  --init                  Generate .binariesrc.yaml from current config
  --list-languages        List available language presets

Inline Configuration:
  --binary NAME           Define a binary to track (repeatable)
  --language LANG         Language preset (go, rust, python, node, etc.)
  --source-patterns PAT   Comma-separated source patterns
  --build-cmd CMD         Build command
  --install-path PATH     Installation path
  --test-cmd CMD          Test command to verify binary after build

Configuration Overrides:
  --config PATH           Path to configuration file (default: .binariesrc.yaml)
  --policy POLICY         Pre-commit policy (warn, block, ignore)
  --track-by METHOD       Tracking method (git_commit, mtime, hash)

Options:
  --dry-run               Preview changes without executing
  --verbose               Show detailed output
  --quiet                 Suppress all output except errors
  --json                  Output in JSON format
  --version               Show version number

Positional Arguments:
  files                   Files to check (passed by pre-commit with --check-staged)
```

## Language Presets

Use `--language` to auto-configure source patterns and build commands:

| Language | Patterns | Default Build Command |
|----------|----------|----------------------|
| `go` | `**/*.go`, `go.mod`, `go.sum` | `go build -o {install_path} ./cmd/{name}` |
| `rust` | `**/*.rs`, `Cargo.toml` | `cargo build --release && cp target/release/{name} {install_path}` |
| `python` | `**/*.py`, `pyproject.toml` | `pip install --user -e .` |
| `uv` | `**/*.py`, `pyproject.toml`, `uv.lock` | `uv tool install --force -e .` |
| `node` | `**/*.ts`, `**/*.js`, `package.json` | `npm run build && npm link` |
| `pnpm` | `**/*.ts`, `**/*.js`, `pnpm-lock.yaml` | `pnpm run build && pnpm link --global` |
| `swift` | `**/*.swift`, `Package.swift` | `swift build -c release && cp .build/release/{name} {install_path}` |
| `swift-app` | `**/*.swift`, `**/*.xcodeproj/**` | `xcodebuild -scheme {name} -configuration Release` |
| `c` | `**/*.c`, `**/*.h`, `Makefile` | `make && cp {name} {install_path}` |
| `cpp` | `**/*.cpp`, `**/*.hpp`, `CMakeLists.txt` | `cmake -B build && cmake --build build` |
| `zig` | `**/*.zig`, `build.zig` | `zig build -Doptimize=ReleaseFast` |
| `haskell` | `**/*.hs`, `**/*.cabal` | `stack build && stack install` |
| `elixir` | `**/*.ex`, `mix.exs` | `mix escript.build` |

List all presets: `binary-track --list-languages`

## Configuration File

For advanced use cases, create a `.binariesrc.yaml` file:

```yaml
binaries:
  mytool:
    language: go  # Use preset defaults
    # Override specific settings:
    source_patterns:
      - "cmd/mytool/**/*.go"
      - "internal/**/*.go"
    build_cmd: "go build -o ~/.local/bin/mytool ./cmd/mytool"
    install_path: "~/.local/bin/mytool"
  
  myapp:
    source_patterns:
      - "src/**/*.swift"
    build_cmd: "xcodebuild -scheme MyApp -configuration Release"
    install_path: "~/Applications/MyApp.app"
    binary_type: "gui"
    install_scope: "user"
    language: "swift"
    codesign:
      enabled: true
      identity: "Developer ID Application: Your Name"

# Global settings
auto_rebuild: false
stale_threshold_hours: 24
watch_debounce_ms: 500
pre_commit_policy: "warn"  # "warn", "block", or "ignore"
track_by: "git_commit"     # "git_commit", "mtime", or "hash"

# Codesigning (macOS only)
codesign:
  enabled: false
  identity: "-"              # "-" for ad-hoc, or certificate name
  entitlements: null
  options:
    - "runtime"
  force: true

# Custom system binary paths (optional)
system_binaries:
  git: "/usr/local/bin/git"
  codesign: "/usr/bin/codesign"
```

## Binary Configuration Options

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `source_patterns` | list | Yes | Glob patterns for source files to track |
| `build_cmd` | string | Yes | Command to build the binary |
| `install_path` | string | Yes | Path where binary is installed |
| `binary_type` | string | No | `"cli"` or `"gui"` (default: `"cli"`) |
| `install_scope` | string | No | `"user"` or `"system"` (default: `"user"`) |
| `language` | string | No | Language for better error detection |
| `rebuild_on_commit` | bool | No | Auto-rebuild on source changes (default: false) |
| `check_in_path` | bool | No | Warn if binary not in PATH (default: true for CLI) |
| `ensure_executable` | bool | No | Set executable permissions after build (default: true) |
| `test_cmd` | string | No | Command to verify binary after build |
| `test_timeout` | int | No | Test command timeout in seconds (default: 60) |
| `retry_count` | int | No | Retry failed builds N times (default: 0) |
| `retry_delay_seconds` | float | No | Delay between retries (default: 1.0) |

## Tracking Methods

| Method | Description | Performance | Accuracy |
|--------|-------------|-------------|----------|
| `git_commit` | Compare git commit SHAs (recommended) | Fast | High |
| `mtime` | Compare file modification times | Fast | Medium |
| `hash` | Compare file content hashes | Slow | Highest |

## Default Install Locations

Binary-track uses platform-specific conventions for binary installation:

### macOS

| Type | Scope | Path | In PATH? |
|------|-------|------|----------|
| CLI | user | `~/.local/bin` | Recommended |
| CLI | system | `/usr/local/bin` | Yes |
| GUI | user | `~/Applications` | N/A |
| GUI | system | `/Applications` | N/A |

### Linux

| Type | Scope | Path | In PATH? |
|------|-------|------|----------|
| CLI | user | `~/.local/bin` | XDG standard |
| CLI | system | `/usr/local/bin` | Yes |
| GUI | user | `~/.local/opt` | N/A |
| GUI | system | `/opt` | N/A |

### Windows

| Type | Scope | Path |
|------|-------|------|
| CLI | user | `%LOCALAPPDATA%\Programs` |
| CLI | system | `%ProgramFiles%` |
| GUI | user | `%LOCALAPPDATA%\Programs` |
| GUI | system | `%ProgramFiles%` |

## Pre-commit Integration

Binary-track integrates with pre-commit to automatically check if staged files affect any tracked binaries. This ensures developers are notified when source code changes require a binary rebuild.

### How It Works

Pre-commit passes staged files to binary-track, which then:
1. Checks if any file matches a binary's source patterns
2. Reports which binaries are affected
3. Exits based on the configured policy (warn, block, or ignore)

This is **simpler and faster** than tracking git commits or file hashes — just pattern matching on staged files.

### Option 1: Inline Configuration (Recommended)

Configure everything directly in `.pre-commit-config.yaml` — no separate config file needed:

```yaml
repos:
  - repo: local
    hooks:
      - id: binary-track
        name: Check if binaries need rebuild
        entry: binary-track --check-staged --binary=mytool --language=rust
        language: system
        # pass_filenames: true (default)
```

When you stage `src/main.rs` and commit:
- Pre-commit runs: `binary-track --check-staged --binary=mytool --language=rust src/main.rs`
- Binary-track checks: Does `src/main.rs` match `**/*.rs`? → Yes → warns about rebuild

#### Multiple Binaries Inline

```yaml
repos:
  - repo: local
    hooks:
      - id: binary-track
        name: Check if binaries need rebuild
        entry: binary-track --check-staged --binary=frontend --language=node --binary=backend --language=go
        language: system
```

#### Monorepo Support

For monorepos with multiple projects in subdirectories, use `--project-dir` to scope each binary to its project:

```yaml
# Single .pre-commit-config.yaml at monorepo root
repos:
  - repo: local
    hooks:
      - id: binary-track
        name: Check if binaries need rebuild
        entry: >-
          binary-track --check-staged
          --binary=api --language=go --project-dir=services/api
          --binary=cli --language=rust --project-dir=tools/cli
          --binary=web --language=node --project-dir=packages/web
        language: system
```

How it works:
- Changes to `services/api/main.go` → only triggers rebuild warning for `api`
- Changes to `tools/cli/src/main.rs` → only triggers rebuild warning for `cli`
- Changes to `packages/web/index.ts` → only triggers rebuild warning for `web`
- Changes to `README.md` (at root) → no warnings (not in any project-dir)

Each `--project-dir` applies to the preceding `--binary`. Files outside a binary's project directory are ignored for that binary.

#### With Block Policy (Prevent Commits)

```yaml
repos:
  - repo: local
    hooks:
      - id: binary-track
        name: Check if binaries need rebuild
        entry: binary-track --check-staged --binary=mytool --language=rust --policy=block
        language: system
```

### Option 2: Config File (For Advanced Setups)

For complex configurations with codesigning, services, or many binaries:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: binary-track
        name: Check if binaries need rebuild
        entry: binary-track --check-staged
        language: system
```

```yaml
# .binariesrc.yaml (separate file)
binaries:
  mytool:
    language: go
    codesign:
      enabled: true
      identity: "Developer ID"
    service:
      enabled: true
      type: launchd
      name: com.example.mytool

pre_commit_policy: warn
```

### Migrating from Inline to Config File

When your setup grows, generate a config file from your inline args:

```bash
binary-track --binary=mytool --language=go --init
```

This creates `.binariesrc.yaml` and you can simplify your pre-commit config.

### Pre-commit Policy Options

Configure how binary-track behaves when staged files affect binaries:

```yaml
# Via inline arg:
--policy=warn

# Or in .binariesrc.yaml:
pre_commit_policy: "warn"
```

| Policy | Behavior | Use Case |
|--------|----------|----------|
| `warn` | Print warning but allow commit | Development workflow (default) |
| `block` | Prevent commit if binaries stale | Strict enforcement |
| `ignore` | Skip check during pre-commit | Disable integration |

### Environment Variable Override

You can also control the policy via environment variable:

```bash
export BINARY_TRACK_POLICY=block
```

This is useful for CI/CD pipelines or team-specific requirements.

### Example Workflow

1. **Initial setup**: Define binary inline or via config
   ```bash
   # Quick start (no config file)
   binary-track --binary=mytool --language=go --status
   
   # Or generate a config file
   binary-track --binary=mytool --language=go --init
   ```

2. **Configure pre-commit hook**: Add to `.pre-commit-config.yaml` (see above)

3. **Make source changes**: Edit your Go/Swift/Rust source files

4. **Commit attempt**: Pre-commit runs binary-track
   ```bash
   git commit -m "Update feature"
   ```

5. **If stale** (policy: warn):
   ```
   ⚠ 1 stale binary(ies) detected. Consider running: binary-track --rebuild
   [develop abc1234] Update feature
   ```

6. **If stale** (policy: block):
   ```
   ✗ Commit blocked: 1 stale binary(ies). Run: binary-track --rebuild
   ```

7. **Rebuild binaries**:
   ```bash
   binary-track --rebuild
   ```

### Per-binary Pre-commit Control

You can control whether specific binaries trigger pre-commit checks:

```yaml
binaries:
  production-tool:
    # ... other config ...
    rebuild_on_commit: true  # Warn/block on commits
  
  experimental-tool:
    # ... other config ...
    rebuild_on_commit: false  # Skip pre-commit checks
```

## Codesigning (macOS)

Binary-track supports automatic codesigning after builds:

```yaml
codesign:
  enabled: true
  identity: "Developer ID Application: Your Name"  # or "-" for ad-hoc
  entitlements: "entitlements.plist"  # optional
  options:
    - "runtime"  # hardened runtime
  force: true    # replace existing signatures
```

### Per-binary Codesigning

Override global settings per binary:

```yaml
binaries:
  myapp:
    # ... other config ...
    codesign:
      enabled: true
      identity: "Developer ID Application: Company Name"
```

### Verify Signatures

```bash
# Sign all binaries
binary-track --codesign

# Verify signatures
binary-track --verify-signature
```

## Service Management

For binaries running as system services (daemons, background agents):

```yaml
binaries:
  mydaemon:
    # ... other config ...
    service:
      enabled: true
      type: "launchd"           # "launchd", "systemd", or "custom"
      name: "com.example.mydaemon"
      restart_after_build: true
      stop_timeout_seconds: 30
      start_timeout_seconds: 10
```

Binary-track will automatically stop services before rebuilds and restart them after successful builds.

### Service Types

| Type | Platform | Auto-detected |
|------|----------|---------------|
| `launchd` | macOS | Yes |
| `systemd` | Linux | Yes |
| `custom` | Any | Manual commands required |

### Custom Service Commands

```yaml
service:
  type: "custom"
  stop_cmd: "sudo systemctl stop mydaemon"
  start_cmd: "sudo systemctl start mydaemon"
  status_cmd: "sudo systemctl status mydaemon"
```

## Test Commands

Verify binaries work after building:

```yaml
binaries:
  mytool:
    # ... other config ...
    test_cmd: "mytool --version"
    test_timeout: 60
```

Test failures are recorded in the build manifest:

```bash
binary-track --status
# ⚠ mytool - TEST_FAILED (last build succeeded but tests failed)
```

## Watch Mode

Continuously monitor source files and auto-rebuild:

```bash
binary-track --watch
```

Uses a debounce timer to avoid rebuild spam during rapid file changes.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `BINARY_TRACK_DRY_RUN` | Set to `true` for dry run |
| `BINARY_TRACK_VERBOSE` | Set to `true` for verbose output |
| `BINARY_TRACK_AUTO_REBUILD` | Set to `true` to auto-rebuild stale binaries |
| `BINARY_TRACK_POLICY` | Pre-commit policy (`warn`/`block`/`ignore`) |
| `BINARY_TRACK_CODESIGN` | Set to `true` to enable codesigning |
| `BINARY_TRACK_CODESIGN_ID` | Codesigning identity (default: `"-"`) |

## Shadow Binary Conflicts

Binary-track detects "shadow conflicts" — when multiple versions of the same binary exist in different PATH locations:

```bash
binary-track --health

# ⚠ mytool shadowed by /usr/local/bin/mytool
#   Your version: ~/.local/bin/mytool (priority: 200)
#   Shadow version: /usr/local/bin/mytool (priority: 100)
#   Shadow will execute first - consider moving or removing
```

## Build Manifest

Binary-track maintains a `.binary-track-manifest.json` file tracking build history:

```json
{
  "binaries": {
    "mytool": {
      "last_build_time": "2026-01-22T10:30:00Z",
      "last_build_commit": "abc1234def5678",
      "source_fingerprint": "sha256:...",
      "build_duration_seconds": 12.5,
      "status": "current",
      "failure_count": 0,
      "last_test_status": "passed"
    }
  },
  "last_check_time": "2026-01-22T11:00:00Z"
}
```

This manifest should be added to `.gitignore` as it's machine-specific.

## Usage Examples

### Check status
```bash
binary-track --status
```

### Check for stale binaries (CI/CD)
```bash
binary-track --check || echo "Binaries need rebuilding"
```

### Rebuild stale binaries
```bash
binary-track --rebuild
```

### Rebuild everything
```bash
binary-track --rebuild-all
```

### Add a new binary interactively
```bash
binary-track --add
```

### Remove a binary from tracking
```bash
binary-track --remove mytool
```

### Dry run
```bash
binary-track --rebuild --dry-run
```

### JSON output (for scripts)
```bash
binary-track --status --json | jq '.stale_binaries'
```

## Integration with CI/CD

### GitHub Actions

```yaml
name: Check Binaries
on: [push, pull_request]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Install binary-track
        run: pip install pre-commit-tidy
      - name: Check for stale binaries
        run: binary-track --check
```

### Pre-push Hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: binary-track-rebuild
        name: Rebuild stale binaries
        entry: binary-track --rebuild
        language: system
        pass_filenames: false
        always_run: true
        stages: [push]
```

## Retry & Recovery

Configure automatic retries for flaky builds:

```yaml
binaries:
  mytool:
    # ... other config ...
    retry_count: 3
    retry_delay_seconds: 2.0
```

Failed builds are recorded in the manifest with failure counts and reasons.

## Troubleshooting

### Binary not in PATH

```bash
binary-track --health
# Follow the instructions to add the install path to your PATH
```

### Build failures

```bash
binary-track --rebuild --verbose
# View detailed build output and error categorization
```

### Signature verification failures

```bash
binary-track --verify-signature --verbose
# See detailed codesigning status
```

### Permission issues

Ensure the install directory is writable:

```bash
mkdir -p ~/.local/bin
chmod u+w ~/.local/bin
```

For system-scoped installs, you may need `sudo`:

```bash
sudo binary-track --rebuild
```
