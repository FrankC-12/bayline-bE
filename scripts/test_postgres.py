"""Run migrations and integration tests against a disposable PostgreSQL container.

Usage: venv/bin/python scripts/test_postgres.py
Docker must be running. The application database and .env are never modified.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    compose = [
        "docker",
        "compose",
        "-p",
        f"bayline-test-{uuid.uuid4().hex[:10]}",
        "-f",
        str(ROOT / "docker-compose.test.yml"),
    ]
    try:
        subprocess.run(
            [*compose, "up", "-d", "--wait", "--wait-timeout", "60"], cwd=ROOT, check=True
        )
        address = subprocess.check_output(
            [*compose, "port", "postgres-test", "5432"], text=True
        ).strip()
        port = int(address.rsplit(":", 1)[1])
        url = f"postgresql+asyncpg://bayline_test:bayline_test@127.0.0.1:{port}/bayline_test"
        env = {
            **os.environ,
            "DATABASE_URL": url,
            "TEST_DATABASE_URL": url,
            "APP_ENV": "test",
            "DEBUG": "false",
            "SECRET_KEY": "isolated-test-key-not-for-application-use",
            "PLATFORM_ADMIN_EMAIL": "tests@example.com",
            "PLATFORM_ADMIN_PASSWORD": "tests-only",
            "PYTHONPATH": str(ROOT),
        }
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
        )
        # Exercise reversibility of the newest migration before inserting test data.
        for target in (("downgrade", "-1"), ("upgrade", "head")):
            subprocess.run(
                [sys.executable, "-m", "alembic", *target], cwd=ROOT, env=env, check=True
            )
        subprocess.run(
            [sys.executable, "-m", "pytest", "tests/integration", "-q", *sys.argv[1:]],
            cwd=ROOT,
            env=env,
            check=True,
        )
    finally:
        subprocess.run([*compose, "down", "--volumes", "--remove-orphans"], cwd=ROOT, check=False)


if __name__ == "__main__":
    main()
