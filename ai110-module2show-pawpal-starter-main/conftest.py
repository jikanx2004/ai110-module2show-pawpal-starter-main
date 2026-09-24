"""Pytest configuration.

This file exists so that pytest adds the project root to `sys.path`, letting
tests under `tests/` import `pawpal_system` and `main` directly. Without it,
pytest would only put the `tests/` directory on the path and the imports would
fail.
"""
