# Git Flow with Copilot CLI

# Smart commit: groups files logically and uses conventional commits
commit-ai:
    copilot --allow-all --model claude-sonnet-4.5 -p "\
    You are a git expert following git flow and Conventional Commits (conventionalcommits.org). \
    \
    TASK: Analyze staged files and create clean, atomic commits. \
    \
    STEPS: \
    1. Run 'git status --porcelain' to see all staged files \
    2. Run 'git branch --show-current' to detect branch type \
    3. Group related files logically for atomic commits: \
       - Tests together \
       - Documentation together \
       - Config/build files together \
       - Source files by module or feature \
    4. For each group: unstage others, commit that group, re-stage remaining \
    5. Infer commit type from branch name: \
       - feature/* → feat: (new feature, correlates with MINOR in SemVer) \
       - hotfix/* → fix: (bug fix, correlates with PATCH in SemVer) \
       - release/* → chore: or appropriate type \
    \
    CONVENTIONAL COMMIT FORMAT: \
    <type>[optional scope]: <description> \
    \
    [optional body] \
    \
    [optional footer(s)] \
    \
    TYPES (per Angular/commitlint convention): \
    - feat: new feature (MINOR version bump) \
    - fix: bug fix (PATCH version bump) \
    - docs: documentation only \
    - test: adding/fixing tests \
    - build: build system or dependencies \
    - ci: CI configuration \
    - chore: maintenance tasks \
    - refactor: code change that neither fixes bug nor adds feature \
    - perf: performance improvement \
    - style: formatting, whitespace (no code change) \
    \
    BREAKING CHANGES: \
    - Append ! after type/scope: feat!: or feat(api)!: \
    - Or add footer: BREAKING CHANGE: <description> \
    - Correlates with MAJOR version bump \
    \
    RULES: \
    - Each commit must be atomic and focused on one thing \
    - Never mix tests with source changes in same commit \
    - Never mix docs with code changes in same commit \
    - Use imperative mood in description: 'add' not 'added' \
    - Keep first line under 72 characters \
    - Scope is optional but helpful: feat(binary_track): add health check \
    \
    Execute the commits now."

# Quick commit with message
cm message:
    git commit -m "{{message}}"

# Start a feature branch
feature name:
    git flow feature start {{name}}

# Finish feature and merge to develop
finish-feature:
    git flow feature finish

# Start a release
release version:
    git flow release start {{version}}

# Finish release and merge to main + develop
finish-release:
    git flow release finish

# Start a hotfix
hotfix version:
    git flow hotfix start {{version}}

# Finish hotfix
finish-hotfix:
    git flow hotfix finish

# Quick merge develop to main
ship:
    git checkout main
    git merge --no-ff develop
    git push origin main --tags
    git checkout develop

# Push current branch
push:
    git push origin $(git branch --show-current)

# Sync with remote
sync:
    git fetch --all --prune
    git pull --rebase

# Show status
status:
    git status -sb

# Run tests
test:
    uv run pytest tests/test_binary_track.py -v

# Run all tests
test-all:
    uv run pytest tests/ -v
