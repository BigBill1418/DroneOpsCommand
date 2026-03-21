"""One-time admin password reset. Run inside the backend container:
   docker compose exec backend python reset_admin.py
"""
import asyncio
import sys

from sqlalchemy import select, text
from app.database import engine, async_session
from app.models.user import User
from app.auth.jwt import hash_password
from app.config import settings


async def main():
    async with async_session() as db:
        # Show all users
        result = await db.execute(select(User))
        users = result.scalars().all()
        print(f"\n  Found {len(users)} user(s) in database:")
        for u in users:
            print(f"    - username={u.username!r}  active={u.is_active}  id={u.id}")

        print(f"\n  Config says: admin_username={settings.admin_username!r}")
        print(f"  Config says: admin_password={settings.admin_password!r}")

        # Reset ALL users' passwords to the configured password
        for u in users:
            old_hash = u.hashed_password[:20] + "..."
            u.hashed_password = hash_password(settings.admin_password)
            new_hash = u.hashed_password[:20] + "..."
            print(f"\n  Reset {u.username!r}: {old_hash} -> {new_hash}")

        await db.commit()
        print(f"\n  Done. Login with: {settings.admin_username} / {settings.admin_password}\n")


if __name__ == "__main__":
    asyncio.run(main())
