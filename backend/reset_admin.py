"""One-time admin password reset. Run inside the backend container:
   docker compose exec backend python reset_admin.py

v2.43.0: This is the ONLY way to reset the admin password.
The seed will never modify an existing user's password.
"""
import asyncio
import sys

from sqlalchemy import select
from app.database import engine, async_session
from app.models.user import User
from app.auth.jwt import hash_password, verify_password
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
        print(f"  Will reset password to ADMIN_PASSWORD from env/.env")

        # Find and reset only the admin user
        result = await db.execute(
            select(User).where(User.username == settings.admin_username)
        )
        admin = result.scalar_one_or_none()
        if not admin:
            print(f"\n  ERROR: No user with username={settings.admin_username!r} found!")
            sys.exit(1)

        new_hash = hash_password(settings.admin_password)
        roundtrip_ok = verify_password(settings.admin_password, new_hash)
        print(f"\n  Bcrypt roundtrip check: {'PASS' if roundtrip_ok else 'FAIL'}")
        if not roundtrip_ok:
            print("  CRITICAL: Bcrypt roundtrip failed — password will NOT work!")
            sys.exit(1)

        old_prefix = admin.hashed_password[:10] if admin.hashed_password else "EMPTY"
        admin.hashed_password = new_hash
        new_prefix = admin.hashed_password[:10]
        print(f"  Reset {admin.username!r}: {old_prefix}... -> {new_prefix}...")

        await db.commit()
        print(f"\n  Done. Login with: {settings.admin_username} / <ADMIN_PASSWORD from env>\n")


if __name__ == "__main__":
    asyncio.run(main())
