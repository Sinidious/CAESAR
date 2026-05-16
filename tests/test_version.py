from __future__ import annotations

import re

import caesar


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", caesar.__version__)
