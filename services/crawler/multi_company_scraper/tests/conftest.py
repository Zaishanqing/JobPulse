"""Pytest configuration — register custom markers."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: marks tests that perform real network requests (deselect with '-m \"not slow\"')",
    )
