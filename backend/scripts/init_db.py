"""Create the RaceSafe database, tables and sample data.

Usage (from the backend folder, with the virtual environment active):
    python scripts\\init_db.py            # create DB + tables + sample data
    python scripts\\init_db.py --reset    # DROP the database first (deletes everything!)
    python scripts\\init_db.py --no-seed  # tables only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import BACKEND_DIR, Settings  # noqa: E402
from app.database.setup import initialize_database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialise the RaceSafe MySQL database")
    parser.add_argument("--reset", action="store_true", help="drop and recreate the database (deletes all data)")
    parser.add_argument("--no-seed", action="store_true", help="do not insert sample students/courses")
    parser.add_argument("--database", help="database name (default: DB_NAME from .env)")
    args = parser.parse_args()

    if not (BACKEND_DIR / ".env").exists():
        print("WARNING: backend/.env not found - using defaults (user root, empty password).")
        print("         Create it with:  Copy-Item .env.example .env   and set DB_PASSWORD.\n")

    overrides = {"db_name": args.database} if args.database else {}
    settings = Settings.from_env(**overrides)
    if args.reset:
        answer = input(f"This will DELETE the database '{settings.db_name}'. Type YES to continue: ")
        if answer.strip() != "YES":
            print("Cancelled.")
            return 1

    print(f"Connecting to MySQL at {settings.db_host}:{settings.db_port} as '{settings.db_user}' ...")
    try:
        version = initialize_database(settings, seed=not args.no_seed, drop_existing=args.reset)
    except Exception as exc:  # show a friendly hint for the most common problems
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        text = str(exc)
        if "1045" in text:
            print("Hint: wrong DB_USER / DB_PASSWORD in backend/.env")
        elif "2003" in text or "Can't connect" in text:
            print("Hint: MySQL is not running. Start it with:  Start-Service MySQL80  (PowerShell as admin)")
        elif "cryptography" in text:
            print("Hint: pip install cryptography (needed for MySQL 8 caching_sha2_password)")
        return 1

    print(f"MySQL version: {version}")
    print(f"Database '{settings.db_name}' is ready" + ("" if args.no_seed else " with 600 students and 8 courses") + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
