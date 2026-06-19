from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_maker
from app.models import StudentCategory, User, Workspace
from app.security import hash_password

DEFAULT_CATEGORIES = ["Day Scholar", "Hosteller"]


async def seed_default_categories(db: AsyncSession, workspace_id) -> None:
    """Give a new workspace a starter set of student categories.

    These are just defaults — users add or remove categories from Settings.
    """
    for name in DEFAULT_CATEGORIES:
        db.add(StudentCategory(workspace_id=workspace_id, name=name))


async def seed_admin() -> None:
    """Create the default workspace + seeded super-admin if they do not exist."""
    async with async_session_maker() as db:
        existing = (
            await db.execute(
                select(User).where(User.email == settings.seed_admin_email.lower())
            )
        ).scalar_one_or_none()
        if existing:
            return

        ws = Workspace(name="Default Workspace")
        db.add(ws)
        await db.flush()
        await seed_default_categories(db, ws.id)

        admin = User(
            workspace_id=ws.id,
            name="Administrator",
            username=settings.seed_admin_username.lower(),
            email=settings.seed_admin_email.lower(),
            password_hash=hash_password(settings.seed_admin_password),
            auth_provider="local",
            role="superadmin",
        )
        db.add(admin)
        await db.commit()
        print(
            f"[seed] Created admin '{settings.seed_admin_username}' "
            f"({settings.seed_admin_email}) in Default Workspace."
        )
