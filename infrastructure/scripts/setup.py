"""Generate local secrets without printing them. Safe to run again."""

import secrets
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]


def main():
    path = ROOT / ".env"
    if path.exists():
        print("Existing .env preserved. Add GEMINI_API_KEY locally if it is not configured.")
        return
    text = (ROOT / ".env.example").read_text()
    text = text.replace("SECRET_KEY=generate-with-setup", "SECRET_KEY=" + secrets.token_urlsafe(48))
    text = text.replace(
        "ENCRYPTION_KEY=generate-with-setup", "ENCRYPTION_KEY=" + Fernet.generate_key().decode()
    )
    for key in (
        "POSTGRES_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "DEMO_DB_PASSWORD",
        "MONGO_ROOT_PASSWORD",
        "QDRANT_API_KEY",
    ):
        text = text.replace(f"{key}=\n", f"{key}={secrets.token_urlsafe(32)}\n")
    path.write_text(text, encoding="utf-8")
    for name in ("files", "sources", "models"):
        (ROOT / "data" / name).mkdir(parents=True, exist_ok=True)
    print("Created .env with random local secrets. Set GEMINI_API_KEY in that file; never commit it.")


if __name__ == "__main__":
    main()
