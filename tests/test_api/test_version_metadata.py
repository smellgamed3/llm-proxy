from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_version_is_consistent_across_public_surfaces() -> None:
    pyproject = _read("pyproject.toml")
    match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    assert match, "pyproject version not found"
    version = match.group(1)

    api_app = _read("api/app.py")
    dashboard_js = _read("api/static/app.js")
    readme = _read("README.md")

    assert f'version="{version}"' in api_app
    assert f"APP_VERSION = 'v{version}'" in dashboard_js
    assert f'当前版本：`{version}`' in readme
    assert f'version = "{version}"' in pyproject
