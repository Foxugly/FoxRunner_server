from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.db import async_session_maker
from api.models import User


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create or promote a FoxRunner superuser.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password")
    args = parser.parse_args()
    password = args.password or getpass.getpass("Password: ")
    async with async_session_maker() as session:
        existing = await session.scalar(select(User).where(User.email == args.email))
        if existing is not None:
            existing.is_active = True
            existing.is_verified = True
            existing.is_superuser = True
            await session.commit()
            print(f"promoted:{args.email}")
            return 0
        from fastapi_users.password import PasswordHelper

        helper = PasswordHelper()
        session.add(User(email=args.email, hashed_password=helper.hash(password), is_active=True, is_verified=True, is_superuser=True))
        await session.commit()
        print(f"created:{args.email}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
