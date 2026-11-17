from __future__ import annotations

import argparse
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
import sys

from dotenv import load_dotenv
from sqlalchemy import func, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
load_dotenv(PROJECT_ROOT / ".env")

from classsimvr.database import SessionLocal  # noqa: E402
from classsimvr.models import AuthAccount  # noqa: E402
from classsimvr.services.auth import hash_password, normalize_identifier  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset a ClassSimVR account password")
    parser.add_argument("--username", default="teacher1")
    args = parser.parse_args()

    password = getpass("New password (input hidden): ")
    confirmation = getpass("Confirm new password (input hidden): ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    password_hash = hash_password(password)
    username = normalize_identifier(args.username)

    with SessionLocal.begin() as db:
        account = db.scalar(
            select(AuthAccount).where(func.lower(AuthAccount.username) == username)
        )
        if account is None:
            raise SystemExit("Account was not found")
        account.password_hash = password_hash
        account.updated_at = datetime.now(timezone.utc)
        print(f"Password reset for username={account.username}; account remains active={account.is_active}")


if __name__ == "__main__":
    main()
