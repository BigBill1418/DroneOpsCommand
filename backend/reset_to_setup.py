"""Reset authentication — deletes all users so the setup wizard reappears.

Usage:
  docker compose exec backend python reset_to_setup.py

After running, visit the app in your browser to set new credentials.
"""
import asyncio

from sqlalchemy import select, delete
from app.database import async_session
from app.models.user import User


async def main():
    async with async_session() as db:
        result = await db.execute(select(User))
        users = result.scalars().all()
        print(f"\n  Found {len(users)} user(s) in database:")
        for u in users:
            print(f"    - username={u.username!r}  active={u.is_active}  id={u.id}")

        if not users:
            print("\n  No users to remove — setup wizard will appear on next visit.\n")
            return

        await db.execute(delete(User))
        await db.commit()
        print(f"\n  Deleted {len(users)} user(s). Setup wizard will appear on next visit.\n")


if __name__ == "__main__":
    asyncio.run(main())
