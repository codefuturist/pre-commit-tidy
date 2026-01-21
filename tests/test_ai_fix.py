"""Tests for ai_fix module."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from pre_commit.ai_fix import (
    AIFixConfig,
    AIFixConfigDict,
    AIFixRunner,
    BehaviorConfig,
    CacheConfig,
    Colors,
    ErrorComplexity,
    ESLintParser,
    FixResult,
    FixStrategy,
    IterationProgress,
    LintError,
    LinterRuntimeConfig,
    Logger,
    MypyParser,
    ProviderConfig,
    RuffParser,
    Severity,
    TypeScriptParser,
    dedupe_errors,
    detect_linters,
    get_file_context,
    get_fix_strategy,
    load_config_file,
    load_env_config,
    sort_errors,
)


# =============================================================================
# Test LintError
# =============================================================================


class TestLintError:
    """Tests for LintError dataclass."""

    def test_location_key(self) -> None:
        """Test location key generation."""
        error = LintError(
            linter='ruff',
            file='test.py',
            line=10,
            column=5,
            code='E501',
            message='Line too long',
            severity=Severity.WARNING,
        )
        assert error.location_key == 'test.py:10:5'

    def test_location_key_no_column(self) -> None:
        """Test location key without column."""
        error = LintError(
            linter='mypy',
            file='test.py',
            line=10,
            column=None,
            code='error',
            message='Type error',
            severity=Severity.ERROR,
        )
        assert error.location_key == 'test.py:10:0'

    def test_priority(self) -> None:
        """Test error priority."""
        security = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='S101', message='Security', severity=Severity.ERROR,
            category='security',
        )
        lint = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='E501', message='Lint', severity=Severity.WARNING,
            category='lint',
        )
        style = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='I001', message='Style', severity=Severity.INFO,
            category='style',
        )

        assert security.priority < lint.priority < style.priority

    def test_matches_pattern_wildcard(self) -> None:
        """Test pattern matching with wildcard."""
        error = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='E501', message='Test', severity=Severity.WARNING,
        )
        assert error.matches_pattern('*')
        assert error.matches_pattern('ruff')
        assert error.matches_pattern('ruff:*')
        assert error.matches_pattern('ruff:E501')
        assert not error.matches_pattern('eslint')
        assert not error.matches_pattern('ruff:E502')

    def test_matches_pattern_glob(self) -> None:
        """Test pattern matching with glob."""
        error = LintError(
            linter='eslint', file='t.js', line=1, column=None,
            code='import/order', message='Test', severity=Severity.WARNING,
        )
        assert error.matches_pattern('eslint:import/*')
        assert not error.matches_pattern('eslint:no-*')

    def test_complexity_simple(self) -> None:
        """Test complexity classification for simple errors."""
        # Unused import - simple
        error = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='F401', message='Unused import', severity=Severity.WARNING,
        )
        assert error.complexity == ErrorComplexity.SIMPLE

        # isort - simple
        error2 = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='I001', message='Import order', severity=Severity.WARNING,
        )
        assert error2.complexity == ErrorComplexity.SIMPLE

    def test_complexity_moderate(self) -> None:
        """Test complexity classification for moderate errors."""
        # Type error - moderate
        error = LintError(
            linter='mypy', file='t.py', line=1, column=None,
            code='arg-type', message='Type error', severity=Severity.ERROR,
        )
        assert error.complexity == ErrorComplexity.MODERATE

    def test_complexity_complex(self) -> None:
        """Test complexity classification for complex errors."""
        # Security issue - complex
        error = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='S101', message='Security issue', severity=Severity.ERROR,
            category='security',
        )
        assert error.complexity == ErrorComplexity.COMPLEX

        # Bugbear - complex
        error2 = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='B006', message='Mutable default', severity=Severity.WARNING,
        )
        assert error2.complexity == ErrorComplexity.COMPLEX

    def test_to_dict(self) -> None:
        """Test dictionary conversion."""
        error = LintError(
            linter='ruff', file='test.py', line=10, column=5,
            code='E501', message='Line too long', severity=Severity.WARNING,
            suggestion='Split the line',
        )
        d = error.to_dict()
        assert d['linter'] == 'ruff'
        assert d['file'] == 'test.py'
        assert d['line'] == 10
        assert d['suggestion'] == 'Split the line'


# =============================================================================
# Test Linter Parsers
# =============================================================================


class TestRuffParser:
    """Tests for Ruff parser."""

    def test_parse_json(self) -> None:
        """Test parsing Ruff JSON output."""
        output = json.dumps([
            {
                'filename': 'test.py',
                'location': {'row': 10, 'column': 5},
                'code': 'E501',
                'message': 'Line too long (120 > 88)',
                'fix': {'message': 'Wrap line'},
            },
        ])

        parser = RuffParser()
        errors = parser.parse(output)

        assert len(errors) == 1
        assert errors[0].file == 'test.py'
        assert errors[0].line == 10
        assert errors[0].code == 'E501'
        assert errors[0].suggestion == 'Wrap line'

    def test_parse_empty(self) -> None:
        """Test parsing empty output."""
        parser = RuffParser()
        errors = parser.parse('')
        assert errors == []

    def test_categorize(self) -> None:
        """Test error categorization."""
        parser = RuffParser()
        assert parser._categorize('S101') == 'security'
        assert parser._categorize('E501') == 'lint'
        assert parser._categorize('I001') == 'style'


class TestMypyParser:
    """Tests for mypy parser."""

    def test_parse_text(self) -> None:
        """Test parsing mypy text output."""
        output = '''test.py:10:5: error: Argument 1 has incompatible type "str" [arg-type]
test.py:20: warning: Unused variable [unused]'''

        parser = MypyParser()
        errors = parser.parse(output)

        assert len(errors) == 2
        assert errors[0].file == 'test.py'
        assert errors[0].line == 10
        assert errors[0].column == 5
        assert errors[0].code == 'arg-type'
        assert errors[0].severity == Severity.ERROR

        assert errors[1].line == 20
        assert errors[1].severity == Severity.WARNING

    def test_parse_json(self) -> None:
        """Test parsing mypy JSON output."""
        output = json.dumps({
            'file': 'test.py',
            'line': 10,
            'column': 5,
            'code': 'arg-type',
            'message': 'Incompatible type',
            'severity': 'error',
        })

        parser = MypyParser()
        errors = parser.parse(output)

        assert len(errors) == 1
        assert errors[0].code == 'arg-type'


class TestESLintParser:
    """Tests for ESLint parser."""

    def test_parse_json(self) -> None:
        """Test parsing ESLint JSON output."""
        output = json.dumps([
            {
                'filePath': '/path/to/test.js',
                'messages': [
                    {
                        'line': 10,
                        'column': 5,
                        'ruleId': 'no-unused-vars',
                        'message': 'Unused variable',
                        'severity': 2,
                    },
                    {
                        'line': 20,
                        'column': 1,
                        'ruleId': 'import/order',
                        'message': 'Wrong import order',
                        'severity': 1,
                    },
                ],
            },
        ])

        parser = ESLintParser()
        errors = parser.parse(output)

        assert len(errors) == 2
        assert errors[0].file == '/path/to/test.js'
        assert errors[0].code == 'no-unused-vars'
        assert errors[0].severity == Severity.ERROR

        assert errors[1].code == 'import/order'
        assert errors[1].severity == Severity.WARNING


class TestTypeScriptParser:
    """Tests for TypeScript parser."""

    def test_parse_output(self) -> None:
        """Test parsing tsc output."""
        output = '''test.ts(10,5): error TS2345: Argument of type 'string' is not assignable.
test.ts(20,1): warning TS6133: 'x' is declared but never used.'''

        parser = TypeScriptParser()
        errors = parser.parse(output)

        assert len(errors) == 2
        assert errors[0].file == 'test.ts'
        assert errors[0].line == 10
        assert errors[0].column == 5
        assert errors[0].code == 'TS2345'


# =============================================================================
# Test Config Loading
# =============================================================================


class TestConfigLoading:
    """Tests for configuration loading."""

    def test_load_yaml_config(self, tmp_path: Path) -> None:
        """Test loading YAML config."""
        config_file = tmp_path / '.aifixrc.yaml'
        config_file.write_text('''
ai_provider: mistral
behavior:
  context_lines: 10
''')

        os.chdir(tmp_path)
        config = load_config_file()

        assert config.get('ai_provider') == 'mistral'
        assert config.get('behavior', {}).get('context_lines') == 10

    def test_load_json_config(self, tmp_path: Path) -> None:
        """Test loading JSON config."""
        config_file = tmp_path / '.aifixrc.json'
        config_file.write_text(json.dumps({
            'ai_provider': 'ollama',
            'providers': {
                'ollama': {'model': 'codellama'},
            },
        }))

        config = load_config_file(config_file)

        assert config.get('ai_provider') == 'ollama'

    def test_load_env_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test loading config from environment."""
        monkeypatch.setenv('AI_FIX_PROVIDER', 'mistral')

        config = load_env_config()

        assert config.get('ai_provider') == 'mistral'

    def test_config_from_dict(self) -> None:
        """Test AIFixConfig.from_dict."""
        data: AIFixConfigDict = {
            'ai_provider': 'ollama',
            'behavior': {
                'context_lines': 10,
                'max_errors_per_file': 20,
            },
            'fix_strategies': {
                'auto_fix': ['ruff:*'],
                'never_fix': ['security:*'],
            },
        }

        config = AIFixConfig.from_dict(data)

        assert config.ai_provider == 'ollama'
        assert config.behavior.context_lines == 10
        assert 'ruff:*' in config.auto_fix_patterns

    def test_config_batch_sizes(self) -> None:
        """Test batch size configuration."""
        data: AIFixConfigDict = {
            'behavior': {
                'batch_size_simple': 15,
                'batch_size_moderate': 5,
                'batch_size_complex': 1,
            },
        }

        config = AIFixConfig.from_dict(data)

        assert config.behavior.batch_size_simple == 15
        assert config.behavior.batch_size_moderate == 5
        assert config.behavior.batch_size_complex == 1


# =============================================================================
# Test Iteration Progress
# =============================================================================


class TestIterationProgress:
    """Tests for IterationProgress tracking."""

    def test_add_batch_result(self) -> None:
        """Test adding batch results."""
        progress = IterationProgress()
        progress.add_batch_result(fixed=3, failed=1, skipped=2)

        assert progress.total_fixed == 3
        assert progress.total_failed == 1
        assert progress.total_skipped == 2

        progress.add_batch_result(fixed=2, failed=0, skipped=1)

        assert progress.total_fixed == 5
        assert progress.total_failed == 1
        assert progress.total_skipped == 3

    def test_should_continue(self) -> None:
        """Test iteration continuation logic."""
        progress = IterationProgress()
        progress.iteration = 1

        assert progress.should_continue(max_iterations=3)

        progress.iteration = 3
        assert not progress.should_continue(max_iterations=3)

    def test_summary(self) -> None:
        """Test summary generation."""
        progress = IterationProgress()
        progress.iteration = 2
        progress.total_fixed = 5
        progress.total_failed = 1
        progress.total_skipped = 3

        summary = progress.summary()

        assert 'Iteration 2' in summary
        assert '5 fixed' in summary
        assert '1 failed' in summary


# =============================================================================
# Test Error Processing
# =============================================================================


class TestErrorProcessing:
    """Tests for error processing functions."""

    def test_dedupe_errors(self) -> None:
        """Test error deduplication."""
        errors = [
            LintError(
                linter='ruff', file='t.py', line=10, column=5,
                code='E501', message='Line too long', severity=Severity.WARNING,
                category='lint',
            ),
            LintError(
                linter='mypy', file='t.py', line=10, column=5,
                code='error', message='Type error', severity=Severity.ERROR,
                category='type',  # Higher priority
            ),
        ]

        deduped = dedupe_errors(errors)

        assert len(deduped) == 1
        assert deduped[0].linter == 'mypy'  # Higher priority kept

    def test_sort_errors(self) -> None:
        """Test error sorting."""
        errors = [
            LintError(
                linter='ruff', file='b.py', line=10, column=None,
                code='E501', message='Lint', severity=Severity.WARNING,
                category='lint',
            ),
            LintError(
                linter='ruff', file='a.py', line=5, column=None,
                code='S101', message='Security', severity=Severity.ERROR,
                category='security',
            ),
        ]

        sorted_errors = sort_errors(errors)

        assert sorted_errors[0].category == 'security'  # Higher priority first
        assert sorted_errors[0].file == 'a.py'

    def test_get_fix_strategy(self) -> None:
        """Test fix strategy determination."""
        config = AIFixConfig(
            auto_fix_patterns=['ruff:*'],
            never_fix_patterns=['security:*'],
            prompt_fix_patterns=['*'],
        )

        ruff_error = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='E501', message='Test', severity=Severity.WARNING,
        )
        security_error = LintError(
            linter='ruff', file='t.py', line=1, column=None,
            code='S101', message='Test', severity=Severity.ERROR,
            category='security',
        )

        assert get_fix_strategy(ruff_error, config) == FixStrategy.AUTO
        # Note: security_error doesn't match 'security:*' pattern unless
        # the code starts with 'security', so it will match 'ruff:*'


# =============================================================================
# Test File Context
# =============================================================================


class TestFileContext:
    """Tests for file context extraction."""

    def test_get_file_context(self, tmp_path: Path) -> None:
        """Test getting context around a line."""
        test_file = tmp_path / 'test.py'
        test_file.write_text('''line 1
line 2
line 3
line 4
line 5
line 6
line 7
line 8
line 9
line 10
''')

        context, start_line = get_file_context(test_file, 5, context_lines=2)

        assert 'line 3' in context
        assert 'line 4' in context
        assert 'line 5' in context
        assert 'line 6' in context
        assert 'line 7' in context
        assert start_line == 3


# =============================================================================
# Test Linter Detection
# =============================================================================


class TestLinterDetection:
    """Tests for linter auto-detection."""

    def test_detect_python_linters(self, tmp_path: Path) -> None:
        """Test detecting Python linters."""
        (tmp_path / 'pyproject.toml').write_text('[build-system]')

        with mock.patch('shutil.which') as mock_which:
            mock_which.side_effect = lambda x: f'/usr/bin/{x}' if x in ('ruff', 'mypy') else None
            linters = detect_linters(tmp_path)

        assert 'ruff' in linters
        assert 'mypy' in linters

    def test_detect_js_linters(self, tmp_path: Path) -> None:
        """Test detecting JavaScript linters."""
        (tmp_path / 'package.json').write_text('{}')
        (tmp_path / 'tsconfig.json').write_text('{}')

        with mock.patch('shutil.which') as mock_which:
            mock_which.return_value = '/usr/bin/eslint'
            linters = detect_linters(tmp_path)

        assert 'eslint' in linters
        assert 'tsc' in linters


# =============================================================================
# Test Logger
# =============================================================================


class TestLogger:
    """Tests for Logger class."""

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test JSON output mode."""
        log = Logger(json_output=True)
        log.info('Test message')
        log.error('Error message')
        log.flush_json()

        output = capsys.readouterr().out
        data = json.loads(output)

        assert len(data) == 2
        assert data[0]['level'] == 'info'
        assert data[1]['level'] == 'error'

    def test_quiet_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test quiet mode."""
        log = Logger(quiet=True)
        log.info('Should not appear')
        log.error('Should appear')

        output = capsys.readouterr().out
        assert 'Should not appear' not in output
        assert 'Should appear' in output


# =============================================================================
# Test Colors
# =============================================================================


class TestColors:
    """Tests for Colors class."""

    def test_disable_colors(self) -> None:
        """Test disabling colors."""
        original = Colors.RED
        Colors.disable()

        assert Colors.RED == ''
        assert Colors.GREEN == ''

        # Restore for other tests
        Colors.RED = '\033[31m'
        Colors.GREEN = '\033[32m'


# =============================================================================
# Test AIFixRunner
# =============================================================================


class TestAIFixRunner:
    """Tests for AIFixRunner."""

    def test_check_only_mode(self, tmp_path: Path) -> None:
        """Test check-only mode."""
        config = AIFixConfig(root_dir=tmp_path)
        runner = AIFixRunner(config, check_only=True)

        # Should return 0 with no files
        with mock.patch('pre_commit.ai_fix.get_staged_files', return_value=[]):
            result = runner.run()

        assert result == 0

    def test_no_provider_available(self, tmp_path: Path) -> None:
        """Test behavior when no provider is available."""
        config = AIFixConfig(root_dir=tmp_path)
        runner = AIFixRunner(config, check_only=False)
        runner.provider = None

        # Create a test file
        test_file = tmp_path / 'test.py'
        test_file.write_text('x = 1')

        with mock.patch('pre_commit.ai_fix.get_staged_files', return_value=['test.py']):
            with mock.patch('pre_commit.ai_fix.detect_linters', return_value=['ruff']):
                with mock.patch('pre_commit.ai_fix.run_linter', return_value=[
                    LintError(
                        linter='ruff', file='test.py', line=1, column=None,
                        code='E501', message='Test', severity=Severity.WARNING,
                    ),
                ]):
                    result = runner.run()

        # Should fail because no provider
        assert result == 1

    def test_group_by_complexity(self, tmp_path: Path) -> None:
        """Test error grouping by complexity."""
        config = AIFixConfig(root_dir=tmp_path)
        runner = AIFixRunner(config, check_only=True)

        errors = [
            LintError(linter='ruff', file='t.py', line=1, column=None,
                      code='F401', message='Unused import', severity=Severity.WARNING),
            LintError(linter='ruff', file='t.py', line=2, column=None,
                      code='S101', message='Security', severity=Severity.ERROR),
            LintError(linter='mypy', file='t.py', line=3, column=None,
                      code='arg-type', message='Type error', severity=Severity.ERROR),
        ]

        groups = runner._group_by_complexity(errors)

        assert len(groups[ErrorComplexity.SIMPLE]) == 1  # F401
        assert len(groups[ErrorComplexity.COMPLEX]) == 1  # S101
        assert len(groups[ErrorComplexity.MODERATE]) == 1  # mypy

    def test_create_batches(self, tmp_path: Path) -> None:
        """Test batch creation respecting complexity."""
        config = AIFixConfig(root_dir=tmp_path)
        config.behavior.batch_size_simple = 2
        config.behavior.batch_size_moderate = 1
        config.behavior.batch_size_complex = 1
        runner = AIFixRunner(config, check_only=True)

        # Create 5 simple errors
        errors = [
            LintError(linter='ruff', file='t.py', line=i, column=None,
                      code='F401', message='Unused import', severity=Severity.WARNING)
            for i in range(5)
        ]

        batches = runner._create_batches(errors)

        # Should create 3 batches: 2 + 2 + 1
        assert len(batches) == 3
        for complexity, batch in batches:
            assert complexity == ErrorComplexity.SIMPLE
            assert len(batch) <= 2

    def test_get_batch_size(self, tmp_path: Path) -> None:
        """Test batch size retrieval by complexity."""
        config = AIFixConfig(root_dir=tmp_path)
        config.behavior.batch_size_simple = 10
        config.behavior.batch_size_moderate = 3
        config.behavior.batch_size_complex = 1
        runner = AIFixRunner(config, check_only=True)

        assert runner._get_batch_size(ErrorComplexity.SIMPLE) == 10
        assert runner._get_batch_size(ErrorComplexity.MODERATE) == 3
        assert runner._get_batch_size(ErrorComplexity.COMPLEX) == 1


# =============================================================================
# Test AI Providers
# =============================================================================


class TestVibeProvider:
    """Tests for VibeProvider."""

    def test_is_available_no_vibe(self) -> None:
        """Test availability when vibe is not installed."""
        from pre_commit.ai_fix import ProviderConfig, VibeProvider

        provider = VibeProvider(ProviderConfig())
        with mock.patch('shutil.which', return_value=None):
            assert provider.is_available() is False

    def test_is_available_no_api_key(self) -> None:
        """Test availability when API key is missing."""
        from pre_commit.ai_fix import ProviderConfig, VibeProvider

        provider = VibeProvider(ProviderConfig())
        with mock.patch('shutil.which', return_value='/usr/bin/vibe'):
            with mock.patch.dict(os.environ, {}, clear=True):
                assert provider.is_available() is False

    def test_is_available_with_all(self) -> None:
        """Test availability when vibe and API key are present."""
        from pre_commit.ai_fix import ProviderConfig, VibeProvider

        provider = VibeProvider(ProviderConfig())
        with mock.patch('shutil.which', return_value='/usr/bin/vibe'):
            with mock.patch.dict(os.environ, {'MISTRAL_API_KEY': 'test-key'}):
                assert provider.is_available() is True


class TestCopilotCLIProvider:
    """Tests for CopilotCLIProvider."""

    def test_is_available_with_copilot(self) -> None:
        """Test availability when copilot is installed."""
        from pre_commit.ai_fix import CopilotCLIProvider, ProviderConfig

        provider = CopilotCLIProvider(ProviderConfig())
        with mock.patch('shutil.which', side_effect=lambda x: '/usr/bin/copilot' if x == 'copilot' else None):
            assert provider.is_available() is True

    def test_is_available_with_gh(self) -> None:
        """Test availability when gh is installed but not copilot."""
        from pre_commit.ai_fix import CopilotCLIProvider, ProviderConfig

        provider = CopilotCLIProvider(ProviderConfig())
        with mock.patch('shutil.which', side_effect=lambda x: '/usr/bin/gh' if x == 'gh' else None):
            assert provider.is_available() is True


# =============================================================================
# Test Main Function
# =============================================================================


class TestMain:
    """Tests for main entry point."""

    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test --version flag."""
        from pre_commit.ai_fix import main

        with pytest.raises(SystemExit) as exc_info:
            main(['--version'])

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert 'ai-fix' in output

    def test_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test --help flag."""
        from pre_commit.ai_fix import main

        with pytest.raises(SystemExit) as exc_info:
            main(['--help'])

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert 'AI-powered' in output
