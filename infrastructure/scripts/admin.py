"""Locally authorize a known registered user as platform operator; requires .env access."""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))


def main():
    from datamind.db import User, engine
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "email", help="Exact email of the registered account whose identity you have verified"
    )
    args = parser.parse_args()
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.email == args.email.lower()))
        if not user:
            raise SystemExit("Account not found. Register first.")
        path = ROOT / ".env"
        text = path.read_text()
        for key, value in {"PLATFORM_ADMIN_EMAIL": user.email, "PLATFORM_ADMIN_USER_ID": user.id}.items():
            text = (
                re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
                if re.search(rf"^{key}=", text, re.MULTILINE)
                else text + f"\n{key}={value}\n"
            )
        path.write_text(text)
    print("Platform identity configured. Restart the API to apply; never share .env.")


if __name__ == "__main__":
    main()
