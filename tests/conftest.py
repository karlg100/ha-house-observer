"""Load pure integration modules without importing Home Assistant."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "house_observer"


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, COMPONENT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def patterns_module():
    """Return the pure baseline module."""
    return _load_module("house_observer_patterns", "patterns.py")


@pytest.fixture(scope="session")
def models_module():
    """Return the pure storage models module."""
    return _load_module("house_observer_models", "models.py")
