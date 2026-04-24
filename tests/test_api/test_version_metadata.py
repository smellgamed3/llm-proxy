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

    # api/app.py 从 pyproject.toml 动态读取，运行时验证一致性
    from api.app import APP_VERSION
    assert APP_VERSION == version, f"APP_VERSION ({APP_VERSION}) != pyproject ({version})"

    # 仪表盘 JS 的版本显示，测试确保同步
    dashboard_js = _read("api/static/app.js")
    readme = _read("README.md")

    assert f"APP_VERSION = 'v{version}'" in dashboard_js
    assert f'当前版本：`{version}`' in readme
    assert f'version = "{version}"' in pyproject
