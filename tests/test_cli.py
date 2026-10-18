import os
import subprocess
import sys

from app.db import PROJECT_ROOT, TABLES, connect_db


def run_cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", *args], cwd=PROJECT_ROOT, env=env, capture_output=True, encoding="utf-8")


def test_cli_init_import_repeat_and_failure(tmp_path, batch, write_batch):
    path = tmp_path / "test_cli.db"
    directory = write_batch(batch)
    assert run_cli("scripts.init_db", "--db", str(path)).returncode == 0
    first = run_cli("scripts.import_data", "--db", str(path), "--input-dir", str(directory))
    assert first.returncode == 0, first.stderr
    assert '"inserted": 3' in first.stdout
    repeated = run_cli("scripts.import_data", "--db", str(path), "--input-dir", str(directory))
    assert repeated.returncode == 0 and '"skipped": 3' in repeated.stdout
    batch["evidences"][0]["source_id"] = "TEST-MISSING"
    write_batch(batch)
    failed = run_cli("scripts.import_data", "--db", str(path), "--input-dir", str(directory))
    assert failed.returncode == 1
    assert "导入失败" in failed.stderr and "evidences.csv" in failed.stderr
    with connect_db(path) as connection:
        assert connection.execute("SELECT source_id FROM evidences WHERE evidence_id='TEST-E1'").fetchone()[0] == "TEST-S1"


def test_environment_database_path_is_honored(tmp_path):
    path = tmp_path / "env_only.db"
    env = dict(os.environ, GUJI_DB_PATH=str(path), PYTHONIOENCODING="utf-8")
    result = run_cli("scripts.init_db", env=env)
    assert result.returncode == 0 and path.exists()
    with connect_db(path) as connection:
        assert all(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0 for table in TABLES)

