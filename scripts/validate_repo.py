"""Fast repository checks that do not require a Home Assistant install."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENTS = ROOT / "custom_components"
DOMAIN = "house_observer"
COMPONENT = COMPONENTS / DOMAIN


def fail(message: str) -> None:
    """Exit with a useful validation failure."""
    raise AssertionError(message)


def load_json(path: Path) -> dict:
    """Load a JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def main() -> None:
    """Validate repository shape and synchronized metadata."""
    integration_directories = [path for path in COMPONENTS.iterdir() if path.is_dir()]
    if integration_directories != [COMPONENT]:
        fail("HACS repositories must contain exactly one custom component")

    required_files = (
        ROOT / "README.md",
        ROOT / "hacs.json",
        COMPONENT / "__init__.py",
        COMPONENT / "manifest.json",
        COMPONENT / "config_flow.py",
        COMPONENT / "translations" / "en.json",
    )
    for path in required_files:
        if not path.is_file():
            fail(f"Missing required file: {path.relative_to(ROOT)}")

    manifest = load_json(COMPONENT / "manifest.json")
    required_manifest_keys = {
        "codeowners",
        "documentation",
        "domain",
        "issue_tracker",
        "name",
        "version",
    }
    missing = required_manifest_keys - manifest.keys()
    if missing:
        fail(f"Manifest is missing: {sorted(missing)}")
    if manifest["domain"] != DOMAIN:
        fail("Manifest domain must match the integration directory")
    if not manifest.get("config_flow"):
        fail("House Observer must remain configurable through the UI")

    hacs = load_json(ROOT / "hacs.json")
    if hacs.get("name") != manifest["name"]:
        fail("hacs.json and manifest names differ")

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["version"] != manifest["version"]:
        fail("pyproject and manifest versions differ")

    translations = load_json(COMPONENT / "translations" / "en.json")
    if translations.get("title") != manifest["name"]:
        fail("English translation title and manifest name differ")

    for path in ROOT.rglob("*.json"):
        load_json(path)
    for path in ROOT.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    print("Repository validation passed")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, json.JSONDecodeError, KeyError) as err:
        print(f"Validation failed: {err}", file=sys.stderr)
        raise SystemExit(1) from err
