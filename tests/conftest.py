"""Test configuration for pytest."""
from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def change_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, Any, None]:
    """Change to temporary directory for each test."""
    original_dir = os.getcwd()
    monkeypatch.chdir(tmp_path)
    yield
    os.chdir(original_dir)
