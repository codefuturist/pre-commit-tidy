# Git Flow Branch Protection

This repository follows the **Git Flow** branching model.

## Branches

### Permanent Branches

| Branch    | Purpose                         | Protected |
| --------- | ------------------------------- | :-------: |
| `main`    | Production releases only        |    ✅     |
| `develop` | Integration branch for features |    ✅     |

### Temporary Branches

| Prefix      | Purpose             | Base      | Merge To           |
| ----------- | ------------------- | --------- | ------------------ |
| `feature/*` | New features        | `develop` | `develop`          |
| `release/*` | Release preparation | `develop` | `main` + `develop` |
| `hotfix/*`  | Production fixes    | `main`    | `main` + `develop` |
| `bugfix/*`  | Bug fixes           | `develop` | `develop`          |

## Workflow

### Starting a Feature

```bash
git checkout develop
git pull origin develop
git checkout -b feature/my-feature
```

### Completing a Feature

```bash
git checkout develop
git pull origin develop
git merge --no-ff feature/my-feature
git push origin develop
git branch -d feature/my-feature
```

### Creating a Release

```bash
git checkout develop
git checkout -b release/1.1.0
# Bump version in pyproject.toml
# Update CHANGELOG.md
git commit -am "chore: bump version to 1.1.0"
git checkout main
git merge --no-ff release/1.1.0
git tag -a v1.1.0 -m "Release v1.1.0"
git checkout develop
git merge --no-ff release/1.1.0
git push origin main develop --tags
git branch -d release/1.1.0
```

### Hotfix

```bash
git checkout main
git checkout -b hotfix/1.0.1
# Fix the issue
# Bump patch version
git commit -am "fix: critical bug"
git checkout main
git merge --no-ff hotfix/1.0.1
git tag -a v1.0.1 -m "Hotfix v1.0.1"
git checkout develop
git merge --no-ff hotfix/1.0.1
git push origin main develop --tags
git branch -d hotfix/1.0.1
```

## Branch Protection Rules

Configure in GitHub Settings → Branches:

### `main` branch

- ✅ Require pull request before merging
- ✅ Require status checks to pass (test, lint)
- ✅ Require branches to be up to date
- ✅ Require linear history
- ✅ Do not allow deletions

### `develop` branch

- ✅ Require pull request before merging
- ✅ Require status checks to pass (test, lint)
- ✅ Do not allow deletions
