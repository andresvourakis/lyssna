"""The package version and the one in pyproject.toml must not drift apart."""

import tomllib
from pathlib import Path

import lyssna


def test_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert lyssna.__version__ == pyproject["project"]["version"]
