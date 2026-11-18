"""Load pure integration modules without importing Home Assistant.

Modules are imported through a synthetic package that points at the component
directory, so relative imports work but the integration's __init__.py (which
needs Home Assistant) never runs.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "house_observer"
PURE_PACKAGE = "house_observer_pure"


def _load_module(filename: str):
    """Import a pure component module by file name."""
    if PURE_PACKAGE not in sys.modules:
        package = types.ModuleType(PURE_PACKAGE)
        package.__path__ = [str(COMPONENT)]
        sys.modules[PURE_PACKAGE] = package
    return importlib.import_module(f"{PURE_PACKAGE}.{Path(filename).stem}")


def _fixture(filename: str):
    @pytest.fixture(scope="session")
    def module():
        return _load_module(filename)

    return module


patterns_module = _fixture("patterns.py")
models_module = _fixture("models.py")
discovery_module = _fixture("discovery.py")
reservations_module = _fixture("reservations.py")
semantics_module = _fixture("semantics.py")
guidance_module = _fixture("guidance.py")
knowledge_module = _fixture("knowledge.py")
occupancy_module = _fixture("occupancy.py")
prompts_module = _fixture("prompts.py")
ingest_module = _fixture("ingest.py")
selection_module = _fixture("selection.py")
