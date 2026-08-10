from pathlib import Path
import tomllib


def test_database_drivers_are_optional_extras() -> None:
    project_root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((project_root / "pyproject.toml").read_text())[
        "project"
    ]

    core = "\n".join(project["dependencies"])
    assert "psycopg" not in core
    assert "pymssql" not in core
    assert "oracledb" not in core

    extras = project["optional-dependencies"]
    assert any(requirement.startswith("psycopg") for requirement in extras["postgres"])
    assert any(requirement.startswith("pymssql") for requirement in extras["sqlserver"])
    assert any(requirement.startswith("oracledb") for requirement in extras["oracle"])

    all_databases = "\n".join(extras["databases"])
    assert "psycopg" in all_databases
    assert "pymssql" in all_databases
    assert "oracledb" in all_databases
