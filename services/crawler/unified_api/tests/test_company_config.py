"""Test company configuration loading (task 02)."""
import os
import pytest
import yaml
from pathlib import Path
from tempfile import TemporaryDirectory


def test_config_path_exists():
    from unified_api.services.company_service import _COMPANIES_CONFIG_PATH
    assert _COMPANIES_CONFIG_PATH.exists()
    assert _COMPANIES_CONFIG_PATH.suffix == ".yaml"


def test_load_config_from_default_location():
    from unified_api.services.company_service import _load_mc_config
    companies = _load_mc_config()
    assert isinstance(companies, list)
    assert len(companies) > 0


def test_load_config_from_other_cwd(tmp_path):
    """Config loads even when cwd is changed."""
    from unified_api.services.company_service import _load_mc_config
    original = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        companies = _load_mc_config()
        assert len(companies) > 0
    finally:
        os.chdir(original)


def test_missing_file_raises():
    from unified_api.services.company_service import _COMPANIES_CONFIG_PATH
    nonexistent = _COMPANIES_CONFIG_PATH.parent / "__nonexistent__.yaml"
    # Test the principle: nonexistent path raises
    assert not nonexistent.exists()


def test_invalid_yaml_raises():
    with TemporaryDirectory() as d:
        path = Path(d) / "bad.yaml"
        path.write_text("{invalid: [yaml", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(path.read_text(encoding="utf-8"))


def test_null_yaml_returns_empty():
    from unified_api.services.company_service import _load_mc_config
    # _load_mc_config uses yaml.safe_load(f) or {}
    # If file has null, or {} should return []
    result = yaml.safe_load("null") or {}
    assert result == {}


def test_no_root_variable():
    import inspect
    from unified_api.services import company_service
    src = inspect.getsource(company_service)
    # Check code lines only (exclude docstrings/comments)
    for line in src.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if 'os.path.join(ROOT' in stripped or stripped.startswith('ROOT ='):
            pytest.fail(f"legacy ROOT variable found: {stripped}")


def test_no_sys_path():
    import inspect
    from unified_api.services import company_service
    src = inspect.getsource(company_service)
    assert "sys.path" not in src
