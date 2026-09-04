from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT = Path(__file__).parents[1]
CONTRACTS_ROOT = ROOT.parents[1] / "packages" / "contracts"


def test_wheel_contains_every_app_package_and_imports_after_install(tmp_path):
    contracts_wheel_dir = tmp_path / "contracts-wheel"
    api_wheel_dir = tmp_path / "api-wheel"
    venv = tmp_path / "venv"
    outside = tmp_path / "outside-repository"
    outside.mkdir()
    contracts_build = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(contracts_wheel_dir),
        ],
        cwd=CONTRACTS_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert contracts_build.returncode == 0, contracts_build.stderr
    contracts_wheel = next(contracts_wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(contracts_wheel) as archive:
        members = set(archive.namelist())
    expected_contract_packages = {
        f"{path.parent.relative_to(CONTRACTS_ROOT).as_posix()}/__init__.py"
        for path in (CONTRACTS_ROOT / "jobgraph_contracts").rglob("__init__.py")
    }
    assert expected_contract_packages <= members
    assert not any(name == "src" or name.startswith("src/") for name in members)

    api_build = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(api_wheel_dir),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert api_build.returncode == 0, api_build.stderr
    api_wheel = next(api_wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(api_wheel) as archive:
        members = set(archive.namelist())
    expected_app_packages = {
        f"{path.parent.relative_to(ROOT).as_posix()}/__init__.py"
        for path in (ROOT / "app").rglob("__init__.py")
    }
    assert expected_app_packages <= members
    assert not any(name.startswith("jobgraph_contracts/") for name in members)
    assert not any(name == "src" or name.startswith("src/") for name in members)

    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    venv_python = (
        venv / "Scripts" / "python.exe"
        if os.name == "nt"
        else venv / "bin" / "python"
    )
    contracts_installation = subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            str(contracts_wheel),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert contracts_installation.returncode == 0, contracts_installation.stderr
    api_installation = subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            str(api_wheel),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert api_installation.returncode == 0, api_installation.stderr
    environment = os.environ.copy()
    # The import must be resolved solely by the clean interpreter. Repository
    # paths inherited from a developer shell would make a broken wheel pass.
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    subprocess.run(
        [
            str(venv_python),
            "-c",
            (
                "from pathlib import Path; import site, sys; import app; import app.main; "
                "import jobgraph_contracts; "
                "origin=Path(app.__file__).resolve(); "
                "contracts_origin=Path(jobgraph_contracts.__file__).resolve(); "
                "roots=[Path(value).resolve() for value in site.getsitepackages()]; "
                "assert any(origin.is_relative_to(root) for root in roots), "
                "(origin, roots); "
                "assert any(contracts_origin.is_relative_to(root) for root in roots), "
                "(contracts_origin, roots); "
                f"assert Path({str(ROOT)!r}).resolve() not in "
                "[Path(value).resolve() for value in sys.path if value]"
            ),
        ],
        cwd=outside,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
