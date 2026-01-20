"""Test configuration for pytest."""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def change_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Change to temporary directory for each test."""
    original_dir = os.getcwd()
    monkeypatch.chdir(tmp_path)
    yield
    os.chdir(original_dir)
