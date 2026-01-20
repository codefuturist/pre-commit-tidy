# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability within pre-commit-tidy, please:

1. **Do not** open a public issue
2. Email the maintainer directly or use GitHub's private vulnerability reporting
3. Include:
   - A description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will respond within 48 hours and work with you to understand and address the issue.

## Security Best Practices

When using pre-commit-tidy:

- Review the configuration file (`.tidyrc.json`) to ensure it doesn't expose sensitive directories
- Be cautious with `overwrite` duplicate strategy in shared environments
- Run with `--dry-run` first to preview changes before applying
