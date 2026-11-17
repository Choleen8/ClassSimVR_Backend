from __future__ import annotations

import argparse
from getpass import getpass
from pathlib import Path
import sys

from dotenv import load_dotenv
from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
load_dotenv(PROJECT_ROOT / ".env")

from classsimvr.database import SessionLocal  # noqa: E402
from classsimvr.models import AuthAccount, Trainee  # noqa: E402
from classsimvr.services.auth import hash_password, validate_email, validate_username  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a local ClassSimVR test account")
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--full-name", required=True)
    args = parser.parse_args()

    username = validate_username(args.username).casefold()
    email = validate_email(args.email)
    password = getpass("Password (input hidden): ")
    confirmation = getpass("Confirm password (input hidden): ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    password_hash = hash_password(password)

    with SessionLocal.begin() as db:
        if db.scalar(select(AuthAccount).where(
            (AuthAccount.username == username) | (AuthAccount.email == email)
        )) is not None:
            raise SystemExit("An account with that username or email already exists")
        trainee = db.scalar(select(Trainee).where(Trainee.email == email))
        if trainee is None:
            trainee = Trainee(full_name=args.full_name.strip(), email=email)
            db.add(trainee)
            db.flush()
        elif trainee.auth_account is not None:
            raise SystemExit("That trainee already has an authentication account")
        db.add(AuthAccount(
            trainee_id=trainee.trainee_id,
            username=username,
            email=email,
            password_hash=password_hash,
        ))
        print(f"Created test account for trainee_id={trainee.trainee_id}, username={username}")


if __name__ == "__main__":
    main()
