"""Smoke test: the test setup runs and the app package imports."""

import backend.app


def test_app_package_imports() -> None:
    assert backend.app.__doc__
