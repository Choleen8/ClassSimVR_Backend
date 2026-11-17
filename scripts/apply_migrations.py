from __future__ import annotations

from pathlib import Path
import sys

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
load_dotenv(PROJECT_ROOT / ".env")

from classsimvr.database import engine  # noqa: E402


def main() -> None:
    migration_dir = PROJECT_ROOT / "migrations" / "versions"
    migrations = sorted(migration_dir.glob("*.sql"))
    if not migrations:
        raise SystemExit("No SQL migrations found")
    with engine.begin() as connection:
        for migration in migrations:
            connection.exec_driver_sql(migration.read_text(encoding="utf-8"))
            print(f"Applied {migration.name}")


if __name__ == "__main__":
    main()
